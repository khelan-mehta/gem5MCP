"""Validated config-template library (design doc 3.3 / 7).

The model never sends raw Python. It picks one of a small set of *fixed* gem5 config
scripts (in ``templates/``) and supplies parameters that are validated here with
pydantic, then turned into a plain ``argv`` list. Nothing is string-interpolated into
a ``.py`` file — the scripts read everything through ``argparse``.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from . import config

ISAName = Literal["X86", "RISCV", "ARM"]
CPUType = Literal["atomic", "timing", "o3", "minor"]
ReplPolicy = Literal["lru", "fifo", "random", "mru", "nru", "lip", "treeplru",
                     "rrip", "brrip", "secondchance"]
Prefetcher = Literal["none", "stride", "tagged", "ampm", "bop", "dcpt"]
DramType = Literal["ddr3_1600", "ddr3_2133", "ddr4_2400", "lpddr3_1600", "hbm",
                   "ddr3_1600_dual", "ddr4_2400_dual", "lpddr3_1600_dual"]
LineSize = Literal[16, 32, 64]

_SIZE_RE = re.compile(r"^\d+(\.\d+)?\s?[kKmMgG]?[iI]?[bB]$")
_FREQ_RE = re.compile(r"^\d+(\.\d+)?\s?[kKmMgG]?Hz$")
# A conservative path: no shell metacharacters, no spaces, no quotes.
_PATH_RE = re.compile(r"^[A-Za-z0-9_./+\-]+$")


class _Base(BaseModel):
    """Parameters shared by every SE-mode template."""

    model_config = {"extra": "forbid"}

    isa: ISAName = Field(description="Target ISA; must be one of the pre-built binaries.")
    binary: str = Field(
        description="Absolute WSL path to a statically-linked guest ELF "
        "(e.g. /home/gem5/gem5/tests/test-progs/hello/bin/x86/linux/hello)."
    )
    cpu: CPUType = Field("timing", description="CPU model.")
    mem_size: str = Field("512MB", description="System memory size, e.g. '512MB', '2GB'.")
    clock: str = Field("1GHz", description="Core clock frequency, e.g. '1GHz'.")
    l1i_size: str = Field("32kB", description="L1 instruction cache size.")
    l1d_size: str = Field("32kB", description="L1 data cache size.")
    l2_size: str = Field("256kB", description="Unified L2 cache size.")
    l1d_assoc: int = Field(8, ge=1, le=64, description="L1 data cache associativity "
                           "(ways). 1 = direct-mapped. Default 8.")
    l1i_assoc: int = Field(8, ge=1, le=64, description="L1 instruction cache assoc.")
    l2_assoc: int = Field(4, ge=1, le=64, description="L2 cache associativity (ways).")
    # --- cache policy / microarchitecture ---
    l1d_repl: ReplPolicy = Field("lru", description="L1D replacement policy.")
    l1i_repl: ReplPolicy = Field("lru", description="L1I replacement policy.")
    l2_repl: ReplPolicy = Field("lru", description="L2 replacement policy.")
    prefetcher: Prefetcher = Field("stride", description="L1D hardware prefetcher "
                                   "('none' to disable).")
    l1d_mshrs: int = Field(16, ge=1, le=128, description="L1D outstanding misses "
                           "(MSHRs) — memory-level parallelism.")
    cache_line_size: LineSize = Field(64, description="Cache block size in bytes "
                                      "(16/32/64). Affects spatial locality.")
    # --- memory ---
    dram_type: DramType = Field("ddr3_1600", description="DRAM technology / channels.")
    # --- out-of-order pipeline (only applied when cpu='o3'; None = gem5 default) ---
    rob_entries: int | None = Field(None, ge=8, le=1024, description="Reorder buffer "
                                    "entries (o3).")
    fetch_width: int | None = Field(None, ge=1, le=16, description="Fetch width (o3).")
    decode_width: int | None = Field(None, ge=1, le=16, description="Decode width (o3).")
    issue_width: int | None = Field(None, ge=1, le=16, description="Issue width (o3).")
    dispatch_width: int | None = Field(None, ge=1, le=16, description="Dispatch width (o3).")
    commit_width: int | None = Field(None, ge=1, le=16, description="Commit width (o3).")
    lq_entries: int | None = Field(None, ge=4, le=512, description="Load queue entries (o3).")
    sq_entries: int | None = Field(None, ge=4, le=512, description="Store queue entries (o3).")
    phys_int_regs: int | None = Field(None, ge=32, le=1024, description="Physical integer "
                                      "registers (o3).")
    phys_float_regs: int | None = Field(None, ge=32, le=1024, description="Physical float "
                                        "registers (o3).")
    max_insts: int | None = Field(
        None, ge=1, description="Optional: stop after this many committed instructions."
    )
    debug_flags: list[str] = Field(
        default_factory=list,
        description="Optional gem5 debug flags. Restricted to a safe whitelist.",
    )
    take_checkpoint: bool = Field(
        False, description="Write a checkpoint at the end of the run."
    )
    restore_checkpoint_dir: str | None = Field(
        None, description="WSL path of a checkpoint directory to restore from."
    )

    @field_validator("binary", "restore_checkpoint_dir")
    @classmethod
    def _safe_path(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if not _PATH_RE.match(v):
            raise ValueError(f"unsafe or malformed path: {v!r}")
        return v

    @field_validator("mem_size", "l1i_size", "l1d_size", "l2_size")
    @classmethod
    def _size(cls, v: str) -> str:
        if not _SIZE_RE.match(v.strip()):
            raise ValueError(f"invalid size string: {v!r} (expected e.g. '32kB', '2GB')")
        return v.strip()

    @field_validator("clock")
    @classmethod
    def _freq(cls, v: str) -> str:
        if not _FREQ_RE.match(v.strip()):
            raise ValueError(f"invalid clock: {v!r} (expected e.g. '1GHz', '2.5GHz')")
        return v.strip()

    @field_validator("debug_flags")
    @classmethod
    def _flags(cls, v: list[str]) -> list[str]:
        bad = [f for f in v if f not in config.ALLOWED_DEBUG_FLAGS]
        if bad:
            allowed = ", ".join(sorted(config.ALLOWED_DEBUG_FLAGS))
            raise ValueError(f"debug flag(s) not allowed: {bad}. Allowed: {allowed}")
        return v


class SingleCoreParams(_Base):
    """SE-mode, one CPU core."""


class MultiCoreParams(_Base):
    """SE-mode, N identical CPU cores running the same binary."""

    num_cores: int = Field(2, ge=2, le=16, description="Number of CPU cores.")


class Template(BaseModel):
    name: str
    script: str  # filename inside templates/
    description: str
    model: type[_Base]


REGISTRY: dict[str, Template] = {
    "se_single_core": Template(
        name="se_single_core",
        script="se_single_core.py",
        description="Syscall-emulation run of a single guest binary on one CPU core "
        "with a classic L1/L2 cache hierarchy.",
        model=SingleCoreParams,
    ),
    "se_multi_core": Template(
        name="se_multi_core",
        script="se_multi_core.py",
        description="Syscall-emulation run of a guest binary on N identical cores, "
        "each with a private L1/L2 cache hierarchy.",
        model=MultiCoreParams,
    ),
}


def describe_templates() -> list[dict]:
    """Return template metadata + JSON schema of each template's parameters."""
    out = []
    for t in REGISTRY.values():
        out.append(
            {
                "name": t.name,
                "description": t.description,
                "parameters": t.model.model_json_schema(),
            }
        )
    return out


