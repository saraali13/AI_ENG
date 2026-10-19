/* Rubik's Cube Race - frontend logic (vanilla JS). Talks to the FastAPI backend; holds no secrets. */
"use strict";

const FACES = ["U", "R", "F", "D", "L", "B"];
const SOLVED = FACES.map((f) => f.repeat(9)).join("");
const COLORS = { jev: "#7c8cff", luna: "#38e0c0", clef: "#fbbf24", opus: "#f472b6" };
const CATEGORY_LABEL = { final: "Final (exactly 3 moves)", warmup: "Warm-up", primary: "Primary", extension: "Extension" };
const MODE_LABEL = { mock: "mock (simulated)", ollama: "ollama (local)", replay: "replay (cache only)", real: "real API" };

const state = {
  config: null,
  models: [],
  health: null,
  category: "final",
  mode: "mock",
  scramble: null,   // response of POST /api/scramble
  run: null,        // latest run object (live or finished)
  view: "start",    // "start" or a competitor key
  frame: null,      // null = follow the latest frame, otherwise a frame index
  busy: false,
  poll: null,
  playTimer: null,
  aggregate: [],
  charts: {},
};

const $ = (id) => document.getElementById(id);

/* ----------------------------------------------------------------- tiny DOM helper (text is never parsed as HTML) */
function h(tag, props, ...kids) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") node.className = v;
    else if (k === "text") node.textContent = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) {
    if (kid === null || kid === undefined || kid === false) continue;
    node.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  }
  return node;
}

async function api(path, options) {
  let resp;
  try {
    resp = await fetch(path, options);
  } catch (err) {
    throw new Error("Cannot reach the backend. Is the server running?");
  }
  let data = null;
  try { data = await resp.json(); } catch (_) { /* not JSON */ }
  if (!resp.ok) {
    const e = data && data.error;
    let msg = (e && e.message) || `Request failed (HTTP ${resp.status})`;
    if (e && Array.isArray(e.details) && e.details.length) msg += ": " + e.details.map((d) => `${d.where} ${d.message}`).join("; ");
    throw new Error(msg);
  }
  return data;
}
const post = (path, body) => api(path, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body || {}) });

/* ----------------------------------------------------------------- formatting */
const fmtMs = (v) => (v === null || v === undefined ? "–" : v >= 1000 ? (v / 1000).toFixed(2) + " s" : Math.round(v) + " ms");
const fmtSec = (v) => (v === null || v === undefined ? "–" : (v / 1000).toFixed(2) + " s");
const fmtPct = (v) => (v === null || v === undefined ? "–" : Math.round(v * 100) + "%");
const fmtCost = (v, mode) => (v === null || v === undefined ? (mode === "real" ? "not reported" : "–") : "$" + (+v).toFixed(6));
const fmtGap = (v) => (v === null || v === undefined ? "N/A" : (v > 0 ? "+" : "") + v);

function banner(message, kind) {
  const b = $("banner");
  if (!message) { b.hidden = true; return; }
  b.hidden = false;
  b.className = "banner " + (kind || "info");
  b.textContent = message;
}

/* ----------------------------------------------------------------- cube rendering */
const cube3d = { faces: {}, rx: -28, ry: -38 };

function buildCube3d() {
  const root = $("cube3d");
  root.textContent = "";
  for (const f of FACES) {
    const face = h("div", { class: "face", "data-f": f });
    cube3d.faces[f] = [];
    for (let i = 0; i < 9; i++) {
      const s = h("div", { class: "sticker", "data-c": f });
      face.append(s);
      cube3d.faces[f].push(s);
    }
    root.append(face);
  }
  applyRotation();

  const stage = $("stage");
  let drag = null;
  stage.addEventListener("pointerdown", (e) => { drag = { x: e.clientX, y: e.clientY }; stage.setPointerCapture(e.pointerId); });
  stage.addEventListener("pointermove", (e) => {
    if (!drag) return;
    cube3d.ry += (e.clientX - drag.x) * 0.6;
    cube3d.rx = Math.max(-89, Math.min(89, cube3d.rx - (e.clientY - drag.y) * 0.6));
    drag = { x: e.clientX, y: e.clientY };
    applyRotation();
  });
  const end = () => { drag = null; };
  stage.addEventListener("pointerup", end);
  stage.addEventListener("pointercancel", end);
}
function applyRotation() { $("cube3d").style.transform = `rotateX(${cube3d.rx}deg) rotateY(${cube3d.ry}deg)`; }

