"""Generate a features overview PDF for the gem5 MCP server project."""

from fpdf import FPDF

NAVY = (23, 42, 77)
BLUE = (37, 99, 235)
GREY = (90, 90, 90)
LIGHT = (238, 242, 249)
DARK = (30, 30, 30)


class PDF(FPDF):
    def header(self):
        if self.page_no() == 1:
            return
        self.set_font("Helvetica", "", 8)
        self.set_text_color(*GREY)
        self.cell(0, 8, "gem5 MCP Server - Project Overview", align="L")
        self.cell(0, 8, f"Page {self.page_no()}", align="R")
        self.ln(10)

    def footer(self):
        self.set_y(-12)
        self.set_font("Helvetica", "I", 7)
        self.set_text_color(*GREY)
        self.cell(0, 8, "Generated for Khelan - gem5 v25.1.0.1 / WSL Ubuntu-24.04", align="C")


def h1(pdf, text):
    pdf.ln(2)
    pdf.set_font("Helvetica", "B", 15)
    pdf.set_text_color(*NAVY)
    pdf.multi_cell(0, 8, text)
    pdf.set_draw_color(*BLUE)
    pdf.set_line_width(0.6)
    y = pdf.get_y() + 1
    pdf.line(pdf.l_margin, y, pdf.w - pdf.r_margin, y)
    pdf.ln(4)


def h2(pdf, text):
    pdf.ln(1)
    pdf.set_font("Helvetica", "B", 11.5)
    pdf.set_text_color(*BLUE)
    pdf.multi_cell(0, 6, text)
    pdf.ln(1)


def body(pdf, text):
    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(*DARK)
    pdf.multi_cell(0, 5.4, text)
    pdf.ln(1)


def bullet(pdf, text, bold_lead=None):
    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(*DARK)
    x = pdf.get_x()
    pdf.set_x(x + 3)
    pdf.cell(4, 5.2, chr(149))  # bullet dot
    if bold_lead:
        pdf.set_font("Helvetica", "B", 10)
        lead_w = pdf.get_string_width(bold_lead + " ")
        pdf.cell(lead_w, 5.2, bold_lead + " ")
        pdf.set_font("Helvetica", "", 10)
    pdf.multi_cell(0, 5.2, text)
    pdf.ln(0.5)


def code(pdf, text):
    pdf.set_fill_color(*LIGHT)
    pdf.set_font("Courier", "", 8.5)
    pdf.set_text_color(*DARK)
    pdf.multi_cell(0, 4.8, text, fill=True, border=0)
    pdf.ln(1.5)


def kv_table(pdf, rows, w1=52):
    w2 = pdf.w - pdf.l_margin - pdf.r_margin - w1
    for k, v in rows:
        y0 = pdf.get_y()
        # Reset to the left margin each row (multi_cell leaves X at the last cell's left).
        pdf.set_xy(pdf.l_margin, y0)
        pdf.set_font("Helvetica", "B", 9)
        pdf.set_text_color(*NAVY)
        pdf.multi_cell(w1, 5.2, k, border="LTB", fill=False)
        y1 = pdf.get_y()
        pdf.set_xy(pdf.l_margin + w1, y0)
        pdf.set_font("Helvetica", "", 9)
        pdf.set_text_color(*DARK)
        pdf.multi_cell(w2, 5.2, v, border="RTB")
        if pdf.get_y() < y1:
            pdf.set_y(y1)
    pdf.set_x(pdf.l_margin)
    pdf.ln(2)


pdf = PDF()
pdf.set_auto_page_break(auto=True, margin=16)
pdf.set_margins(18, 16, 18)
pdf.add_page()

# ---- Title block ----
pdf.ln(6)
pdf.set_fill_color(*NAVY)
pdf.rect(0, pdf.get_y(), pdf.w, 40, style="F")
pdf.set_y(pdf.get_y() + 8)
pdf.set_font("Helvetica", "B", 24)
pdf.set_text_color(255, 255, 255)
pdf.cell(0, 12, "gem5 MCP Server", align="C")
pdf.ln(12)
pdf.set_font("Helvetica", "", 12)
pdf.cell(0, 8, "Driving a computer-architecture simulator from an AI assistant", align="C")
pdf.ln(22)

