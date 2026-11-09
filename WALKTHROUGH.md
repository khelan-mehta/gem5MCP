# End-to-End Walkthrough: from a Claude Desktop prompt to gem5 and back

This document follows **one real request** through every layer of the system, naming
each file and function and explaining what it does. Read it top to bottom and you'll
understand how a sentence typed into Claude becomes a running gem5 simulation and a
chart.

**Example prompt (typed into Claude Desktop):**

> *"Write Fibonacci in C and compare it on X86 vs RISCV with a timing CPU, then show
> the CPI for each."*

---

## The 10,000-foot view

```
Claude Desktop (LLM)
   │  1. decides to call a tool  → JSON-RPC over stdio
   ▼
server/main.py         (FastMCP)         2. tool dispatch
   ▼
server/comparisons.py  (orchestration)   3. one experiment = many runs
   ▼
server/workloads.py    (compiler)        4. C source → per-ISA binary (in WSL)
   ▼
server/jobs.py         (job engine)      5. queue + launch + caps + record
   ▼
server/gem5_runner.py  (command builder) 6. build the wsl.exe … gem5.opt … line
   ▼
gem5.opt  +  templates/se_single_core.py 7. the actual simulation (inside WSL)
   ▼
server/stats.py        (parser)          8. stats.txt → metrics dict
   ▼
server/db.py           (SQLite)          records everything throughout
   ▲
   │  9. Claude polls get_comparison, then explains the numbers
```

Every stage below is one of these numbered steps.

---

## Step 1 — The prompt and the transport (Claude ⇄ server)

When Claude Desktop starts, it reads `claude_desktop_config.json` and launches our server
as a subprocess:

```json
"gem5": {
  "command": "…\\.venv\\Scripts\\python.exe",
  "args": ["-m", "server.main"],
  "env": { "PYTHONPATH": "…\\gem5mcp" }
}
```

The two processes talk **MCP over stdio** — newline-delimited **JSON-RPC** on the
subprocess's stdin/stdout. The LLM never runs Python directly; it emits a structured
"please call tool X with these arguments" message. For our prompt, Claude decides to call:

```
compare_source_across_isas(
    source = "<the C code it wrote>",
    language = "c",
    isas = ["X86", "RISCV"],
    cpu = "timing"
)
```

Claude wrote the C itself; from here on it's our code.

---

## Step 2 — Tool dispatch (`server/main.py`)

`main.py` builds the server and registers every tool with the `@mcp.tool()` decorator.
FastMCP turns each decorated function's **signature + docstring** into the JSON schema
Claude sees, validates the incoming arguments against it, and calls the function.

```python
mcp = FastMCP("gem5")

@mcp.tool()
async def compare_source_across_isas(source, language="c", isas=None, cpu="timing", title=""):
    """Compile one program and run it on several ISAs for a side-by-side comparison…"""
    isas = isas or config.available_isas()          # default = whatever is actually built
    try:
        return await comparisons.create_comparison(
            source=source, language=language, isas=isas, cpu=cpu, title=title or None
        )
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}   # errors come back as readable text
```

**What this layer does:** it's a thin, safe adapter. It fills defaults
(`config.available_isas()` — Step 2a), delegates to the orchestration layer, and converts
any exception into a plain `{"error": …}` string so a bad request never crashes the
conversation. `main.py` holds **22 such tools**; this is just the one our prompt hit.

### Step 2a — `config.available_isas()` (`server/config.py`)

`config.py` is the single source of truth for *where things live* and *how to translate
paths between Windows and WSL*. `available_isas()` returns only the ISAs whose compiled
binary actually exists on disk:

```python
BUILT_ISAS = {                       # ISA → path to its gem5 binary, inside WSL
    "X86":   GEM5_ROOT / "build/X86/gem5.opt",
    "RISCV": GEM5_ROOT / "build/RISCV/gem5.opt",
    "ARM":   GEM5_ROOT / "build/ARM/gem5.opt",
}

def available_isas():
    return [isa for isa, p in BUILT_ISAS.items()
            if Path(wsl_to_unc(p)).exists()]     # check via \\wsl.localhost\… path
```

Two helper functions here are used constantly:

