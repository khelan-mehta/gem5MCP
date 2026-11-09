"""Fixed gem5 SE-mode template: one CPU core, classic L1/L2 caches, richly tunable.

Part of the *trusted* template library. The MCP server never edits it; it only passes
validated ``--flags`` (see server/templates.py). Exposes a broad hardware design space:
CPU pipeline (o3 width/ROB/LSQ/regs, branch predictor), caches (size, associativity,
replacement policy, prefetcher, MSHRs, line size) and memory (DRAM technology).

Defaults are chosen to reproduce the previous stdlib behaviour exactly, so existing
experiments are unaffected; the new knobs only change things when set.
"""

import argparse

import m5
from gem5.components.boards.abstract_board import AbstractBoard
from gem5.components.boards.simple_board import SimpleBoard
from gem5.components.cachehierarchies.classic.caches.l1dcache import L1DCache
from gem5.components.cachehierarchies.classic.caches.l1icache import L1ICache
from gem5.components.cachehierarchies.classic.caches.l2cache import L2Cache
from gem5.components.cachehierarchies.classic.private_l1_private_l2_cache_hierarchy import (
    PrivateL1PrivateL2CacheHierarchy,
)
from gem5.components.memory import single_channel, multi_channel
from gem5.components.processors.cpu_types import CPUTypes
from gem5.components.processors.simple_processor import SimpleProcessor
from gem5.isas import ISA
from gem5.resources.resource import BinaryResource
from gem5.simulate.simulator import Simulator
from gem5.utils.override import overrides
from m5.objects import (
    L2XBar,
    LRURP, FIFORP, RandomRP, MRURP, NRURP, LIPRP, TreePLRURP, RRIPRP, BRRIPRP,
    SecondChanceRP,
    StridePrefetcher, TaggedPrefetcher, AMPMPrefetcher, BOPPrefetcher, DCPTPrefetcher,
    LocalBP, BiModeBP, GshareBP, TAGE, TAGE_SC_L_8KB,
)
from m5.params import NULL

CPU_MAP = {
    "atomic": CPUTypes.ATOMIC,
    "timing": CPUTypes.TIMING,
    "o3": CPUTypes.O3,
    "minor": CPUTypes.MINOR,
}
ISA_MAP = {"X86": ISA.X86, "RISCV": ISA.RISCV, "ARM": ISA.ARM}

REPL_MAP = {
    "lru": LRURP, "fifo": FIFORP, "random": RandomRP, "mru": MRURP, "nru": NRURP,
    "lip": LIPRP, "treeplru": TreePLRURP, "rrip": RRIPRP, "brrip": BRRIPRP,
    "secondchance": SecondChanceRP,
}
PREFETCH_MAP = {
    "none": None, "stride": StridePrefetcher, "tagged": TaggedPrefetcher,
    "ampm": AMPMPrefetcher, "bop": BOPPrefetcher, "dcpt": DCPTPrefetcher,
}
BP_MAP = {
    "local": LocalBP, "bimode": BiModeBP, "gshare": GshareBP,
    "tage": TAGE, "tage_sc_l": TAGE_SC_L_8KB,
}

# DRAM technology -> (module, factory function name). Single & dual channel variants.
DRAM_MAP = {
    "ddr3_1600": (single_channel, "SingleChannelDDR3_1600"),
    "ddr3_2133": (single_channel, "SingleChannelDDR3_2133"),
    "ddr4_2400": (single_channel, "SingleChannelDDR4_2400"),
    "lpddr3_1600": (single_channel, "SingleChannelLPDDR3_1600"),
    "hbm": (single_channel, "SingleChannelHBM"),
    "ddr3_1600_dual": (multi_channel, "DualChannelDDR3_1600"),
    "ddr4_2400_dual": (multi_channel, "DualChannelDDR4_2400"),
    "lpddr3_1600_dual": (multi_channel, "DualChannelLPDDR3_1600"),
}


