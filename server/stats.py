"""Parser for gem5's ``stats.txt`` (design doc 3.5).

gem5 writes results as a flat text file of ``name    value    # comment`` lines,
wrapped in ``---------- Begin/End Simulation Statistics ----------`` banners, with
no JSON option in the general case. This module turns that into a plain ``dict`` and
provides a filtered query so we never dump tens of thousands of lines at the model.
"""

from __future__ import annotations

import fnmatch
import re
from pathlib import Path
from typing import Any

_BEGIN = "Begin Simulation Statistics"
_END = "End Simulation Statistics"
# A stat line: name, whitespace, value(s), optional "# comment".
_LINE = re.compile(r"^(?P<name>\S+)\s+(?P<value>.+?)\s*(?:#.*)?$")


def _coerce(value: str) -> Any:
    """Best-effort convert a stat's textual value to int/float, else keep the string."""
    value = value.strip()
    if value in ("", "nan", "inf", "-inf"):
        return value
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        return value


def parse_stats_text(text: str) -> dict[str, Any]:
    """Parse the first simulation-statistics block from stats.txt text into a dict."""
    stats: dict[str, Any] = {}
    in_block = False
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if _BEGIN in line:
            in_block = True
            continue
        if _END in line:
            # Only return the first dump (multi-dump runs would otherwise clobber).
            break
        if not in_block or line.startswith("-"):
            continue
        m = _LINE.match(line)
        if not m:
            continue
        name = m.group("name")
        # A stat can have multiple whitespace-separated values (e.g. distributions);
        # keep the first numeric-ish token, which covers the common scalar case.
        first = m.group("value").split()[0]
        stats[name] = _coerce(first)
    return stats


def parse_stats_file(path: str | Path) -> dict[str, Any]:
    p = Path(path)
    if not p.exists():
        return {}
    return parse_stats_text(p.read_text(errors="replace"))


def filter_stats(stats: dict[str, Any], pattern: str | None) -> dict[str, Any]:
    """Filter stats by a glob (``system.cpu*``) or, if it looks like one, a regex."""
    if not pattern or pattern in ("*", ""):
        return stats
    # Treat as regex when it contains regex-only metacharacters; else glob.
    if any(c in pattern for c in "()|\\") or pattern.endswith("$"):
        rx = re.compile(pattern)
        return {k: v for k, v in stats.items() if rx.search(k)}
    return {k: v for k, v in stats.items() if fnmatch.fnmatch(k, pattern)}


#: A small, generally-present set of headline stats for job summaries.
SUMMARY_KEYS = [
    "simSeconds",
    "simTicks",
    "hostSeconds",
    "simInsts",
    "simOps",
    "system.cpu.numCycles",
    "board.processor.cores.core.numCycles",
    "system.cpu.ipc",
    "host_inst_rate",
]


def _find(stats: dict[str, Any], *, suffix=None, contains_all=None) -> Any:
    """Return the first stat value whose key matches a suffix or contains all tokens."""
    for k, v in stats.items():
        if suffix and k.endswith(suffix):
            return v
        if contains_all and all(t.lower() in k.lower() for t in contains_all):
            return v
    return None


def comparison_metrics(stats: dict[str, Any]) -> dict[str, Any]:
    """Extract a normalized, cross-architecture-comparable metric set from raw stats.

    Robust to the exact stat key names (which vary by board/ISA) by matching on
    suffixes and token sets rather than fixed paths.
    """
    insts = stats.get("simInsts")
    cycles = _find(stats, suffix="numCycles")
    cpi = _find(stats, suffix=".cpi")
    ipc = _find(stats, suffix=".ipc")
    if cpi is None and insts and cycles:
        cpi = round(cycles / insts, 4)
    if ipc is None and insts and cycles:
        ipc = round(insts / cycles, 4)
    return {
        "instructions": insts,
        "cycles": cycles,
        "cpi": cpi,
        "ipc": ipc,
        "sim_seconds": stats.get("simSeconds"),
        "host_seconds": stats.get("hostSeconds"),
        "l1i_miss_rate": _find(stats, contains_all=["l1i", "MissRate", "total"]),
        "l1d_miss_rate": _find(stats, contains_all=["l1d", "MissRate", "total"]),
        "l2_miss_rate": _find(stats, contains_all=["l2", "MissRate", "total"]),
    }


def summarize(stats: dict[str, Any]) -> dict[str, Any]:
    """Pull a handful of headline numbers for a compact job result."""
    summary = {k: stats[k] for k in SUMMARY_KEYS if k in stats}
    # Always surface a couple of universally-present totals if the exact keys differ.
    for k, v in stats.items():
        if k.endswith("numCycles") and "numCycles" not in str(summary):
            summary.setdefault(k, v)
    return summary
