"""Run the validation microbenchmarks and print their verdicts."""

import asyncio

from server import comparisons, db, validation


async def run(kwargs, tag):
    res = await comparisons.run_experiment(**kwargs)
    cid = res["comparison_id"]
    print(f"[{tag}] {cid} errors={res['errors']}")
    for _ in range(600):
        g = comparisons.get_comparison(cid)
        if g["overall_status"] != "running":
            break
        await asyncio.sleep(1)
    g = comparisons.get_comparison(cid)
    print(f"[{tag}] overall={g['overall_status']} progress={g['progress']}")
    for r in g["results"]:
        m = r.get("metrics") or {}
        print(f"   {r['label']}: {r['status']} cycles={m.get('cycles')} "
              f"ipc={m.get('ipc')} l2_miss={m.get('l2_miss_rate')}")
    v = g.get("verdict", {})
    print(f"[{tag}] VERDICT passed={v.get('passed')}")
    for c in v.get("checks", []):
        print(f"     {'PASS' if c['pass'] else 'FAIL'}  {c['name']} ({c['detail']})")
    print(f"[{tag}] {v.get('explanation','')}\n")


async def main():
    db._connect()
    await run(validation.ipc_experiment("X86"), "IPC")
    await run(validation.memory_ladder_experiment("X86", "timing"), "MEMORY")


if __name__ == "__main__":
    asyncio.run(main())