function paintCube3d(facelets) {
  FACES.forEach((f, fi) => {
    cube3d.faces[f].forEach((s, i) => { s.dataset.c = facelets[fi * 9 + i]; });
  });
}

/* A flat "net": row 1 = U, row 2 = L F R B, row 3 = D. Returns an object with update(facelets). */
function makeNet(container, mini) {
  container.textContent = "";
  container.classList.toggle("mini", !!mini);
  const layout = [[null, "U", null, null], ["L", "F", "R", "B"], [null, "D", null, null]];
  const cells = {};
  for (const row of layout) {
    for (const f of row) {
      if (!f) { container.append(h("div", { class: "nf blank" })); continue; }
      const face = h("div", { class: "nf" });
      cells[f] = [];
      for (let i = 0; i < 9; i++) { const s = h("div", { class: "sticker", "data-c": f }); face.append(s); cells[f].push(s); }
      container.append(face);
    }
  }
  return {
    update(facelets) {
      FACES.forEach((f, fi) => cells[f].forEach((s, i) => { s.dataset.c = facelets[fi * 9 + i]; }));
    },
  };
}

/* ----------------------------------------------------------------- frames (replay) */
function competitor(key) { return state.run && state.run.competitors.find((c) => c.key === key); }

function framesFor(view) {
  if (view === "start") {
    const f = state.run ? state.run.start_facelets : state.scramble ? state.scramble.cube.facelets : SOLVED;
    return [{ facelets: f, label: "start" }];
  }
  const c = competitor(view);
  if (!c) return [{ facelets: SOLVED, label: "start" }];
  const frames = [{ facelets: c.start_facelets, label: "start" }];
  for (const s of c.steps) if (s.applied) frames.push({ facelets: s.facelets_after, label: s.move });
  return frames;
}

function currentFrame() {
  const frames = framesFor(state.view);
  const idx = state.frame === null ? frames.length - 1 : Math.min(state.frame, frames.length - 1);
  return { frames, idx, frame: frames[idx] };
}

function setView(view) {
  stopPlay();
  state.view = view;
  state.frame = null;
  renderViewer(); renderHistory(); renderCards();
}

function stopPlay() {
  if (state.playTimer) { clearInterval(state.playTimer); state.playTimer = null; }
  $("playBtn").textContent = "▶";
}

function togglePlay() {
  if (state.playTimer) { stopPlay(); return; }
  const { frames, idx } = currentFrame();
  if (frames.length < 2) return;
  state.frame = idx >= frames.length - 1 ? 0 : idx;
  $("playBtn").textContent = "❚❚";
  state.playTimer = setInterval(() => {
    const cur = currentFrame();
    if (cur.idx >= cur.frames.length - 1) { state.frame = null; stopPlay(); renderViewer(); return; }
    state.frame = cur.idx + 1;
    renderViewer();
  }, 550);
}

/* ----------------------------------------------------------------- renderers */
let mainNet = null;

