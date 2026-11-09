"""Self-consistency validation microbenchmarks.

These do NOT claim to match real silicon. Instead they check that the simulator
behaves the way its own configuration and basic architecture principles say it must:

  * IPC test: a loop of independent integer adds. An out-of-order (o3) core should
    extract instruction-level parallelism and reach IPC well above an in-order
    (timing) core. If it doesn't, the model (or your understanding) is wrong.

  * Memory-latency ladder: dependent pointer-chasing over working sets that fit in
    L1, in L2, and in DRAM. Cycles must climb as the data spills out of each cache
    level - a direct, visible confirmation the cache hierarchy is real.

This is the honest, teachable form of "validation": provable expectations, not a
claim of hardware accuracy.
"""

from __future__ import annotations

from typing import Any

# --- Microbenchmark sources -------------------------------------------------

# Four independent accumulator chains -> ILP ~= 4 available to an OoO core.
PEAK_IPC_SRC = r"""
#include <stdio.h>
int main(void) {
    long a=0,b=0,c=0,d=0;
    for (long i = 0; i < 1000000L; i++) {
        a += i; b += i ^ 1; c += i ^ 2; d += i ^ 3;
    }
    volatile long sink = a + b + c + d;
    printf("%ld\n", (long)sink);
    return 0;
}
"""

# Dependent pointer chase over a precomputed cycle of length N (stride coprime to N,
# N a power of two so an odd stride is coprime). Pure load-use latency, no arithmetic.
# __N__ is substituted with the working-set element count (no %-formatting; the C code
# contains literal % in printf).
_POINTER_CHASE_TMPL = r"""
#include <stdio.h>
#define N __N__L
#define STEPS 2000000L
static int nxt[N];
int main(void) {
    long stride = 131;                 /* odd => coprime to power-of-two N */
    for (long i = 0; i < N; i++) nxt[i] = (int)((i + stride) % N);
    int i = 0;
    for (long s = 0; s < STEPS; s++) i = nxt[i];
    volatile int sink = i;
    printf("%d\n", sink);
    return 0;
}
"""

# Working sets (int = 4 bytes): 16 KB (in L1), 128 KB (in L2), 8 MB (DRAM).
_LADDER = [("L1 (16KB)", 4096), ("L2 (128KB)", 32768), ("DRAM (8MB)", 2097152)]


def _chase_src(n: int) -> str:
    return _POINTER_CHASE_TMPL.replace("__N__", str(n))


def ipc_experiment(isa: str) -> dict[str, Any]:
    """Kwargs for comparisons.run_experiment: in-order vs out-of-order IPC on `isa`."""
    variants = [
        {"label": "timing (in-order)", "isa": isa, "cpu": "timing", "extra_params": {}},
        {"label": "o3 (out-of-order)", "isa": isa, "cpu": "o3", "extra_params": {}},
    ]
    return {
        "source": PEAK_IPC_SRC,
        "language": "c",
        "variants": variants,
        "kind": "validate_ipc",
        "title": f"Validation - IPC (in-order vs OoO) on {isa}",
        "spec_extra": {"isa": isa},
    }


def memory_ladder_experiment(isa: str, cpu: str = "timing") -> dict[str, Any]:
    """Kwargs for run_experiment: latency ladder across L1/L2/DRAM working sets."""
    variants = [
        {"label": label, "isa": isa, "cpu": cpu, "extra_params": {},
         "source": _chase_src(n)}
        for label, n in _LADDER
    ]
    return {
        "source": PEAK_IPC_SRC,  # unused (each variant overrides), kept for signature
        "language": "c",
        "variants": variants,
        "kind": "validate_memory",
        "title": f"Validation - memory latency ladder on {isa}/{cpu}",
        "spec_extra": {"isa": isa, "cpu": cpu},
    }


# --- Verdicts ---------------------------------------------------------------

def verdict(spec: dict[str, Any], results: list[dict[str, Any]]) -> dict[str, Any]:
    """Given assembled results, decide whether the expected behavior held."""
    done = [r for r in results if r.get("status") == "done" and r.get("metrics")]
    if len(done) < len(results) or not done:
        return {"ready": False, "note": "waiting for all runs to finish"}

    kind = spec.get("kind")
    if kind == "validate_ipc":
        return _verdict_ipc(done)
    if kind == "validate_memory":
        return _verdict_memory(done)
    return {"ready": True, "checks": []}


def _get(done, label_contains):
    for r in done:
        if label_contains.lower() in (r.get("label") or "").lower():
            return r["metrics"]
    return None


def _verdict_ipc(done) -> dict[str, Any]:
    io = _get(done, "timing")
    ooo = _get(done, "o3")
    checks = []
    ok = True
    if io and ooo and io.get("ipc") and ooo.get("ipc"):
        c1 = ooo["ipc"] > io["ipc"]
        c2 = ooo["ipc"] > 1.2
        checks.append({"name": "OoO IPC > in-order IPC",
                       "pass": c1, "detail": f"{ooo['ipc']:.3f} vs {io['ipc']:.3f}"})
        checks.append({"name": "OoO exploits ILP (IPC > 1.2)",
                       "pass": c2, "detail": f"IPC = {ooo['ipc']:.3f}"})
        ok = c1 and c2
    else:
        ok = False
        checks.append({"name": "metrics present", "pass": False, "detail": "missing IPC"})
    return {
        "ready": True, "passed": ok, "checks": checks,
        "explanation": (
            "An out-of-order core reorders these independent adds to issue several per "
            "cycle, so its IPC should clearly exceed the in-order core's ~1.0. "
            + ("Confirmed." if ok else "NOT confirmed - investigate the config.")
        ),
    }


def _verdict_memory(done) -> dict[str, Any]:
    order = ["L1", "L2", "DRAM"]
    seq = []
    for key in order:
        m = _get(done, key)
        if m and m.get("cycles"):
            seq.append((key, m["cycles"]))
    checks, ok = [], True
    for (a_name, a_cyc), (b_name, b_cyc) in zip(seq, seq[1:]):
        c = b_cyc > a_cyc
        ok = ok and c
        checks.append({"name": f"{b_name} slower than {a_name}",
                       "pass": c, "detail": f"{b_cyc:,} vs {a_cyc:,} cycles"})
    return {
        "ready": True, "passed": ok and len(seq) == 3, "checks": checks,
        "explanation": (
            "Identical pointer-chase, growing working set. As the data spills out of L1 "
            "then L2, each dependent load waits longer, so total cycles must rise. "
            + ("Confirmed - the cache hierarchy is behaving as configured."
               if ok else "NOT confirmed - unexpected.")
        ),
    }
