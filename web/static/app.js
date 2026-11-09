"use strict";

const FIB_EXAMPLE = `#include <stdio.h>

// Recursive Fibonacci - a small CPU-bound workload that stresses
// call/return, branches and the caches differently on each ISA.
long fib(int n) {
    if (n < 2) return n;
    return fib(n - 1) + fib(n - 2);
}

int main(void) {
    int n = 30;
    printf("fib(%d) = %ld\\n", n, fib(n));
    return 0;
}
`;

const PALETTE = ["#ff2d95", "#0a0a0a", "#8a8a8a", "#35d0ff", "#ffc400"];
let charts = {};
let pollTimer = null;
let META = { isas: [], cpus: [] };

const $ = (id) => document.getElementById(id);

// Neobrutalist chart defaults: black text, chunky feel on a light panel.
if (window.Chart) {
  Chart.defaults.color = "#0a0a0a";
  Chart.defaults.borderColor = "rgba(0,0,0,0.12)";
  Chart.defaults.font.family =
    "'Space Grotesk', Inter, -apple-system, 'Segoe UI', Roboto, Arial, sans-serif";
  Chart.defaults.font.weight = "600";
}

async function init() {
  try {
    META = await (await fetch("/api/meta")).json();
    $("meta-badge").textContent = "gem5 " + META.gem5_version + " · ready";
    $("meta-badge").classList.add("done");

    fill($("language"), META.languages, (l) => (l === "cpp" ? "C++" : l.toUpperCase()));
    fill($("cpu"), META.cpus, (c) => c, "timing");
    fill($("val-isa"), META.isas, (i) => i);

    const isaList = $("isa-list");
    META.isas.forEach((isa) => {
      const chip = document.createElement("div");
      chip.className = "chip on"; chip.dataset.isa = isa;
      chip.innerHTML = `<span>${isa}</span>`;
      chip.onclick = () => chip.classList.toggle("on");
      isaList.appendChild(chip);
    });

    // Advanced-knob dropdowns
    fill($("k_l1d_repl"), ["", ...(META.repl_policies || [])], (x) => x || "default");
    fill($("k_l2_repl"), ["", ...(META.repl_policies || [])], (x) => x || "default");
    fill($("k_prefetcher"), ["", ...(META.prefetchers || [])], (x) => x || "default");
    fill($("k_cache_line_size"), ["", ...(META.line_sizes || [])], (x) => x || "default");
    fill($("k_dram_type"), ["", ...(META.dram_types || [])], (x) => x || "default");
  } catch (e) {
    $("meta-badge").textContent = "backend offline";
    $("meta-badge").classList.add("failed");
  }

  // Tabs
  document.querySelectorAll(".tab").forEach((t) => {
    t.onclick = () => {
      document.querySelectorAll(".tab").forEach((x) => x.classList.remove("on"));
      t.classList.add("on");
      document.querySelectorAll(".view").forEach((v) => v.classList.add("hidden"));
      $("view-" + t.dataset.view).classList.remove("hidden");
      if (t.dataset.view === "labs") loadLabs();
    };
  });

  $("example-btn").onclick = () => { $("source").value = FIB_EXAMPLE; };
  $("run-btn").onclick = runComparison;
  $("source").value = FIB_EXAMPLE;
  const resetBtn = $("reset-adv");
  if (resetBtn) resetBtn.onclick = () => {
    document.querySelectorAll("#adv input").forEach((i) => (i.value = ""));
    document.querySelectorAll("#adv select").forEach((s) => (s.selectedIndex = 0));
  };

  document.querySelectorAll("[data-val]").forEach((b) => {
    b.onclick = () => runValidation(b.dataset.val);
  });
}

function fill(sel, items, label, selected) {
  items.forEach((it) => {
    const o = document.createElement("option");
    o.value = it; o.textContent = label(it);
    if (it === selected) o.selected = true;
    sel.appendChild(o);
  });
}

function selectedIsas() {
  return [...document.querySelectorAll(".chip.on")].map((c) => c.dataset.isa);
}

// String knobs (kept as-is) and numeric knobs (parsed to int) collected from Advanced.
const STR_KNOBS = ["l1d_size", "l2_size", "mem_size", "clock",
                   "l1d_repl", "l2_repl", "prefetcher", "dram_type"];
const NUM_KNOBS = ["l1d_assoc", "l2_assoc", "l1d_mshrs", "cache_line_size",
                   "rob_entries", "fetch_width", "issue_width", "commit_width",
                   "lq_entries", "sq_entries", "phys_int_regs"];