class ConfigurableCacheHierarchy(PrivateL1PrivateL2CacheHierarchy):
    """Private-L1/private-L2 topology with associativity, replacement policy,
    prefetcher and MSHRs exposed (the stdlib class hardcodes them). Byte-for-byte
    identical wiring; only the cache object parameters are made configurable.
    """

    def __init__(self, l1d_size, l1i_size, l2_size,
                 l1d_assoc=8, l1i_assoc=8, l2_assoc=4,
                 l1d_repl="lru", l1i_repl="lru", l2_repl="lru",
                 prefetcher="stride", l1d_mshrs=16, membus=None):
        super().__init__(l1d_size=l1d_size, l1i_size=l1i_size, l2_size=l2_size,
                         membus=membus)
        self._l1d_assoc, self._l1i_assoc, self._l2_assoc = l1d_assoc, l1i_assoc, l2_assoc
        self._l1d_repl, self._l1i_repl, self._l2_repl = l1d_repl, l1i_repl, l2_repl
        self._prefetcher = prefetcher
        self._l1d_mshrs = l1d_mshrs

    @overrides(PrivateL1PrivateL2CacheHierarchy)
    def incorporate_cache(self, board: AbstractBoard) -> None:
        board.connect_system_port(self.membus.cpu_side_ports)
        for _, port in board.get_mem_ports():
            self.membus.mem_side_ports = port

        self.l2buses = [
            L2XBar() for _ in range(board.get_processor().get_num_cores())
        ]
        pf_cls = PREFETCH_MAP.get(self._prefetcher, StridePrefetcher) or StridePrefetcher
        for i, cpu in enumerate(board.get_processor().get_cores()):
            l2 = L2Cache(size=self._l2_size, assoc=self._l2_assoc)
            l1i = L1ICache(size=self._l1i_size, assoc=self._l1i_assoc)
            l1d = L1DCache(size=self._l1d_size, assoc=self._l1d_assoc,
                           mshrs=self._l1d_mshrs, PrefetcherCls=pf_cls)
            # Replacement policies.
            l2.replacement_policy = REPL_MAP[self._l2_repl]()
            l1i.replacement_policy = REPL_MAP[self._l1i_repl]()
            l1d.replacement_policy = REPL_MAP[self._l1d_repl]()
            # If prefetcher "none", detach it (stdlib always attaches one; the
            # underlying Cache param defaults to NULL = no prefetcher).
            if self._prefetcher == "none":
                l1d.prefetcher = NULL

            l2_node = self.add_root_child(f"l2-cache-{i}", l2)
            l1i_node = l2_node.add_child(f"l1i-cache-{i}", l1i)
            l1d_node = l2_node.add_child(f"l1d-cache-{i}", l1d)

            self.l2buses[i].mem_side_ports = l2_node.cache.cpu_side
            self.membus.cpu_side_ports = l2_node.cache.mem_side
            l1i_node.cache.mem_side = self.l2buses[i].cpu_side_ports
            l1d_node.cache.mem_side = self.l2buses[i].cpu_side_ports
            cpu.connect_icache(l1i_node.cache.cpu_side)
            cpu.connect_dcache(l1d_node.cache.cpu_side)
            self._connect_table_walker(i, cpu)

            if board.get_processor().get_isa() == ISA.X86:
                cpu.connect_interrupt(
                    self.membus.mem_side_ports, self.membus.cpu_side_ports)
            else:
                cpu.connect_interrupt()

        if board.has_coherent_io():
            self._setup_io_cache(board)


def make_memory(args):
    module, fname = DRAM_MAP[args.dram_type]
    return getattr(module, fname)(size=args.mem_size)


