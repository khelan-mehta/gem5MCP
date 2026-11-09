"""Builds the WSL command line that launches a gem5 simulation.

The MCP server is fundamentally a *subprocess orchestrator* (design doc 3.2): there is
no gem5 API, only ``gem5.opt <script.py> [args]``. This module assembles the exact
``wsl.exe ... bash -lc '<cmd>'`` invocation; ``jobs.py`` owns the async lifecycle.
"""

from __future__ import annotations

import shlex
from pathlib import PurePosixPath

from . import config


def outdir_for(job_id: str) -> tuple[str, str]:
    """Return (wsl_path, windows_unc_path) of the per-job output directory (design 3.8)."""
    wsl = config.RUNS_ROOT_WSL / job_id / "m5out"
    return str(wsl), config.wsl_to_unc(wsl)


def build_wsl_argv(
    *,
    isa: str,
    outdir_wsl: str,
    script_wsl: str,
    script_argv: list[str],
    debug_flags: list[str],
) -> list[str]:
    """Assemble the argv for ``asyncio.create_subprocess_exec`` (no shell on our side)."""
    gem5_bin = config.BUILT_ISAS[isa]

    # gem5's own options come *before* the config script; debug flags belong to gem5.
    gem5_opts = [str(gem5_bin), f"--outdir={outdir_wsl}"]
    if debug_flags:
        gem5_opts.append(f"--debug-flags={','.join(debug_flags)}")

    inner = [*gem5_opts, script_wsl, *script_argv]
    # Every token is server-validated, but quote anyway for defense in depth.
    posix_cmd = "mkdir -p {} && cd {} && {}".format(
        shlex.quote(str(PurePosixPath(outdir_wsl))),
        shlex.quote(str(config.GEM5_ROOT)),
        " ".join(shlex.quote(tok) for tok in inner),
    )

    return [
        "wsl.exe",
        "-d",
        config.WSL_DISTRO,
        "-u",
        config.WSL_USER,
        "--",
        "bash",
        "-lc",
        posix_cmd,
    ]