function collectParams() {
  const p = {};
  STR_KNOBS.forEach((k) => {
    const el = $("k_" + k);
    if (el && el.value.trim()) p[k] = el.value.trim();
  });
  NUM_KNOBS.forEach((k) => {
    const el = $("k_" + k);
    if (el && el.value.trim()) p[k] = parseInt(el.value, 10);
  });
  return p;
}

// ---- Compare ----
async function runComparison() {
  const source = $("source").value.trim();
  const isas = selectedIsas();
  const msg = $("submit-msg"); msg.className = "msg";
  if (!source) return fail(msg, "Write some code first.");
  if (!isas.length) return fail(msg, "Pick at least one architecture.");
  const params = collectParams();
  if ($("cpu").value !== "o3" &&
      ["rob_entries","fetch_width","issue_width","commit_width","lq_entries","sq_entries","phys_int_regs"]
        .some((k) => k in params)) {
    return fail(msg, "Pipeline knobs (ROB/width/queues) require CPU model = o3.");
  }
  $("run-btn").disabled = true;
  msg.textContent = "Compiling and launching simulations…";
  await launch("/api/compare", {
    source, isas, language: $("language").value, cpu: $("cpu").value, params,
  }, msg, $("run-btn"));
}

// ---- Validation ----
async function runValidation(kind) {
  const msg = $("val-msg"); msg.className = "msg";
  msg.textContent = "Launching validation…";
  await launch("/api/validation", { kind, isa: $("val-isa").value }, msg, null);
}

// ---- Labs ----
async function loadLabs() {
  const list = $("lab-list");
  if (list.dataset.loaded) return;
  const { labs } = await (await fetch("/api/labs")).json();
  list.innerHTML = "";
  labs.forEach((lab) => {
    const el = document.createElement("div");
    el.className = "lab-card";
    el.innerHTML = `<span class="lvl ${lab.level}">${lab.level}</span>
      <div class="lt">${lab.title}</div><div class="lo">${lab.objective}</div>`;
    el.onclick = () => openLab(lab.id);
    list.appendChild(el);
  });
  list.dataset.loaded = "1";
}

async function openLab(id) {
  const lab = await (await fetch("/api/lab/" + id)).json();
  const d = $("lab-detail");
  d.classList.remove("hidden");
  d.innerHTML = `
    <h3>${lab.title}</h3>
    <p><b>Objective.</b> ${lab.objective}</p>
    <p><b>Background.</b> ${lab.background}</p>
    <p><b>What to look for.</b> ${lab.look_for}</p>
    <p><b>Questions</b></p>
    <ol>${(lab.questions || []).map((q) => `<li>${q}</li>`).join("")}</ol>
    <button class="primary" id="lab-run-btn" type="button">Run this lab ▶</button>
    <div id="lab-msg" class="msg"></div>`;
  $("lab-run-btn").onclick = async () => {
    const msg = $("lab-msg"); msg.className = "msg"; msg.textContent = "Launching lab…";
    $("lab-run-btn").disabled = true;
    await launch("/api/lab/" + id + "/run", null, msg, $("lab-run-btn"), "POST");
  };
}

// ---- shared launch + poll ----
async function launch(url, body, msg, btn, method = "POST") {
  try {
    const opts = { method };
    if (body) { opts.headers = { "Content-Type": "application/json" }; opts.body = JSON.stringify(body); }
    const res = await fetch(url, opts);
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "request failed");
    msg.textContent = "";
    if (data.errors && data.errors.length) {
      fail(msg, "Some builds failed: " + data.errors.map((e) => e.label + " (" + (e.error||"").split("\\n")[0] + ")").join("; "));
    }
    $("results-title").textContent = data.title || "Results";
    startPolling(data.comparison_id);
  } catch (e) {
    fail(msg, "Error: " + e.message);
    if (btn) btn.disabled = false;
  }
}

function startPolling(id) {
  if (pollTimer) clearInterval(pollTimer);
  const tick = async () => {
    const data = await (await fetch("/api/comparison/" + id)).json();
    render(data);
    if (data.overall_status !== "running") {
      clearInterval(pollTimer); pollTimer = null;
      document.querySelectorAll(".primary").forEach((b) => (b.disabled = false));
    }
  };
  tick();
  pollTimer = setInterval(tick, 2000);
}

