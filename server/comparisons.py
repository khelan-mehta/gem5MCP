"""Multi-architecture comparison: compile one program, run it on several ISAs, collect.

This is the automation layer behind "generate Fibonacci and compare across
architectures". It compiles the same source for each requested ISA, submits a gem5 job
per ISA (tagged with a shared group_id), and later assembles their stats into one
comparable structure for the UI or the model.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from . import db, jobs, stats, workloads


async def submit_from_source(
    *,
    source: str,
    language: str,
    isa: str,
    cpu: str = "timing",
    extra_params: dict[str, Any] | None = None,
    group_id: str | None = None,
    label: str | None = None,
) -> dict[str, Any]:
    """Compile `source` for `isa` and submit a single-core SE job. Returns job info."""
    binary = await workloads.compile_source(source, language, isa)
    params = {"isa": isa, "binary": binary, "cpu": cpu, **(extra_params or {})}
    return jobs.submit("se_single_core", params, group_id=group_id, label=label)


# A single point in an experiment: one gem5 run with a label.
#   {"label": str, "isa": str, "cpu": str, "extra_params": dict}
Variant = dict[str, Any]

# Parameters a sweep is allowed to vary (each validated by the template schema).
SWEEP_AXES = {
    # capacity / timing
    "l1i_size", "l1d_size", "l2_size", "clock", "mem_size", "max_insts",
    # cache organisation
    "l1d_assoc", "l1i_assoc", "l2_assoc", "l1d_repl", "l1i_repl", "l2_repl",
    "prefetcher", "l1d_mshrs", "cache_line_size",
    # memory
    "dram_type",
    # out-of-order pipeline
    "rob_entries", "fetch_width", "decode_width", "issue_width", "dispatch_width",
    "commit_width", "lq_entries", "sq_entries", "phys_int_regs", "phys_float_regs",
}


async def run_experiment(
    *,
    source: str,
    language: str,
    variants: list[Variant],
    title: str,
    kind: str,
    spec_extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Generic engine: run one source across a list of labeled variants in one group.

    Comparisons, CPU sweeps and cache sweeps are all just different variant lists.
    """
    if not variants:
        raise ValueError("no variants to run")
    comparison_id = "cmp_" + uuid.uuid4().hex[:10]
    spec = {
        "kind": kind,
        "language": language,
        "source": source,
        "variants": variants,
        **(spec_extra or {}),
    }
    db.insert_comparison(
        {
            "comparison_id": comparison_id,
            "title": title,
            "spec": db.dumps(spec),
            "created_at": db.now(),
        }
    )

    submitted, errors = [], []
    for v in variants:
        try:
            info = await submit_from_source(
                source=v.get("source", source),         # per-variant source override
                language=v.get("language", language),
                isa=v["isa"],
                cpu=v.get("cpu", "timing"),
                extra_params=v.get("extra_params"),
                group_id=comparison_id,
                label=v["label"],
            )
            submitted.append({"label": v["label"], **info})
        except Exception as exc:  # compile failure etc. -> report per-variant
            errors.append({"label": v["label"], "error": str(exc)})

    return {
        "comparison_id": comparison_id,
        "title": title,
        "submitted": submitted,
        "errors": errors,
    }


async def create_comparison(
    *,
    source: str,
    language: str,
    isas: list[str],
    cpu: str = "timing",
    title: str | None = None,
    extra_params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compile+run `source` on each ISA under one comparison group."""
    if not isas:
        raise ValueError("at least one ISA is required")
    variants = [
        {"label": f"{isa} / {cpu}", "isa": isa, "cpu": cpu,
         "extra_params": extra_params or {}}
        for isa in isas
    ]
    return await run_experiment(
        source=source, language=language, variants=variants, kind="isa_comparison",
        title=title or f"{language.upper()} across {', '.join(isas)}",
    )


async def create_cpu_sweep(
    *,
    source: str,
    language: str,
    isa: str,
    cpus: list[str],
    title: str | None = None,
) -> dict[str, Any]:
    """Run the same program+ISA across several CPU models (atomic/timing/o3/minor)."""
    if not cpus:
        raise ValueError("at least one CPU model is required")
    variants = [
        {"label": f"{cpu}", "isa": isa, "cpu": cpu, "extra_params": {}}
        for cpu in cpus
    ]
    return await run_experiment(
        source=source, language=language, variants=variants, kind="cpu_sweep",
        title=title or f"{isa}: CPU models {', '.join(cpus)}",
    )


async def create_sweep(
    *,
    source: str,
    language: str,
    isa: str,
    axis: str,
    values: list[Any],
    cpu: str = "timing",
    title: str | None = None,
    base_params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Sweep one parameter (e.g. l2_size) across `values`, holding everything else.

    `base_params` are fixed params applied to *every* variant (e.g. hold l1d_size='16kB'
    while sweeping l1d_assoc). They must also be valid template params.
    """
    if axis not in SWEEP_AXES:
        raise ValueError(f"cannot sweep {axis!r}; allowed: {sorted(SWEEP_AXES)}")
    if not values:
        raise ValueError("no values to sweep")
    base = dict(base_params or {})
    if axis in base:
        raise ValueError(f"axis {axis!r} cannot also be a fixed base_param")
    variants = [
        {"label": f"{axis}={v}", "isa": isa, "cpu": cpu,
         "extra_params": {**base, axis: v}}
        for v in values
    ]
    return await run_experiment(
        source=source, language=language, variants=variants, kind="param_sweep",
        title=title or f"{isa}/{cpu}: {axis} sweep",
        spec_extra={"axis": axis, "values": values, "base_params": base},
    )


def get_comparison(comparison_id: str) -> dict[str, Any]:
    """Assemble live status + normalized metrics for every job in a comparison."""
    meta = db.get_comparison(comparison_id)
    if not meta:
        raise KeyError(f"unknown comparison_id {comparison_id!r}")
    rows = db.jobs_in_group(comparison_id)

    results = []
    for r in rows:
        entry = {
            "job_id": r["job_id"],
            "isa": r["isa"],
            "label": r["label"],
            "status": r["status"],
            "error": r["error"],
        }
        if r["status"] == "done":
            stats_win = str(Path(r["outdir_win"]) / "stats.txt")
            parsed = stats.parse_stats_file(stats_win)
            entry["metrics"] = stats.comparison_metrics(parsed)
        results.append(entry)

    done = sum(1 for r in rows if r["status"] == "done")
    failed = sum(1 for r in rows if r["status"] == "failed")
    total = len(rows)
    overall = (
        "done" if done + failed == total and total > 0
        else "failed" if failed == total and total > 0
        else "running"
    )
    import json

    spec = json.loads(meta["spec"])
    out = {
        "comparison_id": comparison_id,
        "title": meta["title"],
        "spec": spec,
        "kind": spec.get("kind", "isa_comparison"),
        "overall_status": overall,
        "progress": {"done": done, "failed": failed, "total": total},
        "results": results,
    }
    if str(spec.get("kind", "")).startswith("validate"):
        from . import validation
        out["verdict"] = validation.verdict(spec, results)
    return out
