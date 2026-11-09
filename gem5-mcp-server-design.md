# MCP Server for gem5 — Design Document

## 1. Overview

gem5 is a cycle-level computer-architecture simulator (CPU, memory, cache, NoC, full-system) used
in research and education. It has **no native API** — every interaction happens through the command
line, invoking a compiled binary with a Python configuration script as an argument:

```
build/X86/gem5.opt configs/deprecated/example/se.py --cmd=./a.out --cpu-type=O3CPU
```

Wrapping this in an MCP server means giving an LLM tools to: build/select a config, launch a
simulation, monitor it, and read back structured results — while working around the fact that
gem5 was never designed to be called by an automated agent.

This document lists the concrete obstacles you'll hit, the workaround for each, and the tech stack
that fits the constraints.

---

## 2. Root Cause of Most Obstacles

gem5 is a **long-running, CLI-only, filesystem-heavy, arbitrary-Python-script-driven** simulator.
Every obstacle below traces back to one of these four properties, and MCP's simple synchronous
tool-call model doesn't naturally fit any of them.

---

## 3. Obstacles & Workarounds

### 3.1 Simulations run for minutes to hours; MCP tool calls expect fast responses
**Problem:** A single detailed-CPU or full-system run can take 20 minutes to several hours.
Blocking an MCP `tool_use` call for that long will time out the client and freeze the conversation.

**Workaround:** Don't simulate synchronously. Use a **job pattern**:
- `submit_simulation(config, params)` → returns a `job_id` immediately, launches gem5 as a
  detached background process.
- `get_job_status(job_id)` → returns `queued / running / done / failed` plus progress signals
  (e.g. ticks simulated so far, parsed from stdout).
- `get_job_result(job_id)` → returns parsed stats once `done`.

### 3.2 gem5 has no API — only a CLI binary + Python config script
**Problem:** There's nothing to "call" — you're spawning a process and scraping its filesystem output.

**Workaround:** The MCP server is fundamentally a **subprocess orchestrator**, not an API client.
Build tool handlers around `subprocess.Popen`, capture stdout/stderr to log files per run, and treat
gem5's exit code + `m5out/` directory contents as the real response payload.

### 3.3 Config "scripts" are arbitrary executable Python
**Problem:** gem5 config files are full Python programs, not declarative config (YAML/JSON). If you
let the LLM generate and execute arbitrary config scripts, you've effectively given it arbitrary
code execution on your host.

**Workaround:**
- Don't accept raw Python from the model. Ship a small, fixed **library of validated config
  templates** (SE-mode single-core, SE-mode multi-core, simple full-system boot, etc.) with a
  strict parameter schema (ISA, CPU type, cache sizes, binary path) validated server-side before
  substitution.
- Run gem5 itself inside a **sandboxed container** (non-root user, seccomp profile, no network,
  read-only mount of binaries) regardless, as defense in depth.

### 3.4 Per-ISA build is slow and heavy (SCons, 30–90+ min, 5–15 GB)
**Problem:** gem5 must be compiled per target ISA (X86, ARM, RISC-V…). You cannot build this
on-demand inside a tool call.

**Workaround:** Treat building as **infrastructure setup, not a tool call**. Pre-build gem5 binaries
for the ISAs you support into a Docker image ahead of time; the MCP server only ever *invokes*
`gem5.opt`, never builds it. Expose a `list_available_isas()` tool that just reflects what's
pre-built, so the model doesn't ask for something unsupported.

### 3.5 Output is a giant flat-text file, not structured data
**Problem:** Results land in `m5out/stats.txt` — thousands of lines of `name    value    # comment`
in a bespoke text format, plus `config.ini`. There's no JSON output in the general case.

**Workaround:** Write a small dedicated parser that converts `stats.txt` into a `dict`/JSON on read.
Then expose a **filtered query tool** — `get_stats(job_id, pattern="system.cpu0.*")` — rather than
dumping the whole file back to the model. A full stats.txt from a multi-core run can be tens of
thousands of lines; that alone can blow an LLM's context window.

### 3.6 Full-system mode needs large external assets (disk images, kernels, bootloaders)
**Problem:** Full-system simulation needs multi-GB disk images and kernel binaries that can't be
fetched or generated mid-conversation.

**Workaround:** Default all tools to **SE (syscall-emulation) mode**, which only needs a compiled
binary and no disk image. Support full-system mode only against a **pre-staged asset manifest**
(a registry file mapping known image/kernel names → local paths) rather than letting the model
specify arbitrary asset URLs or paths.

