"""FastAPI dashboard: paste code, pick ISAs/CPU, run gem5, see a comparison dashboard.

Start with:  python -m web.app     (serves http://127.0.0.1:8000)

Shares the exact same backend (jobs, workloads, comparisons, SQLite) as the MCP server,
so experiments started here or by Claude both show up in the same place.
"""

from __future__ import annotations

import typing
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from server import comparisons, config, course, db, jobs, templates, validation, workloads

STATIC = Path(__file__).parent / "static"

app = FastAPI(title="gem5 comparison dashboard")


@app.on_event("startup")
def _startup() -> None:
    db._connect()


CPU_MODELS = ["atomic", "timing", "o3", "minor"]


class CompareRequest(BaseModel):
    source: str
    language: str = "c"
    isas: list[str]
    cpu: str = "timing"
    title: str = ""
    params: dict[str, Any] = {}   # advanced hardware knobs applied to every run


class BinaryRequest(BaseModel):
    binary: str
    isa: str
    cpu: str = "timing"


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/api/meta")
def meta() -> dict[str, Any]:
    return {
        "isas": config.available_isas(),
        "cpus": CPU_MODELS,
        "languages": workloads.languages(),
        "gem5_version": config.GEM5_VERSION,
        # Option lists for the advanced-hardware UI controls.
        "repl_policies": list(typing.get_args(templates.ReplPolicy)),
        "prefetchers": list(typing.get_args(templates.Prefetcher)),
        "dram_types": list(typing.get_args(templates.DramType)),
        "line_sizes": list(typing.get_args(templates.LineSize)),
        "o3_params": [
            "rob_entries", "fetch_width", "decode_width", "issue_width",
            "dispatch_width", "commit_width", "lq_entries", "sq_entries",
            "phys_int_regs", "phys_float_regs",
        ],
        "sweep_axes": sorted(comparisons.SWEEP_AXES),
    }


@app.post("/api/compare")
async def compare(req: CompareRequest) -> dict[str, Any]:
    if not req.source.strip():
        raise HTTPException(400, "source is empty")
    if not req.isas:
        raise HTTPException(400, "select at least one ISA")
    return await comparisons.create_comparison(
        source=req.source,
        language=req.language,
        isas=req.isas,
        cpu=req.cpu,
        title=req.title or None,
        extra_params=req.params or None,
    )


@app.post("/api/submit_binary")
def submit_binary(req: BinaryRequest) -> dict[str, Any]:
    """Run an existing pre-built WSL binary (the precompiled path)."""
    try:
        return jobs.submit(
            "se_single_core",
            {"isa": req.isa, "binary": req.binary, "cpu": req.cpu},
            label=f"{req.isa} / {req.cpu}",
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"{type(exc).__name__}: {exc}")


class SweepRequest(BaseModel):
    source: str
    language: str = "c"
    isa: str
    axis: str
    values: list[Any]
    cpu: str = "timing"


class CpuSweepRequest(BaseModel):
    source: str
    language: str = "c"
    isa: str
    cpus: list[str]


class ValidationRequest(BaseModel):
    kind: str = "ipc"      # 'ipc' | 'memory'
    isa: str = "X86"
    cpu: str = "timing"


@app.post("/api/sweep")
async def sweep(req: SweepRequest) -> dict[str, Any]:
    try:
        return await comparisons.create_sweep(
            source=req.source, language=req.language, isa=req.isa,
            axis=req.axis, values=req.values, cpu=req.cpu)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"{type(exc).__name__}: {exc}")


@app.post("/api/cpu_sweep")
async def cpu_sweep(req: CpuSweepRequest) -> dict[str, Any]:
    try:
        return await comparisons.create_cpu_sweep(
            source=req.source, language=req.language, isa=req.isa, cpus=req.cpus)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"{type(exc).__name__}: {exc}")


@app.post("/api/validation")
async def run_validation(req: ValidationRequest) -> dict[str, Any]:
    if req.kind == "ipc":
        kwargs = validation.ipc_experiment(req.isa)
    elif req.kind == "memory":
        kwargs = validation.memory_ladder_experiment(req.isa, req.cpu)
    else:
        raise HTTPException(400, "kind must be 'ipc' or 'memory'")
    return await comparisons.run_experiment(**kwargs)


@app.get("/api/labs")
def labs() -> dict[str, Any]:
    return {"labs": course.list_labs()}


@app.get("/api/lab/{lab_id}")
def lab(lab_id: str) -> dict[str, Any]:
    try:
        return course.get_lab(lab_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc))


@app.post("/api/lab/{lab_id}/run")
async def lab_run(lab_id: str) -> dict[str, Any]:
    try:
        return await course.run_lab(lab_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"{type(exc).__name__}: {exc}")


@app.get("/api/comparison/{comparison_id}")
def comparison(comparison_id: str) -> dict[str, Any]:
    try:
        return comparisons.get_comparison(comparison_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc))


@app.get("/api/comparisons")
def recent() -> dict[str, Any]:
    return {"comparisons": db.list_comparisons()[:25]}


@app.get("/api/job/{job_id}")
def job(job_id: str) -> dict[str, Any]:
    try:
        return jobs.status(job_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc))


app.mount("/static", StaticFiles(directory=STATIC), name="static")


def main() -> None:
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="info")


if __name__ == "__main__":
    main()