pdf.set_text_color(*DARK)
body(
    pdf,
    "This document explains what you built: a Model Context Protocol (MCP) server that "
    "lets an LLM (via Claude Desktop) run and analyse gem5 simulations. gem5 is a "
    "cycle-level simulator of CPUs, caches and memory used in computer-architecture "
    "research. It has no programming API - you can only launch a compiled binary with a "
    "Python config script - so the server wraps that command-line workflow in clean, "
    "safe tools an AI can call.",
)

h2(pdf, "At a glance")
kv_table(
    pdf,
    [
        ("Simulator", "gem5 v25.1.0.1, built for X86 and RISC-V (SE mode)"),
        ("Runs on", "WSL Ubuntu-24.04, as the non-root 'gem5' user"),
        ("Server", "Python (official MCP SDK), runs on Windows, drives gem5 via WSL"),
        ("Interface", "9 MCP tools exposed to Claude Desktop over stdio"),
        ("Status", "Installed, verified end-to-end, connected and 'running' in Claude"),
    ],
)

# ---- Section 1 ----
h1(pdf, "1.  The problem it solves")
body(
    pdf,
    "gem5 was never designed to be called by software. Every interaction is a long-running "
    "command line that writes results to files. That clashes with how an AI assistant "
    "works. The server bridges the gap by handling four hard properties of gem5:",
)
bullet(pdf, "gem5 has no API - it is a CLI binary fed an arbitrary Python script.", "No API:")
bullet(pdf, "A single run can take minutes to hours, far longer than a tool call should block.", "Slow:")
bullet(pdf, "Config 'scripts' are executable Python - letting a model write them is code execution.", "Unsafe input:")
bullet(pdf, "Output is one giant flat-text stats file, not structured data.", "Messy output:")

# ---- Section 2 ----
h1(pdf, "2.  How it works (architecture)")
code(
    pdf,
    "Claude Desktop  (Home chat)\n"
    "      |  MCP over stdio\n"
    "      v\n"
    "MCP Server  (Windows Python)\n"
    "      |  wsl.exe -u gem5 -- gem5.opt <template>.py --outdir=/runs/{job_id}\n"
    "      v\n"
    "gem5  (compiled inside WSL Ubuntu: X86 + RISCV)\n"
    "      |  writes m5out/stats.txt\n"
    "      v\n"
    "Stats parser -> filtered JSON        SQLite <- job & checkpoint records",
)
body(
    pdf,
    "The server is essentially a subprocess orchestrator. It launches gem5 inside WSL, "
    "gives every job its own output folder, reads the results back from Windows through "
    "the \\\\wsl.localhost\\ path, parses them, and returns clean JSON to Claude.",
)

# ---- Section 3 ----
h1(pdf, "3.  The tools Claude can use")
body(pdf, "Nine tools make up the interface. Claude calls these; you never touch a command line.")
kv_table(
    pdf,
    [
        ("list_available_isas", "Which ISAs are built and runnable (X86, RISCV)."),
        ("get_gem5_version", "The exact pinned gem5 version."),
        ("list_config_templates", "The fixed simulation templates + their parameters."),
        ("submit_simulation", "Launch a run; returns a job_id immediately (non-blocking)."),
        ("get_job_status", "queued / running / done / failed, plus queue position."),
        ("get_job_result", "Headline summary (cycles, instructions, sim time)."),
        ("get_stats", "Filtered query of the full stats, e.g. pattern 'system.cpu*'."),
        ("list_checkpoints", "Checkpoints produced by finished jobs."),
        ("restore_from_checkpoint", "Start a new run resuming from a saved checkpoint."),
    ],
    w1=48,
)

h2(pdf, "The job pattern")
body(
    pdf,
    "Because simulations are slow, submit_simulation never blocks. It returns a job_id "
    "right away and runs gem5 in the background. Claude then polls get_job_status until "
    "the run is 'done', and finally pulls results with get_job_result or get_stats. This "
    "keeps the conversation responsive even for long runs.",
)

