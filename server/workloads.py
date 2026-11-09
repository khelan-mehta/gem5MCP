"""Compile guest source code into per-ISA static binaries for gem5 SE mode.

This is what turns "here's some C code" into "a binary gem5 can simulate on X86 and
RISC-V". Source is written on the Windows side and compiled inside WSL via the already
installed native + cross toolchains. Compilation never *executes* the code; the program
only ever runs later, sandboxed inside gem5's syscall emulation as the non-root user.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
from pathlib import Path, PurePosixPath

from . import config

# language -> (extension, {isa: compiler})
_TOOLCHAINS: dict[str, tuple[str, dict[str, str]]] = {
    "c": (".c", {
        "X86": "gcc",
        "RISCV": "riscv64-linux-gnu-gcc",
        "ARM": "aarch64-linux-gnu-gcc",
    }),
    "cpp": (".cpp", {
        "X86": "g++",
        "RISCV": "riscv64-linux-gnu-g++",
        "ARM": "aarch64-linux-gnu-g++",
    }),
}

#: Where compiled workloads live inside WSL.
WORKLOADS_ROOT_WSL = PurePosixPath("/home/gem5/gem5mcp-workloads")
#: Where source is staged on the Windows side (readable from WSL via /mnt/c).
WORKLOADS_ROOT_WIN = Path(config.DB_PATH).parent / "workloads"

_IDENT = re.compile(r"^[A-Za-z0-9_]+$")


class CompileError(RuntimeError):
    pass


def languages() -> list[str]:
    return list(_TOOLCHAINS)


def _digest(source: str, language: str, isa: str) -> str:
    h = hashlib.sha1(f"{language}:{isa}:{source}".encode()).hexdigest()
    return h[:16]


async def compile_source(source: str, language: str, isa: str) -> str:
    """Compile `source` for `isa`; return the WSL path of the static binary (cached)."""
    language = language.lower()
    if language not in _TOOLCHAINS:
        raise CompileError(f"unsupported language {language!r}; use one of {languages()}")
    ext, compilers = _TOOLCHAINS[language]
    if isa not in compilers:
        raise CompileError(f"no {language} toolchain for ISA {isa!r}")

    digest = _digest(source, language, isa)
    out_wsl = WORKLOADS_ROOT_WSL / digest / f"prog_{isa}"

    # Cache hit? (binary already built for this exact source+isa)
    out_unc = config.wsl_to_unc(out_wsl)
    if Path(out_unc).exists():
        return str(out_wsl)

    # Stage source on the Windows side; WSL reads it through /mnt/c.
    src_dir_win = WORKLOADS_ROOT_WIN / digest
    src_dir_win.mkdir(parents=True, exist_ok=True)
    src_win = src_dir_win / f"src{ext}"
    src_win.write_text(source, encoding="utf-8")
    src_wsl = config.win_to_wsl(src_win)

    compiler = compilers[isa]
    cmd = (
        f"mkdir -p {out_wsl.parent} && "
        f"{compiler} -static -O2 -o {out_wsl} {src_wsl}"
    )
    proc = await asyncio.create_subprocess_exec(
        "wsl.exe", "-d", config.WSL_DISTRO, "-u", config.WSL_USER, "--",
        "bash", "-lc", cmd,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    _, err = await proc.communicate()
    if proc.returncode != 0:
        msg = err.decode("utf-8", "replace").strip()
        raise CompileError(f"{compiler} failed for {isa}:\n{msg}")
    return str(out_wsl)