function renderViewer() {
  const { frames, idx, frame } = currentFrame();
  paintCube3d(frame.facelets);
  mainNet.update(frame.facelets);

  const solved = frame.facelets === SOLVED;
  let title = "Solved cube";
  if (state.view === "start") {
    title = state.run || state.scramble ? "Shared starting state" : "Solved cube (no scramble yet)";
  } else {
    const c = competitor(state.view);
    title = `${c.name}: after ${idx} move${idx === 1 ? "" : "s"}`;
  }
  $("viewTitle").textContent = title;
  const pill = $("viewSolved");
  pill.textContent = solved ? "solved" : "not solved";
  pill.className = "pill " + (solved ? "solved" : "");

  const replay = $("replay");
  replay.hidden = frames.length < 2;
  const range = $("frameRange");
  range.max = frames.length - 1;
  range.value = idx;
  $("frameLabel").textContent = `${idx} / ${frames.length - 1}`;

  // the view selector
  const sel = $("viewSelect");
  const wanted = ["start", ...(state.run ? state.run.competitors.map((c) => c.key) : [])];
  if (sel.options.length !== wanted.length) {
    sel.textContent = "";
    for (const k of wanted) sel.append(h("option", { value: k, text: k === "start" ? "Starting state" : (competitor(k) || {}).name }));
  }
  sel.value = state.view;
}

function renderScramble() {
  const chips = $("scrambleChips");
  chips.textContent = "";
  const s = state.scramble || (state.run && { scramble_moves: state.run.scramble_moves, category: state.run.category, optimal: state.run.optimal });
  if (!s) {
    chips.append(h("span", { class: "muted", text: "No scramble yet. Press “Generate Scramble” (or just run the benchmark)." }));
    $("scrambleMeta").textContent = "";
    return;
  }
  s.scramble_moves.forEach((m, i) => chips.append(h("span", { class: "chip", style: `animation-delay:${i * 30}ms`, text: m })));
  const opt = s.optimal && s.optimal.exact ? `Exact shortest solution: ${s.optimal.length} moves.` : "Shortest solution: N/A (exact search only proves lengths up to 8).";
  $("scrambleMeta").textContent = `${CATEGORY_LABEL[s.category] || s.category}: ${s.scramble_moves.length} moves. ${opt}`;
}

/* competitor cards are built once and updated in place so the live nets don't flicker */
const cardRefs = {};

function ensureCards() {
  const wrap = $("cards");
  if (Object.keys(cardRefs).length === state.models.length && state.models.length) return;
  wrap.textContent = "";
  for (const m of state.models) {
    const refs = {};
    const dd = (key, label) => { refs[key] = h("dd", { text: "–" }); return h("div", {}, h("dt", { text: label }), refs[key]); };
    refs.status = h("span", { class: "pill idle", text: "idle" });
    refs.mode = h("span", { class: "pill", text: "" });
    refs.netBox = h("div", { class: "net" });
    refs.verdict = h("div", { class: "verdict" });
    refs.root = h("article", { class: "card", tabindex: "0", role: "button", "aria-label": `Show ${m.name}`, onclick: () => setView(m.key),
      onkeydown: (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setView(m.key); } } },
      h("div", { class: "card-top" },
        h("div", {}, h("h3", { text: m.name }), h("div", { class: "mid", text: m.openrouter_id }),
          h("div", { class: "badges" }, refs.mode, h("span", { class: "pill", text: m.protocol === "decisions" ? "decisions API" : "chat API" }))),
        refs.status),
      h("div", { class: "card-body" },
        refs.netBox,
        h("dl", { class: "stats" }, dd("moves", "Moves"), dd("latency", "Live latency"), dd("cache", "Cache latency"), dd("time", "Solve time"),
          dd("calls", "API calls"), dd("hits", "Cache hits"), dd("cost", "Cost (USD)"), dd("bad", "Invalid / errors"))),
      refs.verdict);
    refs.net = makeNet(refs.netBox, true);
    refs.net.update(SOLVED);
    cardRefs[m.key] = refs;
    wrap.append(refs.root);
  }
}

