"""Async job manager: the 'job pattern' from design doc 3.1.

Simulations run for minutes to hours, so we never block an MCP tool call on one.
``submit`` returns a ``job_id`` immediately and schedules an asyncio task; the task
queues behind a bounded semaphore (design doc 3.8), launches gem5, enforces size /
time caps (3.9), then parses ``stats.txt`` and registers any checkpoints (3.10).
"""

from __future__ import annotations

import asyncio
import os
import re
import uuid
from pathlib import Path
from typing import Any

from . import config, db, gem5_runner, stats, templates

_EXIT_TICK_RE = re.compile(r"Exiting @ tick (\d+)")

# Bounded worker pool + submission order tracking, created lazily on the running loop.
_semaphore: asyncio.Semaphore | None = None
_tasks: dict[str, asyncio.Task] = {}
_queue_order: list[str] = []  # job_ids in submission order, still queued/running


def _sem() -> asyncio.Semaphore:
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(config.MAX_CONCURRENT_JOBS)
    return _semaphore


def _log_path(job_id: str) -> str:
    return str(Path(config.DB_PATH).parent / "runs" / f"{job_id}.log")


def submit(
    template: str,
    params: dict[str, Any],
    group_id: str | None = None,
    label: str | None = None,
) -> dict[str, Any]:
    """Validate params, create a queued job record and schedule it. Returns job info."""
    validated = templates.validate(template, params)  # raises on bad input
    return submit_prepared(
        isa=validated.isa,
        script_wsl=templates.script_wsl_path(template),
        script_argv=templates.build_argv(validated, template),
        debug_flags=validated.debug_flags,
        template=template,
        params_json=db.dumps(validated.model_dump()),
        take_checkpoint=getattr(validated, "take_checkpoint", False),
        group_id=group_id,
        label=label,
    )


def submit_prepared(
    *,
    isa: str,
    script_wsl: str,
    script_argv: list[str],
    debug_flags: list[str],
    template: str,
    params_json: str,
    take_checkpoint: bool = False,
    group_id: str | None = None,
    label: str | None = None,
) -> dict[str, Any]:
    """Schedule a job from an already-resolved script + argv (used by SE and FS paths)."""
    job_id = uuid.uuid4().hex[:12]
    outdir_wsl, outdir_win = gem5_runner.outdir_for(job_id)
    db.insert_job(
        {
            "job_id": job_id,
            "template": template,
            "isa": isa,
            "params": params_json,
            "status": "queued",
            "created_at": db.now(),
            "outdir_wsl": outdir_wsl,
            "outdir_win": outdir_win,
            "group_id": group_id,
            "label": label,
        }
    )
    _queue_order.append(job_id)
    _tasks[job_id] = asyncio.create_task(
        _run_prepared(job_id, isa, script_wsl, script_argv, debug_flags,
                      outdir_wsl, take_checkpoint)
    )
    return {
        "job_id": job_id,
        "status": "queued",
        "template": template,
        "isa": isa,
        "queue_position": _queue_position(job_id),
    }


def _queue_position(job_id: str) -> int:
    """0 == currently running/next; N == N jobs ahead still queued."""
    ahead = 0
    for jid in _queue_order:
        if jid == job_id:
            break
        row = db.get_job(jid)
        if row and row["status"] in ("queued", "running"):
            ahead += 1
    return ahead


async def _run_prepared(
    job_id: str,
    isa: str,
    script_wsl: str,
    script_argv: list[str],
    debug_flags: list[str],
    outdir_wsl: str,
    take_checkpoint: bool,
) -> None:
    async with _sem():
        db.update_job(job_id, status="running", started_at=db.now())
        argv = gem5_runner.build_wsl_argv(
            isa=isa,
            outdir_wsl=outdir_wsl,
            script_wsl=script_wsl,
            script_argv=script_argv,
            debug_flags=debug_flags,
        )
        try:
            rc, final_tick, err = await _launch(job_id, argv)
        except Exception as exc:  # noqa: BLE001 - surface any launch failure to the model
            db.update_job(
                job_id, status="failed", finished_at=db.now(), error=str(exc)
            )
            _forget(job_id)
            return

        status = "done" if rc == 0 else "failed"
        db.update_job(
            job_id,
            status=status,
            finished_at=db.now(),
            return_code=rc,
            final_tick=final_tick,
            error=err,
        )
        if status == "done":
            row = db.get_job(job_id)
            _register_checkpoints(job_id, take_checkpoint, row["params"], outdir_wsl)
    _forget(job_id)


def _forget(job_id: str) -> None:
    if job_id in _queue_order:
        _queue_order.remove(job_id)
    _tasks.pop(job_id, None)


