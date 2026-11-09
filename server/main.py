"""MCP server entry point: exposes the gem5 tool surface (design doc 5) over stdio.

Run with:  python -m server.main
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from . import (
    comparisons, config, course, db, fullsystem, jobs, templates, validation, workloads,
)

mcp = FastMCP("gem5")


@mcp.tool()
def list_available_isas() -> dict[str, Any]:
    """List the pre-built gem5 ISAs this server can simulate (design doc 3.4).

    Only ISAs whose gem5.opt binary exists are returned, so you never request an
    unsupported target.
    """
    return {"isas": config.available_isas(), "mode": "SE (syscall-emulation)"}


@mcp.tool()
def get_gem5_version() -> dict[str, Any]:
    """Return the pinned gem5 version baked into this server (design doc 3.7)."""
    return {"version": config.GEM5_VERSION, "distro": config.WSL_DISTRO}


@mcp.tool()
def list_config_templates() -> dict[str, Any]:
    """List the fixed, validated config templates and their parameter schemas.

    You cannot send raw gem5 Python; pick a template and supply parameters that match
    its schema. Parameters are validated server-side before any simulation runs.
    """
    return {"templates": templates.describe_templates()}


@mcp.tool()
def submit_simulation(template: str, params: dict[str, Any]) -> dict[str, Any]:
    """Launch a gem5 simulation and return a job_id immediately (non-blocking).

    Args:
        template: one of the names from list_config_templates (e.g. 'se_single_core').
        params: parameters matching that template's schema. Required: 'isa' and
            'binary' (an absolute WSL path to a guest ELF). See the template schema
            for optional fields (cpu, cache sizes, mem_size, clock, max_insts,
            debug_flags, take_checkpoint).

    Returns a job record with the job_id; poll it with get_job_status.
    """
    try:
        return jobs.submit(template, params)
    except Exception as exc:  # surface validation errors as readable text
        return {"error": f"{type(exc).__name__}: {exc}"}


@mcp.tool()
def get_job_status(job_id: str) -> dict[str, Any]:
    """Get a job's status: queued / running / done / failed (+ queue position).

    Once status is 'done', call get_job_result or get_stats.
    """
    try:
        return jobs.status(job_id)
    except KeyError as exc:
        return {"error": str(exc)}


@mcp.tool()
def get_job_result(job_id: str) -> dict[str, Any]:
    """Get a finished job's summary stats plus a pointer to the full stats file.

    Returns a small headline summary (cycles, instructions, sim time). Use get_stats
    for detailed, filtered queries rather than pulling the whole file.
    """
    try:
        return jobs.result(job_id)
    except KeyError as exc:
        return {"error": str(exc)}


@mcp.tool()
def get_stats(job_id: str, pattern: str = "*") -> dict[str, Any]:
    """Query a finished job's stats, filtered by a glob or regex (design doc 3.5).

    Args:
        job_id: the job to read.
        pattern: glob like 'system.cpu*' or a regex. Defaults to '*' (all). A full
            multi-core stats.txt can be tens of thousands of lines, so always filter.

    The result is capped to 400 keys to protect the context window.
    """
    try:
        return jobs.query_stats(job_id, pattern)
    except KeyError as exc:
        return {"error": str(exc)}


@mcp.tool()
async def submit_from_source(
    source: str, language: str, isa: str, cpu: str = "timing"
) -> dict[str, Any]:
    """Compile source code and run it on one ISA in gem5 (SE mode). Returns a job_id.

    Great for "write a program and simulate it" without needing a pre-built binary.

    Args:
        source: the full program source (C or C++).
        language: 'c' or 'cpp'.
        isa: 'X86' or 'RISCV'.
        cpu: 'atomic' / 'timing' / 'o3' / 'minor' (default 'timing').
    """
    try:
        return await comparisons.submit_from_source(
            source=source, language=language, isa=isa, cpu=cpu
        )
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {exc}"}


@mcp.tool()
async def compare_source_across_isas(
    source: str,
    language: str = "c",
    isas: list[str] | None = None,
    cpu: str = "timing",
    title: str = "",
) -> dict[str, Any]:
    """Compile one program and run it on several ISAs for a side-by-side comparison.

    This is the automated multi-architecture experiment: e.g. give it Fibonacci C code
    and isas ['X86','RISCV'] and it compiles + launches a gem5 run for each. Returns a
    comparison_id; poll it with get_comparison to get normalized metrics (instructions,
    cycles, CPI/IPC, sim time, cache miss rates) for charting.

    Args:
        source: the program source (C or C++).
        language: 'c' or 'cpp' (default 'c').
        isas: list of ISAs, e.g. ['X86', 'RISCV']. Defaults to all built ISAs.
        cpu: CPU model to hold constant across ISAs (default 'timing').
        title: optional human title for the comparison.
    """
    isas = isas or config.available_isas()
    try:
        return await comparisons.create_comparison(
            source=source, language=language, isas=isas, cpu=cpu, title=title or None
        )
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {exc}"}


@mcp.tool()
def get_comparison(comparison_id: str) -> dict[str, Any]:
    """Get live status + normalized metrics for a multi-architecture comparison.

    Returns overall progress plus, for each finished run, a comparable metric set
    (instructions, cycles, CPI, IPC, sim time, cache miss rates).
    """
    try:
        return comparisons.get_comparison(comparison_id)
    except KeyError as exc:
        return {"error": str(exc)}


@mcp.tool()
async def run_cpu_sweep(
    source: str, isa: str, cpus: list[str] | None = None, language: str = "c"
) -> dict[str, Any]:
    """Run one program on one ISA across several CPU models (in-order vs out-of-order).

    Great for showing the out-of-order speedup. Poll the returned comparison_id with
    get_comparison. `cpus` defaults to ['atomic','timing','o3'].
    """
    cpus = cpus or ["atomic", "timing", "o3"]
    try:
        return await comparisons.create_cpu_sweep(
            source=source, language=language, isa=isa, cpus=cpus
        )
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {exc}"}


@mcp.tool()
async def run_parameter_sweep(
    source: str,
    isa: str,
    axis: str,
    values: list[Any],
    cpu: str = "timing",
    language: str = "c",
    base_params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Sweep one hardware parameter across values and compare (e.g. cache-size study).

    Args:
        axis: one of l1i_size, l1d_size, l2_size, l1d_assoc, l1i_assoc, l2_assoc,
            clock, mem_size, max_insts.
        values: e.g. ['128kB','256kB','512kB','1MB'] for l2_size, or [1,2,8] for
            l1d_assoc (associativity in ways; 1 = direct-mapped).
        base_params: fixed params held across all runs, e.g. {"l1d_size":"16kB"} while
            sweeping l1d_assoc.
    Poll the returned comparison_id with get_comparison to chart the effect.
    """
    try:
        return await comparisons.create_sweep(
            source=source, language=language, isa=isa, axis=axis, values=values,
            cpu=cpu, base_params=base_params,
        )
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {exc}"}