function renderCards() {
  ensureCards();
  const run = state.run;
  for (const m of state.models) {
    const r = cardRefs[m.key];
    const c = run && competitor(m.key);
    const status = c ? c.status : "idle";
    r.root.className = "card " + status + (state.view === m.key ? " selected" : "");
    r.status.className = "pill " + status;
    r.status.textContent = status === "running" ? "thinking…" : status === "failed" ? "unsolved" : status;
    const mode = c ? c.mode : state.mode;
    r.mode.className = "pill " + mode;
    r.mode.textContent = MODE_LABEL[mode] || mode;
    if (!c) {
      r.net.update(state.scramble ? state.scramble.cube.facelets : SOLVED);
      for (const k of ["moves", "latency", "cache", "time", "calls", "hits", "cost", "bad"]) r[k].textContent = "–";
      r.verdict.textContent = "";
      r.verdict.className = "verdict";
      continue;
    }
    r.net.update(c.final_facelets || c.start_facelets);
    const met = c.metrics;
    r.moves.textContent = `${met.moves} / ${run.move_limit}`;
    r.latency.textContent = fmtMs(met.avg_latency_ms);
    r.time.textContent = c.total_time_ms === null ? "–" : fmtSec(c.total_time_ms);
    r.cache.textContent = fmtMs(met.avg_cache_latency_ms);
    r.calls.textContent = met.api_calls ?? "–";
    r.hits.textContent = met.cache_hits ?? "–";
    r.cost.textContent = fmtCost(met.cost_usd, c.mode);
    r.bad.textContent = `${met.invalid_responses} / ${met.api_errors}`;
    if (c.status === "solved") { r.verdict.className = "verdict ok"; r.verdict.textContent = `✓ Solved in ${met.moves} move${met.moves === 1 ? "" : "s"}`; }
    else if (c.status === "failed") { r.verdict.className = "verdict no"; r.verdict.textContent = "✗ Unsolved: " + (c.failure_reason || "failed"); }
    else { r.verdict.className = "verdict"; r.verdict.textContent = c.status === "running" ? `Move ${met.moves + 1} of ${run.move_limit} max…` : "Waiting…"; }
  }
}

function renderProgress() {
  const run = state.run;
  const bar = $("overallBar");
  if (!run) { $("runStatus").textContent = "Idle"; bar.style.width = "0%"; return; }
  const comps = run.competitors;
  const done = comps.filter((c) => c.status === "solved" || c.status === "failed").length;
  const partial = comps.reduce((a, c) => a + (c.status === "solved" || c.status === "failed" ? 1 : Math.min(c.metrics.moves / run.move_limit, 0.95)), 0);
  bar.style.width = Math.round((partial / comps.length) * 100) + "%";
  if (run.status === "completed") $("runStatus").textContent = `Finished (${run.mode}): ${comps.filter((c) => c.status === "solved").length}/${comps.length} solved`;
  else if (run.status === "error") $("runStatus").textContent = "Run failed: " + (run.error || "unknown error");
  else $("runStatus").textContent = `Running (${run.mode}): ${done}/${comps.length} finished`;
}

