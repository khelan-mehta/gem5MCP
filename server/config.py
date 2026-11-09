"""Central configuration for the gem5 MCP server.

Everything the server needs to know about *where* gem5 lives and *how* to invoke
it through WSL is collected here so the rest of the code never hard-codes paths.
Values can be overridden with environment variables (handy for CI / other hosts).
"""

from __future__ import annotations

import os
from pathlib import PurePosixPath, PureWindowsPath

# ---------------------------------------------------------------------------
# WSL / gem5 location
# ---------------------------------------------------------------------------

#: Name of the WSL distribution that has gem5 built inside it.
WSL_DISTRO = os.environ.get("GEM5_MCP_DISTRO", "Ubuntu-24.04")

#: Non-root Linux user that runs simulations (defense-in-depth, design doc 3.3).
WSL_USER = os.environ.get("GEM5_MCP_USER", "gem5")

#: Absolute path *inside WSL* of the gem5 checkout.
GEM5_ROOT = PurePosixPath(os.environ.get("GEM5_MCP_GEM5_ROOT", "/home/gem5/gem5"))

#: gem5 version pinned into the image (design doc 3.7). Reported by get_gem5_version.
GEM5_VERSION = os.environ.get("GEM5_MCP_VERSION", "25.1.0.1")

#: ISAs whose gem5.opt binary has been pre-built. list_available_isas reflects this.
#: Map ISA name -> path to the built binary inside WSL.
BUILT_ISAS: dict[str, PurePosixPath] = {
    "X86": GEM5_ROOT / "build" / "X86" / "gem5.opt",
    "RISCV": GEM5_ROOT / "build" / "RISCV" / "gem5.opt",
    "ARM": GEM5_ROOT / "build" / "ARM" / "gem5.opt",
}

#: Directory inside WSL where per-job output directories are created.
RUNS_ROOT_WSL = PurePosixPath(
    os.environ.get("GEM5_MCP_RUNS_ROOT", "/home/gem5/gem5mcp-runs")
)

# ---------------------------------------------------------------------------
# Host (Windows) side
# ---------------------------------------------------------------------------

#: Root of this project on the Windows filesystem.
PROJECT_ROOT = PureWindowsPath(__file__).parent.parent

#: Local SQLite database (job + checkpoint metadata, design doc 3.10 / stack table).
DB_PATH = str(PROJECT_ROOT / "data" / "gem5mcp.db")

# ---------------------------------------------------------------------------
# Worker pool & safety limits
# ---------------------------------------------------------------------------

#: Max concurrent gem5 processes (design doc 3.8: ~physical cores, capped for RAM).
MAX_CONCURRENT_JOBS = int(os.environ.get("GEM5_MCP_MAX_JOBS", "4"))

#: Debug flags the model is *allowed* to enable (design doc 3.9). Off by default.
ALLOWED_DEBUG_FLAGS = {
    "Exec",
    "ExecAll",
    "Cache",
    "DRAM",
    "Fetch",
    "O3PipeView",
    "SyscallVerbose",
}

#: Kill a run whose simout/trace exceeds this many bytes (design doc 3.9).
MAX_OUTPUT_BYTES = int(os.environ.get("GEM5_MCP_MAX_OUTPUT_BYTES", str(512 * 1024 * 1024)))

#: Hard wall-clock cap per simulation (seconds); safety net against runaway jobs.
JOB_TIMEOUT_SECONDS = int(os.environ.get("GEM5_MCP_JOB_TIMEOUT", str(2 * 60 * 60)))


# ---------------------------------------------------------------------------
# Path translation helpers
# ---------------------------------------------------------------------------

def available_isas() -> list[str]:
    """ISAs whose gem5.opt binary actually exists on disk (auto-detects new builds)."""
    from pathlib import Path

    out = []
    for isa, binpath in BUILT_ISAS.items():
        try:
            if Path(wsl_to_unc(binpath)).exists():
                out.append(isa)
        except OSError:
            pass
    return out


def win_to_wsl(path: str | PureWindowsPath) -> PurePosixPath:
    """Translate a Windows path (``C:\\Users\\x``) to its WSL ``/mnt/c/Users/x`` form."""
    p = PureWindowsPath(path)
    drive = p.drive.rstrip(":").lower()
    parts = p.parts[1:] if p.drive else p.parts
    return PurePosixPath("/mnt", drive, *parts)


def wsl_to_unc(path: str | PurePosixPath) -> str:
    """Translate a WSL absolute path to a Windows UNC path under ``\\\\wsl.localhost``."""
    posix = PurePosixPath(path)
    tail = "\\".join(posix.parts[1:])  # drop leading "/"
    return rf"\\wsl.localhost\{WSL_DISTRO}\{tail}"


#: WSL path of our validated template library (lives on the Windows side, read via /mnt).
TEMPLATES_DIR_WSL = win_to_wsl(PROJECT_ROOT / "templates")