@mcp.tool()
async def run_validation(kind: str = "ipc", isa: str = "X86", cpu: str = "timing") -> dict[str, Any]:
    """Run a self-consistency validation microbenchmark (honest, not silicon-accurate).

    kind='ipc'    -> in-order vs out-of-order IPC (OoO must win).
    kind='memory' -> L1/L2/DRAM latency ladder (cycles must climb).
    Poll the returned comparison_id with get_comparison; it includes a pass/fail verdict.
    """
    try:
        if kind == "ipc":
            kwargs = validation.ipc_experiment(isa)
        elif kind == "memory":
            kwargs = validation.memory_ladder_experiment(isa, cpu)
        else:
            return {"error": "kind must be 'ipc' or 'memory'"}
        return await comparisons.run_experiment(**kwargs)
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {exc}"}


@mcp.tool()
def list_labs() -> dict[str, Any]:
    """List the guided computer-architecture labs (course mode)."""
    return {"labs": course.list_labs()}


@mcp.tool()
def get_lab(lab_id: str) -> dict[str, Any]:
    """Get a lab's objective, background, experiment, what-to-look-for and questions."""
    try:
        return course.get_lab(lab_id)
    except KeyError as exc:
        return {"error": str(exc)}


@mcp.tool()
async def run_lab(lab_id: str) -> dict[str, Any]:
    """Launch a lab's experiment. Returns a comparison_id to poll with get_comparison."""
    try:
        return await course.run_lab(lab_id)
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {exc}"}


@mcp.tool()
def explain_lab(lab_id: str, comparison_id: str) -> dict[str, Any]:
    """Get an 'explain this result' prompt for a finished lab, ready to reason over."""
    try:
        comp = comparisons.get_comparison(comparison_id)
        return {"prompt": course.explain_prompt(lab_id, comp), "results": comp}
    except KeyError as exc:
        return {"error": str(exc)}


@mcp.tool()
def open_dashboard() -> dict[str, Any]:
    """Tell the user how to open the web comparison dashboard."""
    return {
        "url": "http://127.0.0.1:8000",
        "how_to_start": "Run:  python -m web.app   (from the gem5mcp folder)",
        "note": "The dashboard lets you paste code, pick ISAs/CPU, run, and see charts.",
    }


@mcp.tool()
def list_fs_assets() -> dict[str, Any]:
    """List pre-approved full-system boot assets (kernel + disk images).

    Full-system mode boots a real OS. Assets are fixed (no arbitrary images). Note: the
    first use of an asset downloads several GB, and a boot without KVM is slow.
    """
    return {"assets": fullsystem.list_assets()}


@mcp.tool()
def submit_full_system(asset_name: str, max_ticks: int = 0) -> dict[str, Any]:
    """Boot a pre-approved full-system asset. Returns a job_id (poll get_job_status).

    Expect a large one-time download and a long run. Set max_ticks > 0 to stop early
    (e.g. boot for a fixed simulated time instead of to completion).
    """
    try:
        return fullsystem.submit_full_system(asset_name, max_ticks or None)
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {exc}"}


@mcp.tool()
def list_checkpoints() -> dict[str, Any]:
    """List checkpoints created by finished jobs (design doc 3.10)."""
    rows = db.list_checkpoints()
    return {
        "checkpoints": [
            {
                "checkpoint_id": r["checkpoint_id"],
                "job_id": r["job_id"],
                "tick": r["tick"],
                "created_at": r["created_at"],
            }
            for r in rows
        ]
    }


@mcp.tool()
def restore_from_checkpoint(checkpoint_id: str) -> dict[str, Any]:
    """Start a new simulation that restores from a saved checkpoint (design doc 3.10).

    Re-uses the parameters of the job that produced the checkpoint, pointing gem5 at
    the checkpoint directory. Returns a new job_id.
    """
    cp = db.get_checkpoint(checkpoint_id)
    if not cp:
        return {"error": f"unknown checkpoint_id {checkpoint_id!r}"}
    src_job = db.get_job(cp["job_id"])
    if not src_job:
        return {"error": f"source job {cp['job_id']!r} missing"}
    import json

    params = json.loads(cp["params"])
    params["restore_checkpoint_dir"] = cp["dir_wsl"]
    params.pop("take_checkpoint", None)
    try:
        return jobs.submit(src_job["template"], params)
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {exc}"}


def main() -> None:
    db._connect()  # ensure schema exists before serving
    mcp.run()


if __name__ == "__main__":
    main()
