"""Full-system (FS) mode: boot a real OS kernel + disk image under gem5.

HONEST STATUS: this is working infrastructure, but FS boots are heavyweight -
each asset is a multi-GB download (fetched by gem5 on first use) and, without KVM
(which WSL2 does not expose to gem5), a full Linux boot simulates slowly (many
minutes to hours). So FS is gated behind a fixed asset manifest (design doc 3.6):
the model can only pick a pre-approved asset name, never an arbitrary image path/URL.
"""

from __future__ import annotations

from . import config, jobs

# Pre-approved assets. `resource` is a gem5 Resources workload id that
# obtain_resource() downloads + caches on first use. Nothing else is runnable.
ASSETS: dict[str, dict[str, str]] = {
    "x86-ubuntu-boot": {
        "isa": "X86",
        "resource": "x86-ubuntu-24.04-boot",
        "description": "Boot Ubuntu 24.04 on X86 (downloads ~1-2 GB; slow without KVM).",
    },
    "riscv-ubuntu-boot": {
        "isa": "RISCV",
        "resource": "riscv-ubuntu-24.04-boot",
        "description": "Boot Ubuntu on RISC-V (downloads disk+kernel; slow).",
    },
}


def _fs_script_wsl() -> str:
    return str(config.TEMPLATES_DIR_WSL / "fs_boot.py")


def list_assets() -> list[dict[str, str]]:
    return [{"name": n, **a} for n, a in ASSETS.items()]


def submit_full_system(asset_name: str, max_ticks: int | None = None) -> dict:
    """Boot a pre-approved FS asset. Returns a job_id (poll with get_job_status).

    Heads-up: expect a long run and a large one-time download.
    """
    if asset_name not in ASSETS:
        raise ValueError(
            f"unknown asset {asset_name!r}; available: {list(ASSETS)}"
        )
    a = ASSETS[asset_name]
    argv = ["--isa", a["isa"], "--resource", a["resource"]]
    if max_ticks:
        argv += ["--max-ticks", str(int(max_ticks))]
    import json

    return jobs.submit_prepared(
        isa=a["isa"],
        script_wsl=_fs_script_wsl(),
        script_argv=argv,
        debug_flags=[],
        template="fs_boot",
        params_json=json.dumps({"asset": asset_name, **a, "max_ticks": max_ticks}),
        label=asset_name,
    )