def validate(template: str, params: dict) -> _Base:
    if template not in REGISTRY:
        raise ValueError(
            f"unknown template {template!r}; available: {list(REGISTRY)}"
        )
    return REGISTRY[template].model(**params)


def script_wsl_path(template: str) -> str:
    """WSL path to the fixed template script (lives on the Windows side, read via /mnt)."""
    return str(config.TEMPLATES_DIR_WSL / REGISTRY[template].script)


def build_argv(validated: _Base, template: str) -> list[str]:
    """Turn validated params into the argv passed *after* the template script path."""
    argv = [
        "--isa", validated.isa,
        "--binary", validated.binary,
        "--cpu", validated.cpu,
        "--mem-size", validated.mem_size,
        "--clock", validated.clock,
        "--l1i-size", validated.l1i_size,
        "--l1d-size", validated.l1d_size,
        "--l2-size", validated.l2_size,
        "--l1d-assoc", str(validated.l1d_assoc),
        "--l1i-assoc", str(validated.l1i_assoc),
        "--l2-assoc", str(validated.l2_assoc),
    ]
    # The extended knob set is only implemented by se_single_core; se_multi_core
    # supports sizes + associativity only.
    if template == "se_single_core":
        argv += [
            "--l1d-repl", validated.l1d_repl,
            "--l1i-repl", validated.l1i_repl,
            "--l2-repl", validated.l2_repl,
            "--prefetcher", validated.prefetcher,
            "--l1d-mshrs", str(validated.l1d_mshrs),
            "--cache-line-size", str(validated.cache_line_size),
            "--dram-type", validated.dram_type,
        ]
        # OoO pipeline knobs: only forward when set (leave gem5 default otherwise).
        o3_flags = {
            "--rob-entries": validated.rob_entries,
            "--fetch-width": validated.fetch_width,
            "--decode-width": validated.decode_width,
            "--issue-width": validated.issue_width,
            "--dispatch-width": validated.dispatch_width,
            "--commit-width": validated.commit_width,
            "--lq-entries": validated.lq_entries,
            "--sq-entries": validated.sq_entries,
            "--phys-int-regs": validated.phys_int_regs,
            "--phys-float-regs": validated.phys_float_regs,
        }
        for flag, val in o3_flags.items():
            if val is not None:
                argv += [flag, str(val)]
    if validated.max_insts is not None:
        argv += ["--max-insts", str(validated.max_insts)]
    if validated.take_checkpoint:
        argv += ["--take-checkpoint"]
    if validated.restore_checkpoint_dir:
        argv += ["--restore-checkpoint-dir", validated.restore_checkpoint_dir]
    if isinstance(validated, MultiCoreParams):
        argv += ["--num-cores", str(validated.num_cores)]
    return argv