function renderHistory() {
  const tabs = $("histTabs");
  tabs.textContent = "";
  const keys = ["start", ...(state.run ? state.run.competitors.map((c) => c.key) : [])];
  for (const k of keys) {
    tabs.append(h("button", { class: "tab", type: "button", role: "tab", "aria-selected": String(state.view === k), onclick: () => setView(k),
      text: k === "start" ? "Scramble" : (competitor(k) || {}).name }));
  }
  const body = $("histTable").tBodies[0];
  body.textContent = "";
  const { idx } = currentFrame();

  if (state.view === "start") {
    const moves = state.scramble ? state.scramble.scramble_moves : state.run ? state.run.scramble_moves : [];
    if (!moves.length) { body.append(h("tr", {}, h("td", { colspan: 5, class: "muted", text: "No scramble yet." }))); return; }
    moves.forEach((m, i) => body.append(h("tr", {}, h("td", { class: "num", text: i + 1 }), h("td", { class: "num", text: m }),
      h("td", { text: "scramble move" }), h("td", { text: "–" }), h("td", { text: "" }))));
    return;
  }
  const c = competitor(state.view);
  if (!c || !c.steps.length) { body.append(h("tr", {}, h("td", { colspan: 5, class: "muted", text: "No moves yet." }))); return; }
  for (const s of c.steps) {
    let result, cls = "";
    if (s.applied) { result = `✓ applied${s.proposed && s.proposed !== s.move ? " as " + s.move : ""}`; cls = "ok"; }
    else if (s.error_kind === "invalid_response") { result = "✗ invalid move (not applied)"; cls = "no"; }
    else { result = `⚠ ${s.error_kind} (no reply)`; cls = "warnc"; }
    const note = s.error || (s.solved_after ? "cube solved" : (s.meta && s.meta.confidence !== undefined ? `confidence ${(s.meta.confidence * 100).toFixed(0)}%` : ""));
    const clickable = s.applied;
    body.append(h("tr", { class: (clickable ? "clickable " : "") + (s.applied && s.move_number === idx ? "active" : ""),
      onclick: clickable ? () => { stopPlay(); state.frame = s.move_number; renderViewer(); renderHistory(); } : null },
      h("td", { class: "num", text: s.step }), h("td", { class: "num", text: s.proposed || "–" }),
      h("td", { class: cls, text: result }), h("td", { class: "num", text: s.latency_ms === null ? "–" : fmtMs(s.latency_ms) }),
      h("td", { text: note })));
  }
  const wrap = body.closest(".table-scroll");
  if (state.run && state.run.status === "running") wrap.scrollTop = wrap.scrollHeight;
}

function renderResultsTable() {
  const body = $("resultsTable").tBodies[0];
  body.textContent = "";
  const run = state.run;
  if (!run) { body.append(h("tr", {}, h("td", { colspan: 13, class: "muted", text: "Run a benchmark to see results." }))); $("optimalNote").textContent = ""; return; }
  for (const c of run.competitors) {
    const m = c.metrics;
    const cls = c.status === "solved" ? "ok" : c.status === "failed" ? "no" : "";
    body.append(h("tr", {},
      h("td", { text: c.name }), h("td", {}, h("span", { class: "pill " + c.mode, text: c.mode })),
      h("td", { class: cls, text: c.status === "solved" ? "✓ solved" : c.status === "failed" ? "✗ unsolved" : c.status, title: c.failure_reason || "" }),
      h("td", { class: "num", text: m.moves }), h("td", { class: "num", text: fmtMs(m.avg_latency_ms) }),
      h("td", { class: "num", text: fmtMs(m.avg_cache_latency_ms) }),
      h("td", { class: "num", text: fmtSec(c.total_time_ms) }),
      h("td", { class: "num", text: m.api_calls ?? "–" }), h("td", { class: "num", text: m.cache_hits ?? "–" }),
      h("td", { class: "num", text: fmtCost(m.cost_usd, c.mode) }), h("td", { class: "num", text: m.invalid_responses }),
      h("td", { class: "num", text: m.api_errors }),
      h("td", { class: "num", text: fmtGap(m.optimality_gap), title: "moves used minus the proven shortest solution; N/A when not solved or when no exact optimum is known" })));
  }
  const o = run.optimal || {};
  $("optimalNote").textContent = `Scramble: ${run.scramble}. ` + (o.exact ? `Proven shortest solution: ${o.length} moves (${o.moves.join(" ")}).` : (o.note || "Optimal length not computed."));
}