def tune_o3(processor, args):
    """Apply out-of-order microarchitecture knobs (only meaningful for the o3 CPU)."""
    if args.cpu != "o3":
        return
    for core in processor.get_cores():
        c = core.core
        pairs = {
            "numROBEntries": args.rob_entries,
            "fetchWidth": args.fetch_width,
            "decodeWidth": args.decode_width,
            "issueWidth": args.issue_width,
            "dispatchWidth": args.dispatch_width,
            "commitWidth": args.commit_width,
            "LQEntries": args.lq_entries,
            "SQEntries": args.sq_entries,
            "numPhysIntRegs": args.phys_int_regs,
            "numPhysFloatRegs": args.phys_float_regs,
        }
        for attr, val in pairs.items():
            if val is not None:
                try:
                    setattr(c, attr, val)
                except Exception:
                    pass
        if args.branch_pred != "default":
            try:
                c.branchPred = BP_MAP[args.branch_pred]()
            except Exception:
                pass


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--isa", required=True, choices=list(ISA_MAP))
    p.add_argument("--binary", required=True)
    p.add_argument("--cpu", default="timing", choices=list(CPU_MAP))
    p.add_argument("--mem-size", default="512MB")
    p.add_argument("--clock", default="1GHz")
    # Caches
    p.add_argument("--l1i-size", default="32kB")
    p.add_argument("--l1d-size", default="32kB")
    p.add_argument("--l2-size", default="256kB")
    p.add_argument("--l1d-assoc", type=int, default=8)
    p.add_argument("--l1i-assoc", type=int, default=8)
    p.add_argument("--l2-assoc", type=int, default=4)
    p.add_argument("--l1d-repl", default="lru", choices=list(REPL_MAP))
    p.add_argument("--l1i-repl", default="lru", choices=list(REPL_MAP))
    p.add_argument("--l2-repl", default="lru", choices=list(REPL_MAP))
    p.add_argument("--prefetcher", default="stride", choices=list(PREFETCH_MAP))
    p.add_argument("--l1d-mshrs", type=int, default=16)
    p.add_argument("--cache-line-size", type=int, default=64)
    # Memory
    p.add_argument("--dram-type", default="ddr3_1600", choices=list(DRAM_MAP))
    # O3 pipeline (None => leave gem5 default)
    p.add_argument("--rob-entries", type=int, default=None)
    p.add_argument("--fetch-width", type=int, default=None)
    p.add_argument("--decode-width", type=int, default=None)
    p.add_argument("--issue-width", type=int, default=None)
    p.add_argument("--dispatch-width", type=int, default=None)
    p.add_argument("--commit-width", type=int, default=None)
    p.add_argument("--lq-entries", type=int, default=None)
    p.add_argument("--sq-entries", type=int, default=None)
    p.add_argument("--phys-int-regs", type=int, default=None)
    p.add_argument("--phys-float-regs", type=int, default=None)
    p.add_argument("--branch-pred", default="default")
    # Misc
    p.add_argument("--max-insts", type=int, default=None)
    p.add_argument("--take-checkpoint", action="store_true")
    p.add_argument("--restore-checkpoint-dir", default=None)
    p.add_argument("--num-cores", type=int, default=1)  # accepted for uniformity
    return p.parse_args()


def build_board(args):
    cache = ConfigurableCacheHierarchy(
        l1i_size=args.l1i_size, l1d_size=args.l1d_size, l2_size=args.l2_size,
        l1d_assoc=args.l1d_assoc, l1i_assoc=args.l1i_assoc, l2_assoc=args.l2_assoc,
        l1d_repl=args.l1d_repl, l1i_repl=args.l1i_repl, l2_repl=args.l2_repl,
        prefetcher=args.prefetcher, l1d_mshrs=args.l1d_mshrs,
    )
    memory = make_memory(args)
    processor = SimpleProcessor(
        cpu_type=CPU_MAP[args.cpu], isa=ISA_MAP[args.isa], num_cores=args.num_cores
    )
    tune_o3(processor, args)
    board = SimpleBoard(
        clk_freq=args.clock, processor=processor, memory=memory, cache_hierarchy=cache
    )
    board.cache_line_size = args.cache_line_size
    board.set_se_binary_workload(BinaryResource(local_path=args.binary))

    if args.max_insts is not None:
        for core in processor.get_cores():
            try:
                core.core.max_insts_any_thread = args.max_insts
            except Exception:
                pass
    return board


def main():
    args = parse_args()
    board = build_board(args)

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

    print("Exiting @ tick {} because {}".format(
        m5.curTick(), simulator.get_last_exit_event_cause()))


if __name__ == "__m5_main__" or __name__ == "__main__":
    main()
