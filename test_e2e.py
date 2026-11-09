"""End-to-end test: drive a real gem5 run through the MCP server's job layer."""

import asyncio

from server import db, jobs

BINARY = "/home/gem5/testbins/hello_x86"


async def main():
    db._connect()
    sub = jobs.submit("se_single_core", {"isa": "X86", "binary": BINARY, "cpu": "timing"})
    job_id = sub["job_id"]
    print("submitted:", sub)

    # Poll until terminal.
    for _ in range(600):
        st = jobs.status(job_id)
        if st["status"] in ("done", "failed"):
            print("final status:", st)
            break
        await asyncio.sleep(1)
    else:
        print("timed out waiting")
        return

    print("\nresult:", jobs.result(job_id))
    print("\nget_stats(system*):")
    q = jobs.query_stats(job_id, "board.processor*")
    print("  count:", q["count"])
    for k in list(q["stats"])[:6]:
        print("   ", k, "=", q["stats"][k])


if __name__ == "__main__":
    asyncio.run(main())