function renderAggTable() {
  const body = $("aggTable").tBodies[0];
  body.textContent = "";
  if (!state.aggregate.length) { body.append(h("tr", {}, h("td", { colspan: 9, class: "muted", text: `No saved ${state.mode} runs for ${CATEGORY_LABEL[state.category]} yet.` }))); return; }
  for (const r of state.aggregate) {
    body.append(h("tr", {}, h("td", { text: r.name }), h("td", {}, h("span", { class: "pill " + r.mode, text: r.mode })),
      h("td", { class: "num", text: r.runs }), h("td", { class: "num", text: r.solved }), h("td", { class: "num", text: fmtPct(r.success_rate) }),
      h("td", { class: "num", text: fmtMs(r.avg_latency_ms) }), h("td", { class: "num", text: r.avg_moves_when_solved ?? "–" }),
      h("td", { class: "num", text: fmtSec(r.avg_solve_time_ms) }), h("td", { class: "num", text: fmtGap(r.avg_optimality_gap) })));
  }
}

/* ----------------------------------------------------------------- charts */
function chartTheme() {
  const css = getComputedStyle(document.documentElement);
  return { text: css.getPropertyValue("--muted").trim() || "#93a0bd", grid: css.getPropertyValue("--line").trim() || "#26324f" };
}

function upsertChart(id, labels, values, colors, opts) {
  if (!window.Chart) return;
  const theme = chartTheme();
  let chart = state.charts[id];
  if (!chart) {
    chart = new Chart($(id), {
      type: "bar",
      data: { labels, datasets: [{ data: values, backgroundColor: colors, borderColor: colors, borderWidth: 1, borderRadius: 6 }] },
      options: {
        responsive: true, maintainAspectRatio: false, animation: { duration: 300 },
        plugins: { legend: { display: false }, tooltip: { callbacks: { label: (ctx) => (ctx.parsed.y === null ? "n/a" : opts.fmt(ctx.parsed.y)) } } },
        scales: {
          x: { ticks: { color: theme.text }, grid: { display: false } },
          y: { beginAtZero: true, max: opts.max, ticks: { color: theme.text }, grid: { color: theme.grid } },
        },
      },
    });
    state.charts[id] = chart;
    return;
  }
  chart.data.labels = labels;
  chart.data.datasets[0].data = values;
  chart.data.datasets[0].backgroundColor = colors;
  chart.data.datasets[0].borderColor = colors;
  chart.update();
}

const faded = (hex, on) => (on ? hex : hex + "66"); // 40% alpha when the competitor did not solve

function renderCharts() {
  const run = state.run;
  const comps = run ? run.competitors : [];
  const labels = comps.map((c) => c.name);
  const colors = comps.map((c) => faded(COLORS[c.key] || "#888888", c.status === "solved"));
  upsertChart("chLatency", labels, comps.map((c) => c.metrics.avg_latency_ms), colors, { fmt: (v) => fmtMs(v) });
  upsertChart("chMoves", labels, comps.map((c) => c.metrics.moves), colors, { fmt: (v) => v + " moves" });
  upsertChart("chTime", labels, comps.map((c) => (c.total_time_ms === null ? null : +(c.total_time_ms / 1000).toFixed(3))), colors, { fmt: (v) => v + " s" });

  const rows = state.aggregate;
  upsertChart("chSuccess", rows.map((r) => r.name), rows.map((r) => (r.success_rate === null ? null : Math.round(r.success_rate * 100))),
    rows.map((r) => COLORS[r.key] || "#888888"), { fmt: (v) => v + "%", max: 100 });
}

function renderModels() {
  const list = $("modelList");
  list.textContent = "";
  for (const m of state.models) {
    list.append(h("div", { class: "model" },
      h("h3", { text: m.name }),
      h("div", { class: "mono small muted", text: `${m.openrouter_id}  ·  ${m.protocol === "decisions" ? "POST /api/alpha/decisions" : "POST /api/v1/chat/completions"}` }),
      h("p", { text: m.summary }), h("p", { text: m.notes }),
      h("p", { class: "small" }, "Source: ", ...m.sources.flatMap((s, i) => [i ? " · " : "", h("a", { href: s, target: "_blank", rel: "noopener noreferrer", text: new URL(s).pathname })])),
    ));
  }
  $("conv").textContent = "Counting convention: " + state.config.counting_convention;
}