async def _launch(job_id: str, argv: list[str]) -> tuple[int, int | None, str | None]:
    """Run gem5, stream output to a log, enforce size (3.9) and time caps."""
    os.makedirs(Path(_log_path(job_id)).parent, exist_ok=True)
    proc = await asyncio.create_subprocess_exec(
        *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
    )

    total = 0
    final_tick: int | None = None
    killed_reason: str | None = None

    async def pump() -> None:
        nonlocal total, final_tick
        with open(_log_path(job_id), "wb") as fh:
            assert proc.stdout is not None
            async for line in proc.stdout:
                total += len(line)
                fh.write(line)
                m = _EXIT_TICK_RE.search(line.decode("utf-8", "replace"))
                if m:
                    final_tick = int(m.group(1))
                if total > config.MAX_OUTPUT_BYTES:
                    raise _CapExceeded("output size cap exceeded")

    try:
        await asyncio.wait_for(pump(), timeout=config.JOB_TIMEOUT_SECONDS)
        rc = await proc.wait()
    except (_CapExceeded, asyncio.TimeoutError) as exc:
        killed_reason = (
            "killed: wall-clock timeout"
            if isinstance(exc, asyncio.TimeoutError)
            else f"killed: {exc}"
        )
        await _terminate(proc, job_id)
        rc = proc.returncode if proc.returncode is not None else -1

    return rc, final_tick, killed_reason


class _CapExceeded(Exception):
    pass


async def _terminate(proc: asyncio.subprocess.Process, job_id: str) -> None:
    """Kill the relay and, since wsl.exe is only a proxy, the gem5 child by outdir tag."""
    try:
        proc.terminate()
    except ProcessLookupError:
        pass
    # The per-job outdir name contains the job_id, so this targets only this run.
    try:
        killer = await asyncio.create_subprocess_exec(
            "wsl.exe", "-d", config.WSL_DISTRO, "-u", config.WSL_USER, "--",
            "pkill", "-f", job_id,
        )
        await killer.wait()
    except Exception:  # noqa: BLE001 - best effort
        pass


def _register_checkpoints(
    job_id: str, take_checkpoint: bool, params_json: str, outdir_wsl: str
) -> None:
    """Scan the finished job's outdir for gem5 checkpoint dirs and record them (3.10)."""
    if not take_checkpoint:
        return
    outdir_win = config.wsl_to_unc(outdir_wsl)
    try:
        entries = list(Path(outdir_win).iterdir())
    except OSError:
        return
    for entry in entries:
        if entry.is_dir() and entry.name.startswith("cpt."):
            tick_match = re.search(r"cpt\.(\d+)", entry.name)
            cp_id = f"{job_id}-{entry.name}"
            db.insert_checkpoint(
                {
                    "checkpoint_id": cp_id,
                    "job_id": job_id,
                    "dir_wsl": str(gem5_runner.PurePosixPath(outdir_wsl) / entry.name),
                    "dir_win": str(entry),
                    "tick": int(tick_match.group(1)) if tick_match else None,
                    "params": params_json,
                    "created_at": db.now(),
                }
            )


# ---------------------------------------------------------------------------
# Read-side helpers used by the MCP tools
# ---------------------------------------------------------------------------

def status(job_id: str) -> dict[str, Any]:
    row = db.get_job(job_id)
    if not row:
        raise KeyError(f"unknown job_id {job_id!r}")
    out = {
        "job_id": job_id,
        "status": row["status"],
        "template": row["template"],
        "isa": row["isa"],
        "return_code": row["return_code"],
        "final_tick": row["final_tick"],
        "error": row["error"],
    }
    if row["status"] in ("queued", "running"):
        out["queue_position"] = _queue_position(job_id)
    return out


def result(job_id: str) -> dict[str, Any]:
    row = db.get_job(job_id)
    if not row:
        raise KeyError(f"unknown job_id {job_id!r}")
    if row["status"] != "done":
        return {"job_id": job_id, "status": row["status"], "error": row["error"]}
    stats_win = str(Path(row["outdir_win"]) / "stats.txt")
    parsed = stats.parse_stats_file(stats_win)
    return {
        "job_id": job_id,
        "status": "done",
        "final_tick": row["final_tick"],
        "summary": stats.summarize(parsed),
        "num_stats": len(parsed),
        "stats_path": stats_win,
        "outdir": row["outdir_win"],
        "hint": "Use get_stats(job_id, pattern) to query the full stat set.",
    }


def query_stats(job_id: str, pattern: str | None) -> dict[str, Any]:
    row = db.get_job(job_id)
    if not row:
        raise KeyError(f"unknown job_id {job_id!r}")
    stats_win = str(Path(row["outdir_win"]) / "stats.txt")
    parsed = stats.parse_stats_file(stats_win)
    filtered = stats.filter_stats(parsed, pattern)
    # Guard the context window: cap the number of returned keys.
    MAX_KEYS = 400
    truncated = len(filtered) > MAX_KEYS
    if truncated:
        filtered = dict(list(filtered.items())[:MAX_KEYS])
    return {
        "job_id": job_id,
        "pattern": pattern,
        "count": len(filtered),
        "truncated": truncated,
        "stats": filtered,
    }
