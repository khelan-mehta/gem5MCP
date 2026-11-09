"""End-to-end test: compile Fibonacci and compare X86 vs RISCV through the backend."""

import asyncio

from server import comparisons, db, workloads

SRC = r"""
#include <stdio.h>
long fib(int n){ if(n<2) return n; return fib(n-1)+fib(n-2); }
int main(void){ printf("fib=%ld\n", fib(28)); return 0; }
"""


async def main():
    db._connect()
    bx = await workloads.compile_source(SRC, "c", "X86")
    br = await workloads.compile_source(SRC, "c", "RISCV")
    print("X86 bin:  ", bx)
    print("RISCV bin:", br)

    cmp = await comparisons.create_comparison(
        source=SRC, language="c", isas=["X86", "RISCV"], cpu="timing", title="fib(28) test"
    )
    cid = cmp["comparison_id"]
    print("comparison:", cid, "errors:", cmp["errors"])

    for _ in range(300):
        g = comparisons.get_comparison(cid)
        if g["overall_status"] != "running":
            break
        await asyncio.sleep(1)

    g = comparisons.get_comparison(cid)
    print("overall:", g["overall_status"], g["progress"])
    for r in g["results"]:
        m = r.get("metrics", {})
        print(f"  {r['label']}: {r['status']} | insts={m.get('instructions')} "
              f"cycles={m.get('cycles')} cpi={m.get('cpi')} "
              f"l1d_miss={m.get('l1d_miss_rate')}")


if __name__ == "__main__":
    asyncio.run(main())
