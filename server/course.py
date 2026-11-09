"""Course mode: guided computer-architecture labs on top of the simulation engine.

Each lab bundles a learning objective, background, a *ready-to-run experiment*, and
guided questions. `run_lab` dispatches to the right engine (CPU sweep, cache sweep, ISA
comparison, or a validation microbenchmark) so a student - or Claude - can run a whole
lab in one step and then reason about the numbers. `explain_prompt` produces the
"explain this result" text that turns raw metrics into understanding.
"""

from __future__ import annotations

from typing import Any

from . import comparisons, validation

# --- Small, self-contained workloads used by the labs -----------------------

MATMUL = r"""
#include <stdio.h>
#define N 64
static long A[N][N], B[N][N], C[N][N];
int main(void) {
    for (int i=0;i<N;i++) for (int j=0;j<N;j++){ A[i][j]=i+j; B[i][j]=i-j; }
    for (int i=0;i<N;i++)
      for (int j=0;j<N;j++){ long s=0; for(int k=0;k<N;k++) s+=A[i][k]*B[k][j]; C[i][j]=s; }
    long chk=0; for(int i=0;i<N;i++) chk+=C[i][i];
    printf("checksum=%ld\n", chk);
    return 0;
}
"""

# --- Lab catalogue ----------------------------------------------------------

LABS: list[dict[str, Any]] = [
    {
        "id": "ooo-vs-inorder",
        "title": "Lab 1 - Out-of-order vs in-order execution",
        "level": "intro",
        "objective": "See how an out-of-order CPU extracts instruction-level "
                     "parallelism that an in-order CPU cannot.",
        "background": "In-order cores stall on the first instruction that isn't ready. "
                     "Out-of-order (OoO) cores keep a window of instructions and issue "
                     "any that are ready, hiding latency. More independent work => bigger "
                     "OoO advantage.",
        "run": {"engine": "cpu_sweep", "isa": "X86",
                "cpus": ["atomic", "timing", "o3"], "source": validation.PEAK_IPC_SRC},
        "look_for": "IPC should rise sharply from timing (~1) to o3 (>1). Cycles drop.",
        "questions": [
            "What IPC did the o3 core reach, and why is it above 1.0?",
            "Atomic executes in ~1 cycle each - why isn't it a fair performance model?",
            "Which real workloads would benefit LEAST from out-of-order execution?",
        ],
    },
    {
        "id": "cache-size-sweep",
        "title": "Lab 2 - How L2 cache size affects performance",
        "level": "intro",
        "objective": "Quantify the effect of L2 cache capacity on miss rate and cycles.",
        "background": "Bigger caches hold more of the working set, cutting misses - but "
                     "with diminishing returns once the working set fits. This is a "
                     "classic design-space tradeoff (area/power vs performance).",
        "run": {"engine": "param_sweep", "isa": "X86", "cpu": "timing", "axis": "l2_size",
                "values": ["128kB", "256kB", "512kB", "1MB", "2MB"], "source": MATMUL},
        "look_for": "L2 miss rate falls as size grows, then flattens once the working "
                    "set fits. Cycles track the miss rate.",
        "questions": [
            "At what L2 size do the returns flatten out? What does that tell you about "
            "this workload's footprint?",
            "Why doesn't doubling the cache keep halving the misses?",
        ],
    },
    {
        "id": "isa-comparison",
        "title": "Lab 3 - x86 vs RISC-V for the same program",
        "level": "intro",
        "objective": "Compare instruction counts and cycles for one algorithm on two ISAs.",
        "background": "RISC-V is a simple load/store ISA; x86 is a rich CISC ISA. The same "
                     "C program becomes different instruction streams. More (simpler) "
                     "instructions vs fewer (complex) ones is a core ISA tradeoff.",
        "run": {"engine": "isa_comparison", "isas": ["X86", "RISCV"], "cpu": "timing",
                "source": MATMUL},
        "look_for": "Different instruction counts; compare cycles and CPI, not just insts.",
        "questions": [
            "Which ISA executed more instructions, and which finished in fewer cycles?",
            "Why is comparing raw instruction counts across ISAs misleading?",
        ],
    },
    {
        "id": "memory-hierarchy",
        "title": "Lab 4 - Seeing the cache hierarchy (validation)",
        "level": "core",
        "objective": "Confirm the simulated memory system behaves as configured by "
                     "measuring latency as the working set outgrows each cache level.",
        "background": "A dependent pointer chase can't be parallelized, so each access "
                     "pays full memory latency. Grow the data past L1, then L2, and cycles "
                     "must climb at each boundary.",
        "run": {"engine": "validate_memory", "isa": "X86", "cpu": "timing"},
        "look_for": "Cycles strictly increase L1 -> L2 -> DRAM; the verdict should PASS.",
        "questions": [
            "By roughly what factor did cycles grow from L1 to DRAM working sets?",
            "How does this confirm the cache hierarchy is real and not a single flat memory?",
        ],
    },
    {
        "id": "ipc-validation",
        "title": "Lab 5 - Does out-of-order really work? (validation)",
        "level": "core",
        "objective": "Prove the o3 core extracts ILP, using a benchmark with known "
                     "available parallelism.",
        "background": "Four independent accumulator chains offer ILP ~4. A correct OoO "
                     "model must beat the in-order core's ~1.0 IPC.",
        "run": {"engine": "validate_ipc", "isa": "X86"},
        "look_for": "o3 IPC clearly above timing IPC and above 1.2; verdict should PASS.",
        "questions": [
            "How close did the o3 IPC get to the theoretical 4, and what limits it?",
        ],
    },
]

