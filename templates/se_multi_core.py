"""Fixed gem5 SE-mode template: N identical CPU cores, private L1/L2 per core.

Part of the trusted template library (see server/templates.py). Run by gem5 as:

    gem5.opt --outdir=<dir> se_multi_core.py --isa X86 --binary <elf> --num-cores 4 ...
"""

import argparse

import m5
from gem5.components.boards.simple_board import SimpleBoard
from gem5.components.cachehierarchies.classic.private_l1_private_l2_cache_hierarchy import (
    PrivateL1PrivateL2CacheHierarchy,
)
from gem5.components.memory.single_channel import SingleChannelDDR3_1600
from gem5.components.processors.cpu_types import CPUTypes
from gem5.components.processors.simple_processor import SimpleProcessor
from gem5.isas import ISA
from gem5.resources.resource import BinaryResource
from gem5.simulate.simulator import Simulator

CPU_MAP = {
    "atomic": CPUTypes.ATOMIC,
    "timing": CPUTypes.TIMING,
    "o3": CPUTypes.O3,
    "minor": CPUTypes.MINOR,
}
ISA_MAP = {"X86": ISA.X86, "RISCV": ISA.RISCV, "ARM": ISA.ARM}


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--isa", required=True, choices=list(ISA_MAP))
    p.add_argument("--binary", required=True)
    p.add_argument("--cpu", default="timing", choices=list(CPU_MAP))
    p.add_argument("--num-cores", type=int, default=2)
    p.add_argument("--mem-size", default="512MB")
    p.add_argument("--clock", default="1GHz")
    p.add_argument("--l1i-size", default="32kB")
    p.add_argument("--l1d-size", default="32kB")
    p.add_argument("--l2-size", default="256kB")
    p.add_argument("--l1d-assoc", type=int, default=8)
    p.add_argument("--l1i-assoc", type=int, default=8)
    p.add_argument("--l2-assoc", type=int, default=4)
    p.add_argument("--max-insts", type=int, default=None)
    p.add_argument("--take-checkpoint", action="store_true")
    p.add_argument("--restore-checkpoint-dir", default=None)
    return p.parse_args()


def main():
    args = parse_args()
    cache = PrivateL1PrivateL2CacheHierarchy(
        l1i_size=args.l1i_size, l1d_size=args.l1d_size, l2_size=args.l2_size
    )
    memory = SingleChannelDDR3_1600(size=args.mem_size)
    processor = SimpleProcessor(
        cpu_type=CPU_MAP[args.cpu], isa=ISA_MAP[args.isa], num_cores=args.num_cores
    )
    board = SimpleBoard(
        clk_freq=args.clock, processor=processor, memory=memory, cache_hierarchy=cache
    )
    board.set_se_binary_workload(BinaryResource(local_path=args.binary))

    if args.max_insts is not None:
        for core in processor.get_cores():
            try:
                core.core.max_insts_any_thread = args.max_insts
            except Exception:
                pass

    sim_kwargs = {"board": board}
    if args.restore_checkpoint_dir:
        from pathlib import Path

        sim_kwargs["checkpoint_path"] = Path(args.restore_checkpoint_dir)

    simulator = Simulator(**sim_kwargs)
    simulator.run()

    if args.take_checkpoint:
        from pathlib import Path

        cpt_dir = Path(m5.options.outdir) / f"cpt.{m5.curTick()}"
        simulator.save_checkpoint(cpt_dir)
        print(f"info: checkpoint saved to {cpt_dir}")

    print(
        "Exiting @ tick {} because {}".format(
            m5.curTick(), simulator.get_last_exit_event_cause()
        )
    )


if __name__ == "__m5_main__" or __name__ == "__main__":
    main()