# ---- Section 4 ----
h1(pdf, "4.  Simulation templates")
body(
    pdf,
    "Instead of accepting raw Python, the server ships a small library of fixed, trusted "
    "config scripts. You pick one and supply validated parameters:",
)
bullet(pdf, "Syscall-emulation run of one binary on a single CPU core with L1/L2 caches.", "se_single_core:")
bullet(pdf, "Same, but on N identical cores (2-16), each with its own cache hierarchy.", "se_multi_core:")
pdf.ln(1)
body(pdf, "Parameters you can set (all validated before anything runs):")
bullet(pdf, "X86 or RISCV.", "isa:")
bullet(pdf, "atomic / timing / o3 / minor - from fast-and-simple to detailed out-of-order.", "cpu:")
bullet(pdf, "l1i, l1d and l2 sizes (e.g. 32kB, 256kB, 1MB).", "caches:")
bullet(pdf, "memory size, clock frequency, and an optional instruction-count limit.", "system:")

# ---- Section 5 ----
h1(pdf, "5.  Built-in safety controls")
body(
    pdf,
    "The design deliberately limits what an AI can do with a native simulator on your machine:",
)
bullet(pdf, "The model never runs raw Python - only fixed templates, with parameters checked by a strict schema.", "No code execution:")
bullet(pdf, "At most 4 simulations run at once; the rest queue, so gem5 can't starve your PC.", "Bounded pool:")
bullet(pdf, "A run is killed if it exceeds 512 MB of output or a 2-hour wall-clock limit.", "Caps:")
bullet(pdf, "Verbose trace flags are off by default; only a small safe whitelist is allowed.", "Debug flags:")
bullet(pdf, "gem5 runs as a non-root Linux user inside WSL, isolated from Windows.", "Isolation:")
bullet(pdf, "Results are filtered before return - a full stats file (tens of thousands of lines) never floods the chat.", "Context safety:")

# ---- Section 6 ----
h1(pdf, "6.  How you use it")
h2(pdf, "From Claude Desktop (the main way)")
body(pdf, "In a normal Home chat, just ask in plain English. For example:")
code(
    pdf,
    "- \"list the available gem5 ISAs\"\n"
    "- \"run a hello-world simulation on X86 (se_single_core,\n"
    "   binary /home/gem5/testbins/hello_x86, cpu timing) and show the CPI\"\n"
    "- \"run the same binary with an o3 CPU and compare the cycle counts\"",
)
h2(pdf, "Standalone (without the AI)")
body(pdf, "gem5 is a full, normal install you can drive yourself from a WSL shell:")
code(
    pdf,
    "wsl -d Ubuntu-24.04 -u gem5\n"
    "cd ~/gem5\n"
    "build/X86/gem5.opt configs/deprecated/example/se.py \\\n"
    "    --cmd=/home/gem5/testbins/hello_x86",
)

# ---- Section 7 ----
h1(pdf, "7.  Project layout")
kv_table(
    pdf,
    [
        ("server/main.py", "The MCP server and its 9 tools."),
        ("server/jobs.py", "Async job queue, launch, size/time caps."),
        ("server/gem5_runner.py", "Builds the wsl.exe + gem5.opt command."),
        ("server/templates.py", "Validated template registry (parameter schemas)."),
        ("server/stats.py", "stats.txt -> dict, with glob/regex filtering."),
        ("server/db.py", "SQLite store for jobs and checkpoints."),
        ("templates/*.py", "The fixed, trusted gem5 config scripts."),
        ("README.md", "Setup and Claude Desktop wiring instructions."),
    ],
    w1=52,
)

h2(pdf, "Verified working")
body(
    pdf,
    "Both ISAs were tested end-to-end through the server. Example X86 hello-world result: "
    "131,262 instructions, 264,820 cycles, CPI ~2.02, out of 1,139 parsed statistics - "
    "all returned to the caller as clean JSON.",
)

pdf.ln(2)
pdf.set_font("Helvetica", "I", 9)
pdf.set_text_color(*GREY)
pdf.multi_cell(
    0, 5,
    "Scope note: this version focuses on SE (syscall-emulation) mode, which only needs a "
    "compiled binary. Full-system mode (booting a real OS with disk images and kernels) is "
    "intentionally left for a future version.",
)

out = r"C:\Users\NG\Desktop\gem5mcp\gem5-mcp-overview.pdf"
pdf.output(out)
print("wrote", out)