_BY_ID = {lab["id"]: lab for lab in LABS}


def list_labs() -> list[dict[str, Any]]:
    return [
        {k: lab[k] for k in ("id", "title", "level", "objective")}
        for lab in LABS
    ]


def get_lab(lab_id: str) -> dict[str, Any]:
    if lab_id not in _BY_ID:
        raise KeyError(f"unknown lab {lab_id!r}; available: {list(_BY_ID)}")
    return _BY_ID[lab_id]


async def run_lab(lab_id: str) -> dict[str, Any]:
    """Launch the experiment for a lab. Returns a comparison_id to poll."""
    lab = get_lab(lab_id)
    r = lab["run"]
    engine = r["engine"]
    title = lab["title"]

    if engine == "cpu_sweep":
        res = await comparisons.create_cpu_sweep(
            source=r["source"], language="c", isa=r["isa"], cpus=r["cpus"], title=title)
    elif engine == "param_sweep":
        res = await comparisons.create_sweep(
            source=r["source"], language="c", isa=r["isa"], axis=r["axis"],
            values=r["values"], cpu=r.get("cpu", "timing"), title=title)
    elif engine == "isa_comparison":
        res = await comparisons.create_comparison(
            source=r["source"], language="c", isas=r["isas"],
            cpu=r.get("cpu", "timing"), title=title)
    elif engine == "validate_ipc":
        res = await comparisons.run_experiment(**validation.ipc_experiment(r["isa"]))
    elif engine == "validate_memory":
        res = await comparisons.run_experiment(
            **validation.memory_ladder_experiment(r["isa"], r.get("cpu", "timing")))
    else:
        raise ValueError(f"unknown lab engine {engine!r}")

    res["lab_id"] = lab_id
    return res


def explain_prompt(lab_id: str, comparison: dict[str, Any]) -> str:
    """Build an 'explain this result' prompt from a lab + its assembled results."""
    lab = get_lab(lab_id)
    lines = [f"Results for {lab['title']}:", ""]
    for r in comparison.get("results", []):
        m = r.get("metrics") or {}
        if r.get("status") == "done":
            lines.append(
                f"- {r['label']}: {m.get('instructions')} insts, {m.get('cycles')} cycles, "
                f"CPI {m.get('cpi')}, IPC {m.get('ipc')}, "
                f"L1D miss {m.get('l1d_miss_rate')}, L2 miss {m.get('l2_miss_rate')}"
            )
        else:
            lines.append(f"- {r['label']}: {r['status']}")
    q = "\n".join(f"  {i+1}. {q}" for i, q in enumerate(lab.get("questions", [])))
    return (
        f"You are a computer-architecture teaching assistant.\n\n"
        f"Objective: {lab['objective']}\n"
        f"Background: {lab['background']}\n\n"
        + "\n".join(lines)
        + f"\n\nWhat to look for: {lab['look_for']}\n\n"
        f"Explain these results to a student, quantitatively, and answer:\n{q}"
    )
