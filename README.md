# gem5 MCP Server

An [MCP](https://modelcontextprotocol.io) server that lets an LLM drive
[gem5](https://www.gem5.org/) simulations — submit runs, poll them, and read back
structured stats — implemented per `gem5-mcp-server-design.md`.

gem5 has no API: it's a CLI binary (`gem5.opt`) fed a Python config script. This server
is a **subprocess orchestrator** around that, with a job queue so long simulations never
block a tool call, server-side parameter validation so the model never runs raw Python,
and a `stats.txt` parser so results come back as filtered JSON instead of thousands of
lines of flat text.

## Architecture

```
Claude / MCP client
      │  (stdio)
      ▼
MCP server  (Windows Python, this repo)
      │  wsl.exe -d Ubuntu-24.04 -u gem5 -- gem5.opt <template>.py --outdir=/…/{job_id}
      ▼
gem5 (built inside WSL Ubuntu, ISAs: X86, RISCV)
      │  writes m5out/stats.txt
      ▼
Stats parser → filtered JSON     SQLite ← job + checkpoint metadata
```

- gem5 is built inside a **WSL Ubuntu-24.04** distro and run as the non-root `gem5` user.
- Per-job output goes to `/home/gem5/gem5mcp-runs/{job_id}/m5out`, read back from Windows
  via the `\\wsl.localhost\Ubuntu-24.04\…` UNC path.
- Config templates live in `templates/` and are read by gem5 through `/mnt/c/...`.

## Layout

| Path | Purpose |
|---|---|
| `server/config.py` | Distro / paths / limits, Windows↔WSL path helpers |
| `server/main.py` | FastMCP server, the 9 tools |
| `server/jobs.py` | Async bounded worker pool, launch, size/time caps |
| `server/gem5_runner.py` | Builds the `wsl.exe … gem5.opt …` command |
| `server/templates.py` | Validated template registry (pydantic schemas) |
| `server/stats.py` | `stats.txt` → dict + glob/regex filter |
| `server/db.py` | SQLite job + checkpoint store |
| `templates/*.py` | Fixed, trusted gem5 config scripts |
| `data/` | SQLite DB + per-job logs (created at runtime) |

## Tools

| Tool | What it does |
|---|---|
| `list_available_isas()` | Pre-built ISAs (X86, RISCV) |
| `get_gem5_version()` | Pinned gem5 version |
| `list_config_templates()` | Templates + their parameter JSON schema |
| `submit_simulation(template, params)` | Launch a run → `job_id` (non-blocking) |
| `get_job_status(job_id)` | queued / running / done / failed + queue position |
| `get_job_result(job_id)` | Summary stats + pointer to full stats |
| `get_stats(job_id, pattern)` | Filtered stat query (glob like `system.cpu*` or regex) |
| `submit_from_source(source, language, isa, cpu)` | Compile C/C++ source and run it on one ISA |
| `compare_source_across_isas(source, language, isas, cpu)` | Compile once, run on several ISAs, compare |
| `get_comparison(comparison_id)` | Live status + normalized metrics for a comparison |
| `open_dashboard()` | How to open the web dashboard |
| `list_checkpoints()` | Checkpoints produced by finished jobs |
| `restore_from_checkpoint(checkpoint_id)` | New run restored from a checkpoint |

## Web dashboard & multi-architecture comparison

A FastAPI + Chart.js dashboard lets you paste a program, pick ISAs + a CPU model, run it,
and see a **graphical side-by-side comparison** (cycles, instructions, CPI/IPC, cache miss
rates). It shares the same backend as the MCP server, so experiments Claude runs also
appear here.

```powershell
cd C:\Users\NG\Desktop\gem5mcp
.\.venv\Scripts\python.exe -m web.app     # serves http://127.0.0.1:8000
```

The same flow is drivable by Claude, e.g.:
> "write Fibonacci in C and compare it on X86 vs RISCV, then show the CPI"

Claude calls `compare_source_across_isas`, which compiles the source for each ISA
(`gcc` / `riscv64-linux-gnu-gcc`, C and C++), launches a gem5 run per ISA, and returns a
`comparison_id` you poll with `get_comparison`.

## Course mode, sweeps & validation

The dashboard has three tabs — **Compare**, **Course labs**, **Validation** — backed by
these tools:

- **Course labs** (`list_labs` / `get_lab` / `run_lab` / `explain_lab`): guided
  experiments with objective, background, a ready-to-run experiment, and questions. Five
  labs ship: out-of-order vs in-order, L2 cache-size sweep, x86 vs RISC-V, the
  memory-hierarchy ladder, and the IPC validation. `explain_lab` produces an
  "explain this result" prompt from the measured numbers.
- **Parameter / CPU sweeps** (`run_parameter_sweep`, `run_cpu_sweep`): vary one knob
  (e.g. `l2_size` across `128kB…2MB`, or the CPU model across `atomic/timing/o3`) and
  chart the effect.
- **Validation** (`run_validation`): *self-consistency* microbenchmarks, not silicon
  accuracy. `ipc` proves the out-of-order core beats in-order on a benchmark with known
  parallelism; `memory` shows cycles climbing as a pointer chase spills L1→L2→DRAM. Each
  returns a pass/fail verdict.

### Tunable hardware knobs (single-core template)

Beyond ISA / CPU model / core count, `se_single_core` exposes a broad design space, all
validated server-side and settable via `submit_simulation`, `run_parameter_sweep`, or the
dashboard's **Advanced hardware** panel:

- **Caches:** `l1i/l1d/l2_size`, `l1d/l1i/l2_assoc`, `l1d/l1i/l2_repl` (LRU, FIFO, random,
  MRU, NRU, LIP, TreePLRU, RRIP, BRRIP, SecondChance), `prefetcher` (none, stride, tagged,
  AMPM, BOP, DCPT), `l1d_mshrs`, `cache_line_size` (16/32/64).
- **Memory:** `dram_type` (DDR3-1600/2133, DDR4-2400, LPDDR3, HBM, + dual-channel), plus
  `mem_size`, `clock`.
- **Out-of-order pipeline** (CPU = `o3`): `rob_entries`, `fetch/decode/issue/dispatch/
  commit_width`, `lq/sq_entries`, `phys_int/float_regs`.

Any scalar or categorical knob can be swept: e.g.
`run_parameter_sweep(axis="issue_width", values=[1,2,8], cpu="o3")` or
`run_parameter_sweep(axis="prefetcher", values=["none","stride","dcpt"])`.
Defaults reproduce the previous stdlib behaviour exactly. (Branch-predictor swapping is not
exposed — a gem5 param-assignment quirk; the default predictor is used.)

### Honest scope on "realism"
These give **relative, illustrative** results on gem5's generic models — not
silicon-accurate absolute numbers, and *not* a match to any specific real chip. The
validation suite deliberately tests "does the simulator behave as configured", which is
provable, rather than claiming hardware accuracy (which is a research effort).

## Full-system mode (infrastructure)

`list_fs_assets` / `submit_full_system` boot a real OS (kernel + disk image) via a fixed
asset manifest (`server/fullsystem.py`) — no arbitrary images, per design doc 3.6.

**Honest status:** this is working infrastructure, but a boot is heavyweight — each asset
is a multi-GB download (fetched by gem5 on first use) and, since WSL2 doesn't expose KVM
to gem5, a full Linux boot simulates slowly (many minutes to hours). It is wired but has
not been run to a full boot on this machine; treat it as ready-to-try, not instant.

### Example params (`se_single_core`)

```json
{
  "isa": "X86",
  "binary": "/home/gem5/gem5/tests/test-progs/hello/bin/x86/linux/hello",
  "cpu": "timing",
  "l1d_size": "64kB",
  "l2_size": "512kB"
}
```

`binary` is an absolute path **inside WSL** to a statically-linked guest ELF.

## Setup

Prerequisites baked by the installer step: WSL `Ubuntu-24.04` with gem5 built at
`/home/gem5/gem5/build/{X86,RISCV}/gem5.opt`.

```powershell
cd C:\Users\NG\Desktop\gem5mcp
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Run standalone (stdio):

```powershell
.\.venv\Scripts\python.exe -m server.main
```

### Claude Desktop

Add to `%APPDATA%\Claude\claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "gem5": {
      "command": "C:\\Users\\NG\\Desktop\\gem5mcp\\.venv\\Scripts\\python.exe",
      "args": ["-m", "server.main"],
      "cwd": "C:\\Users\\NG\\Desktop\\gem5mcp"
    }
  }
}
```

Restart Claude Desktop; the gem5 tools appear in the tool list.

## Safety controls (from the design doc)

- **No raw Python** — only the fixed `templates/` scripts run; params are pydantic-validated
  and passed as argv, never string-interpolated.
- **Bounded pool** — at most `MAX_CONCURRENT_JOBS` (default 4) run at once; the rest queue.
- **Debug flags** off by default; only a small whitelist is accepted.
- **Caps** — a run is killed if its output exceeds `MAX_OUTPUT_BYTES` (512 MB) or
  `JOB_TIMEOUT_SECONDS` (2 h).
- **Non-root** gem5 user inside WSL.

Overridable via `GEM5_MCP_*` environment variables (see `server/config.py`).

## Scope

v1 is **SE (syscall-emulation) mode** only. Full-system mode (disk images / kernels) is
intentionally deferred behind a future asset manifest, per design doc §3.6.
