"""Reproduce the 3 experiments from CA (3).pdf through the MCP comparison engine."""

import asyncio
from pathlib import Path

from server import comparisons, db

MATMUL = Path("testbins/matrix_mul.c").read_text()
AES = Path("testbins/aesbenchmark.c").read_text()


async def wait(cid, timeout=1200):
    for _ in range(timeout):
        g = comparisons.get_comparison(cid)
        if g["overall_status"] != "running":
            return g
        await asyncio.sleep(1)
    return comparisons.get_comparison(cid)


def show(title, g, order_key, paper):
    print(f"\n===== {title} =====")
    print(f"{'config':<16}{'IPC':>10}{'L1D miss%':>12}   (paper)")
    rows = {}
    for r in g["results"]:
        m = r.get("metrics") or {}
        rows[r["label"]] = m
    for label, pIPC, pMiss in paper:
        m = rows.get(label, {})
        ipc = m.get("ipc")
        miss = m.get("l1d_miss_rate")
        missp = f"{miss*100:.2f}%" if isinstance(miss, (int, float)) else "-"
        ipcs = f"{ipc:.3f}" if isinstance(ipc, (int, float)) else "-"
        print(f"{label:<16}{ipcs:>10}{missp:>12}   (IPC {pIPC}, miss {pMiss})")


async def main():
    db._connect()

    # Experiment 1: matmul, L1D capacity 4/32/64 kB, X86 timing
    e1 = await comparisons.create_sweep(
        source=MATMUL, language="c", isa="X86", axis="l1d_size",
        values=["4kB", "32kB", "64kB"], cpu="timing", title="Exp1 matmul L1D capacity")
    # Experiment 2: AES, same capacities
    e2 = await comparisons.create_sweep(
        source=AES, language="c", isa="X86", axis="l1d_size",
        values=["4kB", "32kB", "64kB"], cpu="timing", title="Exp2 AES L1D capacity")
    # Experiment 3: matmul, L1D fixed 16kB, assoc 1/2/8
    e3 = await comparisons.create_sweep(
        source=MATMUL, language="c", isa="X86", axis="l1d_assoc",
        values=[1, 2, 8], cpu="timing", base_params={"l1d_size": "16kB"},
        title="Exp3 matmul associativity @16kB")

    g1 = await wait(e1["comparison_id"])
    g2 = await wait(e2["comparison_id"])
    g3 = await wait(e3["comparison_id"])

    show("Experiment 1: L1D capacity (matmul)", g1, "l1d_size",
         [("l1d_size=4kB", 0.277, "5.06%"), ("l1d_size=32kB", 0.297, "1.82%"),
          ("l1d_size=64kB", 0.310, "0.04%")])
    show("Experiment 2: L1D capacity (AES)", g2, "l1d_size",
         [("l1d_size=4kB", 0.259, "0.14%"), ("l1d_size=32kB", 0.259, "0.14%"),
          ("l1d_size=64kB", 0.263, "0.14%")])
    show("Experiment 3: Associativity @16kB (matmul)", g3, "l1d_assoc",
         [("l1d_assoc=1", 0.279, "4.74%"), ("l1d_assoc=2", 0.296, "2.07%"),
          ("l1d_assoc=8", 0.308, "0.34%")])


if __name__ == "__main__":
    asyncio.run(main())