function renderControls() {
  document.querySelectorAll("#categorySeg button").forEach((b) => b.setAttribute("aria-checked", String(b.dataset.cat === state.category)));
  document.querySelectorAll("#modeSeg button").forEach((b) => b.setAttribute("aria-checked", String(b.dataset.mode === state.mode)));
  const real = state.config && state.config.real_mode_available;
  $("realBtn").disabled = !real || state.busy;
  if (!real && state.mode === "real") state.mode = "mock";
  $("realHint").textContent = real ? "calls OpenRouter with your key" : "needs OPENROUTER_API_KEY in .env";
  document.querySelectorAll("#categorySeg button, #modeSeg button:not(#realBtn)").forEach((b) => { b.disabled = state.busy; });
  $("scrambleBtn").disabled = state.busy;
  $("resetBtn").disabled = state.busy;
  $("runBtn").disabled = state.busy;
  $("runBtn").firstElementChild.textContent = state.busy ? "Running…" : "Run Benchmark";
}

function renderAll() {
  renderControls(); renderScramble(); renderViewer(); renderCards(); renderProgress(); renderHistory(); renderResultsTable(); renderCharts(); renderAggTable();
}

/* ----------------------------------------------------------------- actions */
async function generateScramble() {
  banner("");
  try {
    const s = await post("/api/scramble", { category: state.category });
    state.scramble = s;
    state.run = null;
    state.view = "start";
    state.frame = null;
    stopPlay();
    renderAll();
    return s;
  } catch (err) { banner(err.message, "error"); return null; }
}

async function resetAll() {
  banner("");
  stopPlay();
  try { await post("/api/reset"); } catch (err) { banner(err.message, "error"); return; }
  state.scramble = null; state.run = null; state.view = "start"; state.frame = null;
  document.getElementById("viewSelect").textContent = "";
  renderAll();
  refreshAggregate();
}

async function refreshBudget() {
  const box = $("budgetBody");
  try {
    const d = await api("/api/budget");
    const o = d.ledger.openrouter, L = d.ledger.limits, claude = o.by_model.opus || { requests: 0 };
    const meter = (used, max) => h("div", { class: "meter" }, h("i", { class: used >= max ? "hot" : "", style: `width:${Math.min(100, (used / max) * 100)}%` }));
    box.textContent = "";
    box.append(
      h("div", { class: "row" }, h("span", { text: `All models: ${o.requests} / ${L.global_limit}` }), h("span", { text: `hard ceiling ${L.hard_ceiling}` })), meter(o.requests, L.global_limit),
      h("div", { class: "row" }, h("span", { text: `Claude: ${claude.requests} / ${L.claude_limit}` }), h("span", { text: `cache hits ${d.ledger.cache_hits.total}` })), meter(claude.requests, L.claude_limit),
      h("div", { text: `Cached answers: ${d.cache.entries} · actual cost so far: $${(+o.cost_usd).toFixed(6)}${d.ledger.healthy ? "" : " · LEDGER ERROR: live requests blocked"}` }));
  } catch (err) { box.textContent = "Budget unavailable: " + err.message; }
}

async function refreshAggregate() {
  try {
    const data = await api(`/api/results?category=${state.category}&mode=${state.mode}`);
    state.aggregate = data.aggregate;
  } catch (_) { state.aggregate = []; }
  renderAggTable(); renderCharts();
}