### 3.7 Breaking changes between gem5 versions
**Problem:** Config script APIs and SimObject parameters change between gem5 releases; a config
that works on one checkout fails on another.

**Workaround:** Pin the server to one gem5 commit/tag, bake that version into the container image,
and expose a `get_gem5_version()` tool so the model always knows the capability surface it's
targeting. Re-validate all config templates in CI whenever the pinned version is bumped.

### 3.8 Concurrent runs contend for CPU/RAM and clobber each other's output
**Problem:** gem5 is CPU- and memory-heavy; several simulations launched in parallel can starve the
host, and by default they all write to the same `m5out/` directory.

**Workaround:** Give every job its own output directory (`--outdir /data/runs/{job_id}/m5out`), and
run jobs through a **bounded worker pool** (size ≈ physical cores) so new submissions queue instead
of oversubscribing the machine. Surface queue position via `get_job_status`.

### 3.9 Debug tracing can generate gigabytes per run
**Problem:** `--debug-flags=Exec` (and similar) can produce enormous trace files — easily gigabytes
for a short run — which is not something you want an LLM enabling by accident.

**Workaround:** Debug flags off by default; only allow a small whitelisted set, with an automatic
file-size cap that kills the run if exceeded.

### 3.10 Checkpoints are stateful and filesystem-based
**Problem:** "Create checkpoint" / "restore from checkpoint" is common gem5 workflow, but a
checkpoint is just a directory on disk — nothing tracks what it contains or how it was produced.

**Workaround:** Keep a small metadata store (SQLite is enough) mapping checkpoint IDs to their
directory, source job, and simulation parameters. Expose `list_checkpoints()` /
`restore_from_checkpoint(checkpoint_id)` instead of raw paths.

---

## 4. Recommended Tech Stack

| Layer | Choice | Why |
|---|---|---|
| MCP server language | **Python** (official `mcp` SDK) | gem5's own config layer is Python; parsing/subprocess/tooling all stay in one language. |
| Process orchestration | `asyncio` + `subprocess` for simple cases; **Celery or RQ with Redis** if you need persistence across server restarts and a real queue | Simulations outlive a single request; you need a durable job record, not an in-memory future. |
| Sandboxing | **Docker** (or a sibling containers-per-job model), non-root, `--cpus`/`--memory` limits, no network | Arbitrary long-running native binaries plus a code-adjacent config layer means isolation matters. |
| Job/checkpoint metadata | **SQLite** (Postgres if multi-instance) | Lightweight, embeds easily, enough for job status + checkpoint registry. |
| Stats parsing | Custom line-based parser → `dict`/JSON (optionally `pandas` for downstream aggregation) | `stats.txt` has no standard parser; this is bespoke work regardless of stack. |
| Transport | **stdio** for local/Claude Desktop use; **Streamable HTTP/SSE** if the server needs to be reachable remotely or shared across users | Matches how the server will actually be consumed. |
| Build artifacts | Pre-built per-ISA `gem5.opt` binaries baked into the image at build time, never built at request time | Keeps tool calls fast and predictable. |

---

## 5. Suggested MCP Tool Surface

- `list_available_isas()` — which pre-built targets exist
- `list_config_templates()` — the fixed, validated config templates and their parameters
- `submit_simulation(template, params)` → `job_id`
- `get_job_status(job_id)`
- `get_job_result(job_id)` — summary + pointer to full stats
- `get_stats(job_id, pattern)` — filtered stat query
- `list_checkpoints()` / `restore_from_checkpoint(checkpoint_id)`
- `get_gem5_version()`

---

## 6. Minimal Architecture

```
LLM / MCP client
      │  (submit_simulation / get_job_status / get_stats)
      ▼
MCP Server (Python, mcp SDK)
      │
      ├── Job Queue (Celery/RQ + Redis)
      │        │
      │        ▼
      │   Worker Pool (bounded, N ≈ cores)
      │        │  launches, per job:
      │        ▼
      │   Docker container → gem5.opt <template>.py --outdir /data/runs/{job_id}/m5out
      │
      ├── Stats Parser (stats.txt → JSON) ── reads m5out/ on completion
      │
      └── SQLite (job metadata, checkpoint registry)
```

---

## 7. Deployment Notes

- Build the gem5 image(s) once, offline; the MCP server should never invoke SCons.
- Default every simulation to SE mode unless full-system assets are explicitly present in the
  manifest.
- Cap concurrent jobs to physical core count; queue the rest.
- Treat every config template as the only surface for user/LLM-controlled parameters — never
  string-interpolate raw text into a Python config file.
