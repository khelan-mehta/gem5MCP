"""Generate report figures from measured gem5-MCP results (data/gem5mcp.db)."""

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

OUT = "figs/"
plt.rcParams.update({
    "font.family": "DejaVu Sans", "font.size": 10, "axes.titlesize": 11,
    "axes.grid": True, "grid.alpha": 0.45, "figure.dpi": 150,
})


def line(fname, x, y, title, xlabel, ylabel, color, xticks=None, annotate="{:.3f}"):
    fig, ax = plt.subplots(figsize=(6.4, 3.4))
    ax.plot(x, y, "-o", color=color, lw=1.8, ms=6)
    for xi, yi in zip(x, y):
        ax.annotate(annotate.format(yi), (xi, yi), textcoords="offset points",
                    xytext=(0, 7), ha="center", fontsize=8.5)
    ax.set_title(title); ax.set_xlabel(xlabel); ax.set_ylabel(ylabel)
    if xticks is not None:
        ax.set_xticks(x); ax.set_xticklabels(xticks)
    ax.margins(y=0.18)
    fig.tight_layout(); fig.savefig(OUT + fname); plt.close(fig)


def bars(fname, labels, y, title, ylabel, colors, fmt="{:.3f}"):
    fig, ax = plt.subplots(figsize=(6.4, 3.4))
    b = ax.bar(labels, y, color=colors, width=0.55, edgecolor="black", lw=0.6)
    for r, v in zip(b, y):
        ax.annotate(fmt.format(v), (r.get_x() + r.get_width() / 2, v),
                    textcoords="offset points", xytext=(0, 3), ha="center", fontsize=8.5)
    ax.set_title(title); ax.set_ylabel(ylabel); ax.grid(axis="x", visible=False)
    ax.margins(y=0.15)
    fig.tight_layout(); fig.savefig(OUT + fname); plt.close(fig)


def grouped(fname, groups, series, title, ylabel, fmt="{:.2f}"):
    import numpy as np
    fig, ax = plt.subplots(figsize=(6.4, 3.4))
    n = len(series); w = 0.8 / n; x = np.arange(len(groups))
    cols = ["#1f4e9c", "#d0342c", "#2e8b57"]
    for i, (name, vals) in enumerate(series.items()):
        b = ax.bar(x + (i - (n - 1) / 2) * w, vals, w, label=name, color=cols[i],
                   edgecolor="black", lw=0.5)
        for r, v in zip(b, vals):
            ax.annotate(fmt.format(v), (r.get_x() + r.get_width() / 2, v),
                        textcoords="offset points", xytext=(0, 2), ha="center", fontsize=7.5)
    ax.set_xticks(x); ax.set_xticklabels(groups)
    ax.set_title(title); ax.set_ylabel(ylabel); ax.legend(fontsize=8)
    ax.grid(axis="x", visible=False); ax.margins(y=0.15)
    fig.tight_layout(); fig.savefig(OUT + fname); plt.close(fig)


# Exp 1 - matmul L1D capacity (cmp_f36ae3c538)
cap = [4, 32, 64]
line("e1_ipc.png", cap, [0.4508, 0.5989, 0.6145], "Impact of L1D Cache Size on IPC (Matrix Mult.)",
     "L1 Data Cache Size (kB)", "Instructions Per Cycle (IPC)", "blue")
line("e1_miss.png", cap, [12.01, 0.90, 0.02], "Impact of L1D Cache Size on Miss Rate (Matrix Mult.)",
     "L1 Data Cache Size (kB)", "L1D Miss Rate (%)", "red", annotate="{:.2f}%")

# Exp 2 - AES L1D capacity (cmp_0d26138740)
line("e2_ipc.png", cap, [0.5456, 0.5456, 0.5456], "Impact of L1D Cache Size on IPC (AES SubBytes)",
     "L1 Data Cache Size (kB)", "Instructions Per Cycle (IPC)", "blue")

# Exp 3 - associativity @16kB (cmp_e2b870510f)
line("e3_ipc.png", [1, 2, 8], [0.4341, 0.4452, 0.5988], "L1D Associativity vs. IPC (16kB, Matrix Mult.)",
     "Associativity (ways)", "Instructions Per Cycle (IPC)", "blue",
     xticks=["1-way (DM)", "2-way", "8-way"])
line("e3_miss.png", [1, 2, 8], [12.90, 12.87, 0.90], "L1D Associativity vs. Miss Rate (16kB)",
     "Associativity (ways)", "L1D Miss Rate (%)", "red",
     xticks=["1-way (DM)", "2-way", "8-way"], annotate="{:.2f}%")

# Exp 4 - CPU models (cmp_1ed7ed4d89 ILP kernel, cmp_547eb2ee82 branch kernel)
bars("e4_cpu.png", ["Atomic", "Timing\n(in-order)", "Minor\n(in-order pipe)", "O3\n(out-of-order)"],
     [0.4233, 0.6657, 0.2739, 0.9814], "CPU Model vs. IPC (Branch-heavy kernel, X86)", "IPC",
     ["#9aa5b1", "#5b8def", "#f0a202", "#2e8b57"])
bars("e4_ilp.png", ["Atomic", "Timing (in-order)", "O3 (out-of-order)"],
     [0.521, 0.7456, 3.7315], "CPU Model vs. IPC (4-chain ILP kernel, X86)", "IPC",
     ["#9aa5b1", "#5b8def", "#2e8b57"])

# Exp 5 - O3 microarchitecture sweeps
line("e5_rob.png", [16, 32, 64, 128, 192], [0.6435, 0.8443, 0.9611, 0.9803, 0.9814],
     "Reorder Buffer Size vs. IPC (O3, X86)", "ROB entries", "IPC", "purple",
     xticks=["16", "32", "64", "128", "192"])
line("e5_fetch.png", [1, 2, 4, 8], [0.383, 0.6174, 0.8239, 0.9814],
     "Fetch Width vs. IPC (O3, X86)", "Fetch width (instr/cycle)", "IPC", "darkorange",
     xticks=["1", "2", "4", "8"])
line("e5_issue.png", [1, 2, 8], [0.5312, 1.0546, 2.6609],
     "Issue Width vs. IPC (O3, X86, Matrix Mult.)", "Issue width (instr/cycle)", "IPC", "teal",
     xticks=["1", "2", "8"])

# Exp 6 - ISA comparison
grouped("e6_isa.png", ["fib(30)\nTimingSimpleCPU", "Branch kernel\nO3CPU"],
        {"X86": [0.6171, 0.9814], "RISC-V": [0.7230, 1.0244], "ARM": [0.8244, 1.1587]},
        "IPC across ISAs for identical C source", "IPC")
grouped("e6_matmul.png", ["Atomic", "Timing", "O3"],
        {"X86": [11.65, 7.81, 1.90], "RISC-V": [8.38, 10.83, 2.36]},
        "Matrix Mult. (100x100): Simulated cycles (millions)", "Cycles (x10^6)")

# Exp 7 - memory latency ladder (cmp_e5d6354b92)
bars("e7_ladder.png", ["L1-resident\n(16 KB)", "L2-resident\n(128 KB)", "DRAM\n(8 MB)"],
     [1.349, 2.164, 5.702], "Pointer-chase CPI vs. Working-set Size (X86, Timing)", "CPI",
     ["#2e8b57", "#f0a202", "#d0342c"])
print("ok")
