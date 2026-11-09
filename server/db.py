"""Tiny SQLite metadata store for jobs and checkpoints (design doc 3.10, stack table).

Kept deliberately small: a couple of tables, plain ``sqlite3`` (no ORM). All access
goes through a module-level connection guarded by a lock, which is plenty for a
single-process stdio MCP server.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from typing import Any

from . import config

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None


def _connect() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        os.makedirs(os.path.dirname(config.DB_PATH), exist_ok=True)
        _conn = sqlite3.connect(config.DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _init_schema(_conn)
    return _conn


def _init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS jobs (
            job_id        TEXT PRIMARY KEY,
            template      TEXT NOT NULL,
            isa           TEXT NOT NULL,
            params        TEXT NOT NULL,       -- JSON of validated params
            status        TEXT NOT NULL,       -- queued|running|done|failed
            created_at    REAL NOT NULL,
            started_at    REAL,
            finished_at   REAL,
            return_code   INTEGER,
            error         TEXT,
            outdir_wsl    TEXT NOT NULL,
            outdir_win    TEXT NOT NULL,
            final_tick    INTEGER,
            group_id      TEXT,                -- comparison this job belongs to (nullable)
            label         TEXT                 -- human label, e.g. "X86 / timing"
        );

        CREATE TABLE IF NOT EXISTS comparisons (
            comparison_id TEXT PRIMARY KEY,
            title         TEXT,
            spec          TEXT NOT NULL,        -- JSON: language, isas, cpu, params
            created_at    REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS checkpoints (
            checkpoint_id TEXT PRIMARY KEY,
            job_id        TEXT NOT NULL,
            dir_wsl       TEXT NOT NULL,
            dir_win       TEXT NOT NULL,
            tick          INTEGER,
            params        TEXT NOT NULL,        -- JSON of the producing job's params
            created_at    REAL NOT NULL
        );
        """
    )
    # Migrate older databases that predate the group_id/label columns.
    existing = {r[1] for r in conn.execute("PRAGMA table_info(jobs)")}
    for col in ("group_id", "label"):
        if col not in existing:
            conn.execute(f"ALTER TABLE jobs ADD COLUMN {col} TEXT")
    conn.commit()


def insert_job(row: dict[str, Any]) -> None:
    with _lock:
        conn = _connect()
        row = {"group_id": None, "label": None, **row}
        conn.execute(
            """INSERT INTO jobs
               (job_id, template, isa, params, status, created_at, outdir_wsl,
                outdir_win, group_id, label)
               VALUES (:job_id, :template, :isa, :params, :status, :created_at,
                       :outdir_wsl, :outdir_win, :group_id, :label)""",
            row,
        )
        conn.commit()


def update_job(job_id: str, **fields: Any) -> None:
    if not fields:
        return
    with _lock:
        conn = _connect()
        assignments = ", ".join(f"{k} = :{k}" for k in fields)
        conn.execute(
            f"UPDATE jobs SET {assignments} WHERE job_id = :job_id",
            {**fields, "job_id": job_id},
        )
        conn.commit()


def get_job(job_id: str) -> dict[str, Any] | None:
    with _lock:
        conn = _connect()
        cur = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,))
        row = cur.fetchone()
    return dict(row) if row else None


def list_jobs() -> list[dict[str, Any]]:
    with _lock:
        conn = _connect()
        cur = conn.execute("SELECT * FROM jobs ORDER BY created_at")
    return [dict(r) for r in cur.fetchall()]


def insert_checkpoint(row: dict[str, Any]) -> None:
    with _lock:
        conn = _connect()
        conn.execute(
            """INSERT OR REPLACE INTO checkpoints
               (checkpoint_id, job_id, dir_wsl, dir_win, tick, params, created_at)
               VALUES (:checkpoint_id, :job_id, :dir_wsl, :dir_win, :tick, :params,
                       :created_at)""",
            row,
        )
        conn.commit()


def list_checkpoints() -> list[dict[str, Any]]:
    with _lock:
        conn = _connect()
        cur = conn.execute("SELECT * FROM checkpoints ORDER BY created_at")
    return [dict(r) for r in cur.fetchall()]


def get_checkpoint(checkpoint_id: str) -> dict[str, Any] | None:
    with _lock:
        conn = _connect()
        cur = conn.execute(
            "SELECT * FROM checkpoints WHERE checkpoint_id = ?", (checkpoint_id,)
        )
        row = cur.fetchone()
    return dict(row) if row else None


def insert_comparison(row: dict[str, Any]) -> None:
    with _lock:
        conn = _connect()
        conn.execute(
            """INSERT INTO comparisons (comparison_id, title, spec, created_at)
               VALUES (:comparison_id, :title, :spec, :created_at)""",
            row,
        )
        conn.commit()


def get_comparison(comparison_id: str) -> dict[str, Any] | None:
    with _lock:
        conn = _connect()
        cur = conn.execute(
            "SELECT * FROM comparisons WHERE comparison_id = ?", (comparison_id,)
        )
        row = cur.fetchone()
    return dict(row) if row else None


def list_comparisons() -> list[dict[str, Any]]:
    with _lock:
        conn = _connect()
        cur = conn.execute("SELECT * FROM comparisons ORDER BY created_at DESC")
    return [dict(r) for r in cur.fetchall()]


def jobs_in_group(group_id: str) -> list[dict[str, Any]]:
    with _lock:
        conn = _connect()
        cur = conn.execute(
            "SELECT * FROM jobs WHERE group_id = ? ORDER BY created_at", (group_id,)
        )
    return [dict(r) for r in cur.fetchall()]


def now() -> float:
    return time.time()


def dumps(obj: Any) -> str:
    return json.dumps(obj, default=str)