```python
def win_to_wsl(path):   # C:\Users\NG\x  →  /mnt/c/Users/NG/x   (so WSL can read Windows files)
def wsl_to_unc(path):   # /home/gem5/x   →  \\wsl.localhost\Ubuntu-24.04\home\gem5\x
```

`win_to_wsl` lets gem5 (in Linux) read our templates that live on Windows; `wsl_to_unc`
lets our Windows Python read gem5's output files back.

---

## Step 3 — Orchestration: one experiment, many runs (`server/comparisons.py`)

An "experiment" is one program run under several **variants**. `create_comparison` turns
the ISA list into a variant list and hands it to the generic engine:

```python
async def create_comparison(source, language, isas, cpu="timing", title=None, …):
    variants = [
        {"label": f"{isa} / {cpu}", "isa": isa, "cpu": cpu, "extra_params": {}}
        for isa in isas                       # → [{X86/timing}, {RISCV/timing}]
    ]
    return await run_experiment(source=…, variants=variants, kind="isa_comparison", …)
```

`run_experiment` is the heart of the orchestration layer. It records the experiment, then
submits **one job per variant**, tagging them all with a shared `group_id` so they can be
reassembled later:

```python
async def run_experiment(source, language, variants, title, kind, spec_extra=None):
    comparison_id = "cmp_" + uuid…             # the group id
    db.insert_comparison({comparison_id, title, spec=<json of everything>, …})

    submitted, errors = [], []
    for v in variants:
        try:
            info = await submit_from_source(
                source=v.get("source", source), language=…,
                isa=v["isa"], cpu=v.get("cpu"), extra_params=v.get("extra_params"),
                group_id=comparison_id, label=v["label"],
            )
            submitted.append({...})
        except Exception as exc:
            errors.append({"label": v["label"], "error": str(exc)})   # e.g. a compile error
    return {"comparison_id": comparison_id, "submitted": submitted, "errors": errors}
```

**Why it's built this way:** comparisons, CPU sweeps and cache sweeps are *all* just
different variant lists, so they share this one engine. `create_cpu_sweep` and
`create_sweep` (also in this file) build their variant lists differently and call the same
`run_experiment`.

`run_experiment` returns **immediately** with a `comparison_id` — the jobs are still
running. This is the "job pattern": never block the tool call on a slow simulation.

---

## Step 4 — Compile the source (`server/workloads.py`)

`submit_from_source` (in `comparisons.py`) first turns source into a binary, then submits a
job:

```python
async def submit_from_source(source, language, isa, cpu, extra_params, group_id, label):
    binary = await workloads.compile_source(source, language, isa)     # ← Step 4
    params = {"isa": isa, "binary": binary, "cpu": cpu, **(extra_params or {})}
    return jobs.submit("se_single_core", params, group_id=group_id, label=label)  # ← Step 5
```

`workloads.compile_source` compiles the C for that ISA **inside WSL**, using the matching
toolchain, and caches by content hash so identical source isn't recompiled:

```python
_TOOLCHAINS = {
  "c":   (".c",   {"X86":"gcc", "RISCV":"riscv64-linux-gnu-gcc", "ARM":"aarch64-linux-gnu-gcc"}),
  "cpp": (".cpp", {"X86":"g++", "RISCV":"riscv64-linux-gnu-g++", "ARM":"aarch64-linux-gnu-g++"}),
}

async def compile_source(source, language, isa):
    digest = sha1(f"{language}:{isa}:{source}")            # cache key
    out_wsl = WORKLOADS_ROOT_WSL / digest / f"prog_{isa}"
    if Path(wsl_to_unc(out_wsl)).exists(): return str(out_wsl)   # cache hit

    src_win = data/workloads/<digest>/src.c                # write source on Windows side
    compiler = _TOOLCHAINS[language][1][isa]               # pick gcc / cross-gcc
    cmd = f"{compiler} -static -O2 -o {out_wsl} {win_to_wsl(src_win)}"
    proc = await asyncio.create_subprocess_exec(
        "wsl.exe", "-d", DISTRO, "-u", "gem5", "--", "bash", "-lc", cmd, …)
    if proc.returncode != 0: raise CompileError(<compiler stderr>)   # surfaced to Claude
    return str(out_wsl)
```

Key points:
- **`-static`** so the guest binary has no shared-library dependencies (SE mode can't do a
  dynamic loader easily).