async function runBenchmark() {
  banner("");
  if (state.busy) return;
  state.busy = true; renderControls();
  try {
    if (state.mode === "real") {
      if (state.category !== "final") { state.category = "final"; state.scramble = null; renderAll(); }
      const typed = window.prompt("LIVE RUN: this sends real OpenRouter requests with the restricted key "
        + "(at most 65 in total, at most 5 for Claude; cached answers are free).\n\nType RUN LIVE to continue:");
      if (typed !== "RUN LIVE") { banner("Live run cancelled. No request was sent.", "info"); state.busy = false; renderControls(); return; }
    }
    if (!state.scramble || state.scramble.category !== state.category) {
      if (!(await generateScramble())) { state.busy = false; renderControls(); return; }
    }
    const body = { category: state.category, mode: state.mode };
    if (state.mode !== "real") body.moves = state.scramble.scramble_moves;   // live runs use the server's fixed official start
    if (state.mode === "real") body.confirm_live = true;
    const run = await post("/api/benchmark", body);
    state.run = run; state.view = "start"; state.frame = null;
    document.getElementById("viewSelect").textContent = "";
    renderAll();
    pollRun(run.run_id);
  } catch (err) {
    banner(err.message, "error");
    state.busy = false; renderControls();
  }
}

function pollRun(runId) {
  clearInterval(state.poll);
  const tick = async () => {
    try {
      const run = await api(`/api/benchmark/${runId}`);
      state.run = run;
      const finished = run.status !== "queued" && run.status !== "running";
      if (state.view !== "start" && state.frame === null) { /* follow live */ }
      renderViewer(); renderCards(); renderProgress(); renderHistory(); renderResultsTable(); renderCharts();
      if (finished) {
        clearInterval(state.poll);
        state.busy = false;
        if (run.status === "error") banner("The run stopped with an error: " + run.error, "error");
        renderControls(); renderScramble();
        await refreshAggregate(); refreshBudget();
      }
    } catch (err) {
      clearInterval(state.poll);
      state.busy = false; renderControls();
      banner(err.message, "error");
    }
  };
  state.poll = setInterval(tick, 350);
  tick();
}

/* ----------------------------------------------------------------- startup */
async function init() {
  buildCube3d();
  mainNet = makeNet($("mainNet"), false);
  mainNet.update(SOLVED);

  document.querySelectorAll("#categorySeg button").forEach((b) => b.addEventListener("click", () => {
    if (state.busy) return;
    state.category = b.dataset.cat;
    if (state.scramble && state.scramble.category !== state.category) { state.scramble = null; }
    renderAll(); refreshAggregate();
  }));
  document.querySelectorAll("#modeSeg button").forEach((b) => b.addEventListener("click", () => {
    if (state.busy || b.disabled) return;
    state.mode = b.dataset.mode;
    renderControls(); refreshAggregate();
  }));
  $("scrambleBtn").addEventListener("click", generateScramble);
  $("runBtn").addEventListener("click", runBenchmark);
  $("resetBtn").addEventListener("click", resetAll);
  $("viewSelect").addEventListener("change", (e) => setView(e.target.value));
  $("playBtn").addEventListener("click", togglePlay);
  $("frameRange").addEventListener("input", (e) => { stopPlay(); state.frame = Number(e.target.value); renderViewer(); renderHistory(); });

  try {
    const [config, models, health] = await Promise.all([api("/api/config"), api("/api/models"), api("/api/health")]);
    state.config = config; state.models = models.models; state.health = health;
  } catch (err) {
    banner(err.message + " Start it with: uvicorn backend.main:app --reload", "error");
    return;
  }
  $("studentName").textContent = state.config.student_name;
  $("studentRoll").textContent = "Roll No: " + state.config.roll_number;
  $("footStudent").textContent = `${state.config.student_name} · ${state.config.roll_number}`;
  document.title = `Rubik's Cube Race · ${state.config.student_name}`;
  renderModels();
  renderAll();
  refreshBudget();
  await refreshAggregate();

  // Show the most recent saved run so a page refresh does not lose the last results.
  try {
    const list = await api("/api/results?limit=1");
    if (list.runs.length) {
      const run = await api(`/api/results/${list.runs[0].run_id}`);
      state.run = run; state.category = run.category;
      if (run.mode !== "real" || state.config.real_mode_available) state.mode = run.mode;
      renderAll(); await refreshAggregate();
      $("runStatus").textContent += " (last saved run)";
    }
  } catch (_) { /* nothing saved yet */ }
}

init();
