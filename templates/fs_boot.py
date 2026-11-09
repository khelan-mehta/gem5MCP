"""Fixed gem5 full-system boot template (design doc 3.6).

Boots a pre-approved kernel + disk image (a gem5 Resources "boot" workload) on X86 or
RISC-V. Part of the trusted template library - the server only ever passes a validated
asset from its manifest (see server/fullsystem.py), never an arbitrary path.

Run by gem5 as:
    gem5.opt --outdir=<dir> fs_boot.py --isa X86 --resource x86-ubuntu-24.04-boot
"""

import argparse

from gem5.components.boards.riscv_board import RiscvBoard
from gem5.components.boards.x86_board import X86Board
from gem5.components.cachehierarchies.classic.private_l1_private_l2_cache_hierarchy import (
    PrivateL1PrivateL2CacheHierarchy,
)
from gem5.components.memory.single_channel import SingleChannelDDR4_2400
from gem5.components.processors.cpu_types import CPUTypes
from gem5.components.processors.simple_processor import SimpleProcessor
from gem5.isas import ISA
from gem5.resources.resource import obtain_resource
from gem5.simulate.simulator import Simulator

ISA_MAP = {"X86": ISA.X86, "RISCV": ISA.RISCV}


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--isa", required=True, choices=list(ISA_MAP))
    p.add_argument("--resource", required=True, help="gem5 Resources boot-workload id")
    p.add_argument("--max-ticks", type=int, default=None)
    p.add_argument("--mem-size", default="3GB")
    return p.parse_args()


def main():
    args = parse_args()

    cache = PrivateL1PrivateL2CacheHierarchy(
        l1i_size="32kB", l1d_size="32kB", l2_size="512kB"
    )
    memory = SingleChannelDDR4_2400(size=args.mem_size)
    # Atomic CPU keeps the (already slow) boot tractable without KVM.
    processor = SimpleProcessor(
        cpu_type=CPUTypes.ATOMIC, isa=ISA_MAP[args.isa], num_cores=1
    )

    board_cls = X86Board if args.isa == "X86" else RiscvBoard
    board = board_cls(
        clk_freq="3GHz", processor=processor, memory=memory, cache_hierarchy=cache
    )

    # A "boot" workload bundles kernel + disk image + boot command.
    board.set_workload(obtain_resource(args.resource))

    simulator = Simulator(board=board)
    if args.max_ticks:
        simulator.run(max_ticks=args.max_ticks)
    else:
        simulator.run()

    import m5

    print(
        "Exiting @ tick {} because {}".format(
            m5.curTick(), simulator.get_last_exit_event_cause()
        )
    )


if __name__ == "__m5_main__" or __name__ == "__main__":
    main()