- Compilation never *executes* the code — it only produces an ELF. The program only ever
  runs later, sandboxed inside gem5.
- A compile failure becomes a `CompileError`, which `run_experiment` catches and reports as
  a per-variant `error` — Claude sees exactly which ISA failed and why.

For our prompt this runs twice: once with `gcc` (X86), once with `riscv64-linux-gnu-gcc`
(RISCV), producing two static binaries in WSL.

---

## Step 5 — The job engine (`server/jobs.py`)

`jobs.submit` validates the parameters, then schedules the run.

```python
def submit(template, params, group_id=None, label=None):
    validated = templates.validate(template, params)     # ← Step 5a: pydantic validation
    return submit_prepared(
        isa=validated.isa,
        script_wsl=templates.script_wsl_path(template),   # path to se_single_core.py
        script_argv=templates.build_argv(validated, template),  # the --flags for gem5
        debug_flags=validated.debug_flags,
        template=template,
        params_json=db.dumps(validated.model_dump()),
        take_checkpoint=validated.take_checkpoint,
        group_id=group_id, label=label,
    )
```

### Step 5a — Parameter validation (`server/templates.py`)

This is the **security boundary**. The model never sends Python; it sends parameters that
are checked against a strict pydantic schema before anything runs:

```python
class _Base(BaseModel):
    model_config = {"extra": "forbid"}          # unknown fields rejected
    isa: Literal["X86","RISCV","ARM"]
    binary: str                                  # validated: no shell metacharacters
    cpu: Literal["atomic","timing","o3","minor"] = "timing"
    l2_size: str = "256kB"                        # validated against a size regex
    debug_flags: list[str] = []                  # validated against a whitelist
    …
    @field_validator("binary")
    def _safe_path(cls, v):
        if not _PATH_RE.match(v): raise ValueError(f"unsafe path: {v!r}")
        return v
```

`build_argv` then turns the *validated* object into a plain argument list — never string
interpolation into a `.py` file:

```python
def build_argv(validated, template):
    return ["--isa", validated.isa, "--binary", validated.binary,
            "--cpu", validated.cpu, "--l2-size", validated.l2_size, …]
```

`script_wsl_path` returns the WSL path of the fixed template script (read via `/mnt/c`).

### Step 5b — Scheduling (`submit_prepared`)

```python
def submit_prepared(*, isa, script_wsl, script_argv, debug_flags, template,
                    params_json, take_checkpoint, group_id, label):
    job_id = uuid…
    outdir_wsl, outdir_win = gem5_runner.outdir_for(job_id)   # this job's private m5out/
    db.insert_job({job_id, template, isa, params, status:"queued", outdir…, group_id, label})
    _queue_order.append(job_id)
    _tasks[job_id] = asyncio.create_task(                      # run in the background
        _run_prepared(job_id, isa, script_wsl, script_argv, debug_flags, outdir_wsl, …))
    return {"job_id": job_id, "status": "queued", "queue_position": _queue_position(job_id)}
```

The job is recorded as `queued` and an **asyncio task** is created. Control returns up the
stack instantly — this is why `submit_simulation`/`compare_…` respond in milliseconds even
though gem5 takes seconds to minutes.

### Step 5c — The bounded worker + launch (`_run_prepared` → `_launch`)

```python
async def _run_prepared(job_id, isa, script_wsl, script_argv, debug_flags, outdir_wsl, take_ckpt):
    async with _sem():                       # Semaphore(MAX_CONCURRENT_JOBS) → bounded pool
        db.update_job(job_id, status="running", started_at=now)
        argv = gem5_runner.build_wsl_argv(…) # ← Step 6: the real command
        rc, final_tick, err = await _launch(job_id, argv)
        db.update_job(job_id, status="done" if rc==0 else "failed",
                      return_code=rc, final_tick=final_tick, …)
        if rc == 0: _register_checkpoints(job_id, take_ckpt, …)
```

`async with _sem()` is the **bounded pool**: only `MAX_CONCURRENT_JOBS` (default 4) run at
once; extra submissions wait here, so gem5 can't oversubscribe the machine.

`_launch` runs the process and **enforces the safety caps**:

```python
async def _launch(job_id, argv):
    proc = await asyncio.create_subprocess_exec(*argv, stdout=PIPE, stderr=STDOUT)
    async for line in proc.stdout:                     # stream output to a per-job log
        total += len(line)
        if _EXIT_TICK_RE.search(line): final_tick = …  # parse "Exiting @ tick N"
        if total > MAX_OUTPUT_BYTES: raise _CapExceeded # kill runaway trace output (3.9)
    # wrapped in asyncio.wait_for(…, timeout=JOB_TIMEOUT_SECONDS)  → wall-clock cap
```

If a run exceeds the size or time cap, `_terminate` kills it (including the gem5 child in
WSL, matched by the job_id in its outdir path).

---

## Step 6 — Building the WSL command (`server/gem5_runner.py`)

This module assembles the exact command line. There is no gem5 API — we literally spawn a
process:

```python
def build_wsl_argv(*, isa, outdir_wsl, script_wsl, script_argv, debug_flags):
    gem5_bin = config.BUILT_ISAS[isa]                  # …/build/X86/gem5.opt
    gem5_opts = [str(gem5_bin), f"--outdir={outdir_wsl}"]
    if debug_flags: gem5_opts.append(f"--debug-flags={','.join(debug_flags)}")
    inner = [*gem5_opts, script_wsl, *script_argv]     # gem5 opts, then the config script + its args
    posix_cmd = f"mkdir -p {outdir_wsl} && cd {GEM5_ROOT} && " + " ".join(shlex.quote(t) for t in inner)
    return ["wsl.exe", "-d", DISTRO, "-u", "gem5", "--", "bash", "-lc", posix_cmd]
```

For the X86 variant this produces (conceptually):

```
wsl.exe -d Ubuntu-24.04 -u gem5 -- bash -lc
  "mkdir -p /home/gem5/gem5mcp-runs/<job>/m5out &&
   cd /home/gem5/gem5 &&
   build/X86/gem5.opt --outdir=/home/gem5/gem5mcp-runs/<job>/m5out
       /mnt/c/…/templates/se_single_core.py
       --isa X86 --binary /home/gem5/gem5mcp-workloads/<hash>/prog_X86 --cpu timing …"
```

Note the three worlds joined here: the **Windows** `wsl.exe`, the **WSL** gem5 binary and
output dir, and the template script read from Windows via **`/mnt/c`**. Every token is
`shlex.quote`d — defense in depth, even though the parameters were already validated.

---

## Step 7 — The simulation itself (`templates/se_single_core.py`, runs inside gem5)

`gem5.opt` boots its embedded Python and executes our **fixed, trusted** config script.
This is the only "config code," and we wrote it — the model can't change it, only pass the
validated `--flags`.

```python
args = parse_args()                                    # reads --isa, --binary, --cpu, cache sizes…

cache = PrivateL1PrivateL2CacheHierarchy(l1i_size=…, l1d_size=…, l2_size=…)
memory = SingleChannelDDR3_1600(size=args.mem_size)
processor = SimpleProcessor(cpu_type=CPU_MAP[args.cpu],  # "timing" → CPUTypes.TIMING
                            isa=ISA_MAP[args.isa],        # "X86" → ISA.X86
                            num_cores=1)
board = SimpleBoard(clk_freq=args.clock, processor=processor,
                    memory=memory, cache_hierarchy=cache)
board.set_se_binary_workload(BinaryResource(local_path=args.binary))   # the compiled Fibonacci

simulator = Simulator(board=board)
simulator.run()                                        # ← the actual cycle-level simulation
print(f"Exiting @ tick {m5.curTick()} because …")      # ← the line jobs.py parses for final_tick
```

While running, gem5 writes **`m5out/stats.txt`** (thousands of `name value # comment`
lines) into this job's private output directory. When it exits, the process in Step 5c
sees the exit code, records `final_tick`, and marks the job `done`.

This happens **twice in parallel** for our prompt — one X86 process, one RISCV process,
each with its own outdir.

---

## Step 8 — Reading results back (`server/stats.py`)

When Claude asks for results, `comparisons.get_comparison` reads each finished job's
`stats.txt` (through the `\\wsl.localhost\…` path) and extracts comparable metrics:

```python
def parse_stats_file(path):
    # turn "system.cpu.numCycles  264820  # …"  →  {"system.cpu.numCycles": 264820}
    …

def comparison_metrics(stats):
    return {
      "instructions": stats.get("simInsts"),
      "cycles":       _find(stats, suffix="numCycles"),
      "cpi":          _find(stats, suffix=".cpi") or cycles/insts,
      "ipc":          _find(stats, suffix=".ipc"),
      "l1d_miss_rate":_find(stats, contains_all=["l1d","MissRate","total"]),
      "l2_miss_rate": _find(stats, contains_all=["l2","MissRate","total"]),
      …
    }
```

`_find` matches by suffix/token set rather than exact keys, because stat names differ
slightly by ISA/board. This is what makes X86 and RISCV numbers line up in one table.

---

## Step 9 — Assembling the answer (`comparisons.get_comparison`) and back to Claude

Claude polls `get_comparison(comparison_id)` (a tool in `main.py`). It:

```python
def get_comparison(comparison_id):
    meta = db.get_comparison(comparison_id)          # the group record
    rows = db.jobs_in_group(comparison_id)           # both jobs, by shared group_id
    results = []
    for r in rows:
        entry = {"label": r["label"], "isa": r["isa"], "status": r["status"], …}
        if r["status"] == "done":
            parsed = stats.parse_stats_file(<outdir>/stats.txt)
            entry["metrics"] = stats.comparison_metrics(parsed)   # ← Step 8
        results.append(entry)
    overall = "running" | "done" | "failed"          # from the per-job statuses
    out = {"comparison_id", "title", "overall_status", "progress", "results"}
    if spec.kind starts with "validate":             # validation runs also get a verdict
        out["verdict"] = validation.verdict(spec, results)
    return out
```

Claude calls this every couple of seconds. While jobs are `running` it shows progress; once
`overall_status == "done"` it has both metric sets:

```
X86 / timing  : instructions 9,292,376 · cycles 15,181,526 · CPI 1.63
RISCV / timing: instructions 9,369,626 · cycles 13,176,208 · CPI 1.41
```

Claude then writes the natural-language answer the user asked for ("RISC-V executed more
instructions but finished in fewer cycles, so its CPI is lower…"). The **web dashboard**
(`web/app.py`) calls the exact same `get_comparison` over HTTP and draws the bar charts —
same data, two front ends.

---

## The role of `server/db.py` (used throughout)

SQLite is the memory that ties it all together across the async jobs and the two front
ends. Two tables:

- **`jobs`** — one row per gem5 run: status, timings, return code, `final_tick`, its
  output paths, and the `group_id`/`label` that link it to an experiment.
- **`comparisons`** — one row per experiment (the `spec` JSON: kind, source, variants).

Because both the MCP server and the dashboard point at the same DB file, a run started in
one is visible in the other.

---

## Summary: which file owns what

| File | Responsibility |
|---|---|
| `server/main.py` | MCP tools; the API surface Claude sees; error wrapping |
| `server/config.py` | Paths, limits, Windows↔WSL translation, `available_isas()` |
| `server/comparisons.py` | Orchestration: experiments, sweeps, `get_comparison` |
| `server/workloads.py` | Compile source → per-ISA static binary (cached, in WSL) |
| `server/templates.py` | Parameter **validation** + `build_argv` (the safety boundary) |
| `server/jobs.py` | Job queue, bounded pool, launch, size/time caps, records |
| `server/gem5_runner.py` | Build the `wsl.exe … gem5.opt …` command line |
| `templates/*.py` | Fixed, trusted gem5 config scripts (run inside gem5) |
| `server/stats.py` | Parse `stats.txt` → comparable metrics |
| `server/validation.py` | Self-consistency microbenchmarks + pass/fail verdicts |
| `server/course.py` | Guided labs + "explain this result" prompts |
| `server/db.py` | SQLite store shared by everything |
| `web/app.py` + `web/static/*` | The dashboard (same backend over HTTP) |

**The one-sentence version:** Claude calls a tool in `main.py` → `comparisons.py` fans it
out into per-variant jobs → `workloads.py` compiles the code → `jobs.py` queues and runs it
via the command from `gem5_runner.py` → the fixed `templates/se_single_core.py` simulates
inside gem5 → `stats.py` parses the results → `get_comparison` hands them back for Claude to
explain.