function render(data) {
  const p = data.progress, badge = $("progress");
  badge.className = "badge " + data.overall_status;
  badge.textContent = `${p.done + p.failed}/${p.total} finished`;

  // Verdict (validation)
  const vbox = $("verdict");
  if (data.verdict && data.verdict.ready) {
    const v = data.verdict;
    vbox.className = "verdict " + (v.passed ? "pass" : "warn");
    vbox.innerHTML =
      `<div class="vhead">${v.passed ? "✓ PASSED" : "⚠ NOT CONFIRMED"}</div>` +
      (v.checks || []).map((c) =>
        `<div class="vcheck ${c.pass ? "ok" : "no"}">${c.pass ? "✓" : "✗"} ${c.name}
         <span class="vd">${c.detail}</span></div>`).join("") +
      `<p class="vexp">${v.explanation || ""}</p>`;
    vbox.classList.remove("hidden");
  } else {
    vbox.classList.add("hidden");
  }

  const cards = $("status-cards"); cards.innerHTML = "";
  data.results.forEach((r) => {
    const el = document.createElement("div");
    el.className = "scard " + r.status;
    el.innerHTML = `<div class="isa">${r.label || r.isa}</div>
      <div class="st">${statusText(r)}</div>`;
    cards.appendChild(el);
  });

  const done = data.results.filter((r) => r.status === "done" && r.metrics);
  if (done.length) {
    drawCharts(done); drawTable(done);
    $("charts").classList.remove("hidden");
    $("table-wrap").classList.remove("hidden");
  }
}

function statusText(r) {
  if (r.status === "done") {
    const m = r.metrics || {};
    return `${fmt(m.instructions)} insts · ${fmt(m.cycles)} cyc · CPI ${round(m.cpi)}`;
  }
  if (r.status === "failed") return "failed" + (r.error ? " · " + r.error.split("\\n")[0] : "");
  return r.status + "…";
}

function bar(canvasId, title, labels, values, unit) {
  const ctx = $(canvasId);
  if (charts[canvasId]) charts[canvasId].destroy();
  charts[canvasId] = new Chart(ctx, {
    type: "bar",
    data: { labels, datasets: [{ label: title, data: values,
      backgroundColor: labels.map((_, i) => PALETTE[i % PALETTE.length]),
      borderColor: "#0a0a0a", borderWidth: 2.5,
      borderRadius: 4, borderSkipped: false }] },
    options: { responsive: true,
      plugins: { legend: { display: false },
        title: { display: true, text: title + (unit ? ` (${unit})` : ""),
          color: "#0a0a0a", font: { size: 13, weight: "800" } } },
      scales: {
        y: { beginAtZero: true, grid: { color: "rgba(0,0,0,0.10)" },
             ticks: { color: "#0a0a0a" } },
        x: { grid: { display: false }, ticks: { color: "#0a0a0a" } } } },
  });
}

function drawCharts(done) {
  const labels = done.map((r) => r.label || r.isa);
  bar("chart-cycles", "Cycles", labels, done.map((r) => r.metrics.cycles || 0));
  bar("chart-insts", "Instructions executed", labels, done.map((r) => r.metrics.instructions || 0));
  bar("chart-cpi", "CPI (lower = better)", labels, done.map((r) => r.metrics.cpi || 0));
  bar("chart-miss", "L1D miss rate", labels, done.map((r) => pct(r.metrics.l1d_miss_rate)), "%");
}

function drawTable(done) {
  const rows = [
    ["Configuration", (r) => r.label || r.isa],
    ["Instructions", (r) => fmt(r.metrics.instructions)],
    ["Cycles", (r) => fmt(r.metrics.cycles)],
    ["CPI", (r) => round(r.metrics.cpi)],
    ["IPC", (r) => round(r.metrics.ipc)],
    ["Sim time (s)", (r) => r.metrics.sim_seconds ?? "-"],
    ["L1D miss %", (r) => r.metrics.l1d_miss_rate != null ? pct(r.metrics.l1d_miss_rate) : "-"],
    ["L2 miss %", (r) => r.metrics.l2_miss_rate != null ? pct(r.metrics.l2_miss_rate) : "-"],
  ];
  let html = "";
  rows.forEach(([name, fn], i) => {
    html += "<tr>" + (i === 0 ? `<th>${name}</th>` : `<td>${name}</td>`);
    done.forEach((r) => { const v = fn(r); html += i === 0 ? `<th>${v}</th>` : `<td class="num">${v}</td>`; });
    html += "</tr>";
  });
  $("metrics-table").innerHTML = html;
}

const fail = (msg, t) => { msg.textContent = t; msg.classList.add("err"); };
const fmt = (n) => (n == null ? "-" : Number(n).toLocaleString());
const round = (n) => (n == null ? "-" : Number(n).toFixed(3));
const pct = (n) => (n == null ? 0 : +(Number(n) * 100).toFixed(2));

init();
