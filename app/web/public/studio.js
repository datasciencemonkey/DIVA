/* =============================================================================
   UAIG Voice Studio — application logic
   Configure -> Generate -> Live, one governed voice call instrumented across
   the four pillars (Choice / Control / Context / Costs).

   The Live screen preserves the VERIFIED-WORKING flow from the throwaway
   harness: /api/token -> room.connect(serverUrl, token) -> enable mic ->
   TrackSubscribed audio -> DataReceived (topic "ug_evidence") -> Transcription.
   Everything on screen traces to real data (agent evidence, gateway, or a
   client-side measurement). No fabricated metrics.
   ========================================================================== */
(() => {
  "use strict";

  const $  = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));

  const REDUCED = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const g = window.gsap;
  const HAS_GSAP = typeof g !== "undefined" && !REDUCED;
  const LK = window.LivekitClient;

  /* ---- localStorage, always guarded ------------------------------------ */
  const store = {
    get(k) { try { return localStorage.getItem(k); } catch { return null; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch { /* private mode */ } },
  };

  /* ---- theme (dark is the default; a "light" choice is persisted) ------- */
  const THEME_KEY = "ug_theme";
  function currentTheme() {
    return document.documentElement.getAttribute("data-theme") === "light" ? "light" : "dark";
  }
  function applyTheme(theme) {
    const t = theme === "light" ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", t);
    const btn = $("#themeToggle");
    if (!btn) return;
    const isLight = t === "light";
    const label = isLight ? "Switch to dark theme" : "Switch to light theme";
    btn.setAttribute("aria-pressed", String(isLight));
    btn.setAttribute("aria-label", label);
    btn.title = label;
  }
  function initTheme() {
    // The <head> pre-paint script already set the attribute (no flash). Re-apply here
    // to sync the toggle's aria state and to cover private-mode/blocked-storage loads.
    applyTheme(store.get(THEME_KEY) === "light" ? "light" : "dark");
    const btn = $("#themeToggle");
    if (btn) btn.addEventListener("click", () => {
      const next = currentTheme() === "light" ? "dark" : "light";
      applyTheme(next);
      store.set(THEME_KEY, next);
    });
  }

  /* ---- app state ------------------------------------------------------- */
  const state = {
    stage: "configure",
    presetId: null,
    job: { id: null, timer: null },
    draftDetail: {},        // counts captured at the "drafted" stage (records lives here)
    world: null,            // { gid, company, customers[], counts, mode }
    caller: null,           // { kind, id, name, tier }
    room: null,
    audio: { ctx: null, raf: null, els: [] },
    metrics: { t0: null, awaiting: false, latencies: [] },
    boundModel: null,
    retrievals: 0,
    turns: new Map(),       // segment id -> DOM element
    examples: [],           // grounded read-aloud prompts for the current world
  };

  /* =============================================================================
     PRESETS — voice-friendly system prompts, "answer only from tools; never invent".
     ========================================================================== */
  const PRESETS = [
    {
      id: "cascade", company: "Cascade Airlines", domain: "Airline",
      role: "Airline support agent",
      system_prompt:
        "You are the voice assistant for Cascade Airlines. Answer only from your tools and " +
        "retrieved policies — never invent fares, schedules, baggage rules, or booking details. " +
        "If a tool returns nothing, offer to connect a human agent. Reply in one or two brief, " +
        "calm spoken sentences. Never state or guess a caller's loyalty tier or status.",
    },
    {
      id: "wayfarer", company: "Wayfarer Voyages", domain: "Cruise line",
      role: "Cruise guest support agent",
      system_prompt:
        "You are the voice assistant for Wayfarer Voyages, a cruise line. Answer only from your " +
        "tools and retrieved policies — never invent itineraries, cabin availability, excursion " +
        "details, or booking records. If a tool returns nothing, offer to connect guest services. " +
        "Reply in one or two brief, calm spoken sentences. Never state or guess a caller's loyalty tier or status.",
    },
    {
      id: "harborstone", company: "Harborstone Hotels", domain: "Hotels & resorts",
      role: "Guest services agent",
      system_prompt:
        "You are the voice assistant for Harborstone Hotels & Resorts. Answer only from your tools " +
        "and retrieved policies — never invent rates, room availability, amenities, or reservation " +
        "details. If a tool returns nothing, offer to connect the front desk. Reply in one or two " +
        "brief, warm spoken sentences. Never state or guess a caller's loyalty tier or status.",
    },
    {
      id: "nestly", company: "Nestly Stays", domain: "Vacation rentals",
      role: "Host & guest support agent",
      system_prompt:
        "You are the voice assistant for Nestly Stays, a vacation-rental platform. Answer only from " +
        "your tools and retrieved policies — never invent property details, house rules, prices, or " +
        "booking records. If a tool returns nothing, offer to connect a support specialist. Keep " +
        "replies to one or two short, friendly spoken sentences. Never state or guess a caller's tier or status.",
    },
    {
      id: "brightwok", company: "Brightwok Kitchen", domain: "Quick-service",
      role: "Order & rewards support agent",
      system_prompt:
        "You are the voice assistant for Brightwok Kitchen, a quick-service restaurant. Answer only " +
        "from your tools and retrieved menu and policies — never invent menu items, prices, order " +
        "status, or rewards balances. If a tool returns nothing, offer to connect a team member. " +
        "Keep replies to one or two short, friendly spoken sentences. Never state or guess a caller's rewards tier or status.",
    },
  ];

  /* Model -> indicative cost tier (client-derived; labelled indicative in UI). */
  const COST = {
    "system.ai.gpt-6-sol":  { label: "High",   tierHint: "VIP",      level: 3, rate: 10.0 },
    "system.ai.gpt-5-5":    { label: "Medium", tierHint: "Premium",  level: 2, rate: 2.5 },
    "system.ai.gpt-5-nano": { label: "Low",    tierHint: "Standard", level: 1, rate: 0.4 },
  };

  /* =============================================================================
     MOTION helpers — degrade to instant final state without GSAP / reduced motion.
     ========================================================================== */
  function animIn(els, { y = 18, stagger = 0.07, delay = 0, dur = 0.6 } = {}) {
    els = els.filter(Boolean);
    if (!els.length) return;
    if (!HAS_GSAP) { els.forEach((e) => { e.style.opacity = ""; e.style.transform = ""; }); return; }
    g.fromTo(els,
      { opacity: 0, y, filter: "blur(6px)" },
      { opacity: 1, y: 0, filter: "blur(0px)", duration: dur, stagger, delay, ease: "power3.out", clearProps: "filter,transform" });
  }

  function countTo(el, value, suffix = "") {
    const v = Math.round(value);
    if (!HAS_GSAP) { el.textContent = v + suffix; return; }
    const obj = { n: parseFloat((el.textContent || "0").replace(/[^\d.]/g, "")) || 0 };
    g.to(obj, { n: v, duration: 0.6, ease: "power2.out",
      onUpdate: () => { el.textContent = Math.round(obj.n) + suffix; } });
  }

  /* Authored-SVG draw-on: strokes render fully drawn by default (CSS), so this
     only ADDS the entrance when GSAP is live. No GSAP / reduced motion => no-op,
     and the finished frame stands. The signal packet (.hm-flow) is never drawn. */
  function pathLen(el) { try { return (el.getTotalLength && el.getTotalLength()) || 0; } catch { return 0; } }
  function drawIn(target, { dur = 0.7, stagger = 0.06, delay = 0, ease = "power3.out" } = {}) {
    if (!HAS_GSAP || !target) return;
    let nodes = (target instanceof Element)
      ? $$("path,line,polyline,polygon,circle,rect,ellipse", target)
      : Array.from(target).filter(Boolean);
    nodes = nodes.filter((n) => n && !n.classList.contains("hm-flow") && pathLen(n) > 0.5);
    if (!nodes.length) return;
    g.set(nodes, { strokeDasharray: (i, el) => pathLen(el), strokeDashoffset: (i, el) => pathLen(el) });
    g.to(nodes, { strokeDashoffset: 0, duration: dur, stagger, delay, ease,
      clearProps: "strokeDasharray,strokeDashoffset" });
  }

  /* Ping a set of SVG elements out from a shared origin — feedback for a real event. */
  function pulse(nodes, { from = 0.5, dur = 0.6, stagger = 0.07, origin } = {}) {
    if (!HAS_GSAP) return;
    nodes = Array.from(nodes || []).filter(Boolean);
    if (!nodes.length) return;
    const to = { scale: 1, opacity: 1, duration: dur, stagger, ease: "power3.out", clearProps: "transform,opacity" };
    if (origin) to.svgOrigin = origin;
    g.fromTo(nodes, { scale: from, opacity: 0.85 }, to);
  }

  /* Loopable timelines we pause when their stage is off-screen (cheap when idle). */
  const motion = { hero: null, mic: null };

  /* HERO MOTIF — the focal moment. Draw the figure on (one-shot), then loop a
     signal packet from the spoken waveform, through the governed gate, to the
     one chosen route. Transform-only loop; paused whenever we leave Configure. */
  function startHeroMotif() {
    const motif = $(".hero-motif svg");
    if (!motif) return;
    if (!HAS_GSAP) return;   // CSS renders the finished frame (reduced-motion / no-GSAP)

    // Narrative draw-in: the caller speaks -> the signal reaches the governed gate ->
    // candidate routes fan out -> the ONE chosen route + node lock in. Ordered so the
    // eye reads the story left-to-right instead of everything appearing at once.
    const bars     = $$("g.hm-wave line", motif);         // spoken waveform
    const conn     = $$("line.hm-wave", motif);           // line into the gate
    const gateStr  = $$(".hm-gate, .hm-shield", motif);   // gate outline + shield
    const gateFill = $$(".hm-gate-fill", motif);          // gate pill (fill)
    const cand     = $$(".hm-route", motif);              // candidate routes
    const candNode = $$(".hm-node", motif);               // candidate nodes
    const active   = $$(".hm-route--active", motif);      // the chosen route
    const chosen   = $$(".hm-node-fill, .hm-node--active", motif);  // the chosen node

    // fills would otherwise be visible at t=0 (drawIn only strokes) — enter them in step
    g.set([...gateFill, ...chosen], { opacity: 0 });

    drawIn(bars,     { delay: 0.30, dur: 0.50, stagger: 0.035 });
    drawIn(conn,     { delay: 0.85, dur: 0.30 });
    g.to(gateFill,   { opacity: 1, duration: 0.35, delay: 1.05, ease: "power2.out" });
    drawIn(gateStr,  { delay: 1.10, dur: 0.50, stagger: 0.08 });
    drawIn(cand,     { delay: 1.60, dur: 0.50, stagger: 0.14 });
    drawIn(candNode, { delay: 1.85, dur: 0.40, stagger: 0.10 });
    drawIn(active,   { delay: 2.10, dur: 0.55 });
    // the chosen node "locks in" — the reserved back.out confirmation accent
    g.fromTo(chosen, { opacity: 0, scale: 0.3 },
      { opacity: 1, scale: 1, duration: 0.5, delay: 2.55, ease: "back.out(2)",
        svgOrigin: "424 40", clearProps: "transform,opacity" });

    // Then a calm signal packet loops: voice -> gate -> chosen node.
    const pk = $(".hm-flow", motif);
    if (!pk) return;
    if (motion.hero) { motion.hero.kill(); motion.hero = null; }
    const tl = g.timeline({ repeat: -1, repeatDelay: 0.8, delay: 3.2 });
    tl.set(pk, { opacity: 0, x: 40, y: 84 })
      .to(pk, { opacity: 1, duration: 0.25, ease: "power1.out" })
      .to(pk, { x: 190, y: 84, duration: 0.90, ease: "sine.inOut" })   // through the waveform
      .to(pk, { x: 226, y: 84, duration: 0.35, ease: "sine.inOut" })   // out of the gate
      .to(pk, { x: 340, y: 56, duration: 0.55, ease: "sine.inOut" })   // onto the chosen route
      .to(pk, { x: 424, y: 40, duration: 0.55, ease: "power1.in" })    // arrive at the chosen node
      .to(pk, { opacity: 0, duration: 0.3 }, "-=0.05");
    motion.hero = tl;
  }

  /* =============================================================================
     STAGE MACHINE
     ========================================================================== */
  const STAGES = {
    configure: { el: $("#stage-configure"), anim: "[data-anim]" },
    generate:  { el: $("#stage-generate"),  anim: "[data-anim-g]" },
    live:      { el: $("#stage-live"),       anim: "[data-anim-l]" },
  };

  function setStepper(name) {
    const order = ["configure", "generate", "live"];
    const idx = order.indexOf(name);
    $$(".step").forEach((s) => {
      const so = order.indexOf(s.dataset.step);
      if (so === idx) s.setAttribute("aria-current", "step"); else s.removeAttribute("aria-current");
      s.dataset.done = so < idx ? "true" : "false";
    });
  }

  function goStage(name) {
    if (name === state.stage) return;
    const from = STAGES[state.stage].el;
    const to = STAGES[name].el;
    const finish = () => {
      from.hidden = true;
      to.hidden = false;
      state.stage = name;
      document.body.dataset.stage = name;
      setStepper(name);
      animIn($$(STAGES[name].anim, to));
      // the hero signal loop is only visible on Configure — pause it elsewhere
      if (motion.hero) { if (name === "configure") motion.hero.play(); else motion.hero.pause(); }
      window.scrollTo({ top: 0, behavior: REDUCED ? "auto" : "smooth" });
    };
    if (!HAS_GSAP) { finish(); return; }
    g.to(from, { opacity: 0, y: -14, duration: 0.28, ease: "power2.in",
      onComplete: () => { g.set(from, { opacity: 1, y: 0 }); finish(); } });
  }

  /* =============================================================================
     API
     ========================================================================== */
  async function apiGenerate(body) {
    const r = await fetch("/api/generate", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
    if (!r.ok) throw new Error(`generate failed (${r.status})`);
    return r.json();
  }
  async function apiStatus(jobId) {
    const r = await fetch(`/api/generate/status?job_id=${encodeURIComponent(jobId)}`);
    if (!r.ok) throw new Error(`status failed (${r.status})`);
    return r.json();
  }
  async function apiDatasets() {
    const r = await fetch("/api/datasets");
    if (!r.ok) throw new Error(`datasets failed (${r.status})`);
    return r.json();
  }
  async function apiToken(gid, cid, name) {
    const qs = new URLSearchParams({ dataset: gid || "", customer: cid || "", name: name || "" });
    const r = await fetch(`/api/token?${qs}`);
    if (!r.ok) throw new Error(`token failed (${r.status})`);
    return r.json();
  }
  /* grounded example prompts — fail-soft: any error or empty payload yields [] and the
     "Try asking" panel simply stays hidden. Never throws. */
  async function apiExamples(gid) {
    try {
      const r = await fetch(`/api/examples?dataset=${encodeURIComponent(gid || "")}`);
      if (!r.ok) return [];
      const data = await r.json();
      const qs = data && Array.isArray(data.questions) ? data.questions : [];
      return qs.filter((q) => typeof q === "string" && q.trim()).map((q) => q.trim());
    } catch { return []; }
  }

  /* =============================================================================
     STAGE 1 — CONFIGURE
     ========================================================================== */
  const fCompany = $("#fCompany"), fRole = $("#fRole"), fPrompt = $("#fPrompt");
  const configError = $("#configError");

  function renderPresets() {
    const wrap = $("#presets");
    wrap.innerHTML = "";
    PRESETS.forEach((p) => {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "preset";
      b.setAttribute("aria-pressed", "false");
      b.dataset.id = p.id;
      b.innerHTML = `<b>${p.company}</b><span>${p.domain}</span>`;
      b.addEventListener("click", () => applyPreset(p.id));
      wrap.appendChild(b);
    });
  }

  function applyPreset(id) {
    const p = PRESETS.find((x) => x.id === id);
    if (!p) return;
    state.presetId = id;
    fCompany.value = p.company;
    fRole.value = p.role;
    fPrompt.value = p.system_prompt;
    $$(".preset").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.id === id)));
    saveDraft();
    if (HAS_GSAP) g.fromTo([fCompany, fRole, fPrompt],
      { backgroundColor: "rgba(255,54,33,.10)" },
      { backgroundColor: "rgba(0,0,0,0)", duration: 0.9, stagger: 0.06, ease: "power2.out", clearProps: "backgroundColor" });
  }

  function saveDraft() {
    store.set("ug_draft", JSON.stringify({
      presetId: state.presetId, company: fCompany.value, role: fRole.value, prompt: fPrompt.value,
    }));
  }

  function loadDraft() {
    const raw = store.get("ug_draft");
    if (!raw) return;
    try {
      const d = JSON.parse(raw);
      if (d.company) fCompany.value = d.company;
      if (d.role) fRole.value = d.role;
      if (d.prompt) fPrompt.value = d.prompt;
      if (d.presetId) {
        state.presetId = d.presetId;
        const b = $$(".preset").find((x) => x.dataset.id === d.presetId);
        if (b) b.setAttribute("aria-pressed", "true");
      }
    } catch { /* ignore corrupt draft */ }
  }

  [fCompany, fRole, fPrompt].forEach((el) => el.addEventListener("input", () => {
    // manual edits clear the "pressed" preset highlight but keep the text
    if (state.presetId) {
      state.presetId = null;
      $$(".preset").forEach((b) => b.setAttribute("aria-pressed", "false"));
    }
    saveDraft();
  }));

  $("#configForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    configError.hidden = true;
    const body = {
      company: fCompany.value.trim(),
      role: fRole.value.trim(),
      system_prompt: fPrompt.value.trim(),
    };
    if (!body.company || !body.role || !body.system_prompt) {
      showConfigError("Company, role, and system prompt are all required.");
      return;
    }
    const btn = $("#btnGenerate");
    btn.disabled = true;
    try {
      const { job_id } = await apiGenerate(body);
      if (!job_id) throw new Error("No job_id returned.");
      startGeneration(job_id, body.company);
    } catch (err) {
      showConfigError(errMsg(err));
      btn.disabled = false;
    }
  });

  function showConfigError(msg) {
    configError.textContent = msg;
    configError.hidden = false;
  }

  /* ---- existing worlds drawer ---- */
  const datasetPanel = $("#datasetPanel"), datasetList = $("#datasetList");
  $("#btnExisting").addEventListener("click", () => {
    const btn = $("#btnExisting");
    const open = datasetPanel.hidden;
    datasetPanel.hidden = !open;
    btn.setAttribute("aria-expanded", String(open));
    if (open) { loadDatasets(); if (HAS_GSAP) animIn([datasetPanel], { y: 10, dur: 0.4 }); }
  });
  $("#datasetRefresh").addEventListener("click", loadDatasets);

  async function loadDatasets() {
    datasetList.innerHTML = `<div class="dataset-loading">Loading worlds…</div>`;
    try {
      const { datasets = [] } = await apiDatasets();
      if (!datasets.length) {
        datasetList.innerHTML = `<div class="dataset-empty">No ready worlds yet. Generate one above.</div>`;
        return;
      }
      datasetList.innerHTML = "";
      datasets.forEach((d) => {
        const item = document.createElement("button");
        item.type = "button";
        item.className = "dataset-item";
        item.setAttribute("role", "listitem");
        item.innerHTML =
          `<div class="dataset-item-main"><b>${esc(d.company_name || "Untitled world")}</b>` +
          `<span>${esc(String(d.data_generation_id || "").slice(0, 18))}…</span></div>` +
          `<div class="dataset-item-counts">` +
            `<div class="dc"><b>${num(d.doc_count)}</b><span>docs</span></div>` +
            `<div class="dc"><b>${num(d.customer_count)}</b><span>callers</span></div>` +
          `</div>`;
        item.addEventListener("click", () => enterExistingWorld(d));
        datasetList.appendChild(item);
      });
    } catch (err) {
      datasetList.innerHTML = `<div class="dataset-empty">Couldn't load worlds — ${esc(errMsg(err))}</div>`;
    }
  }

  function enterExistingWorld(d) {
    state.world = {
      gid: d.data_generation_id,
      company: d.company_name || "Saved world",
      customers: [],
      counts: { docs: d.doc_count, customers: d.customer_count },
      mode: "existing",
    };
    loadExamples(d.data_generation_id);   // grounded prompts for the selected world
    prepareLive();
    goStage("live");
  }

  /* =============================================================================
     STAGE 2 — GENERATE
     ========================================================================== */
  const TL_ORDER = ["drafting", "drafted", "embedding", "saving", "ready"];

  function startGeneration(jobId, company) {
    state.job.id = jobId;
    state.draftDetail = {};
    $("#genCompany").textContent = company;
    $("#summaryCard").hidden = true;
    $("#failCard").hidden = true;
    resetTimeline();
    goStage("generate");
    if (state.job.timer) clearInterval(state.job.timer);
    state.job.timer = setInterval(pollStatus, 1000);
    pollStatus(); // kick immediately
  }

  function resetTimeline() {
    $$(".tl-step").forEach((s) => { s.dataset.state = "pending"; });
  }

  async function pollStatus() {
    if (!state.job.id) return;
    let s;
    try { s = await apiStatus(state.job.id); }
    catch (err) { stopPolling(); showFail(errMsg(err)); return; }

    const stage = s.stage || "queued";
    const detail = s.detail || {};

    if (stage === "failed") {
      stopPolling();
      showFail(s.error || detail.message || detail.error || "Generation failed.");
      return;
    }

    paintTimeline(stage, detail);

    // Transition only when the job is truly done — _store_ready (customers attached)
    // is the sole non-failed writer of done=true. A bare stage==="ready" tick can
    // arrive one Lakebase round-trip before the customer list is ready.
    if (s.done) {
      stopPolling();
      showSummary(s, detail);
    }
  }

  function stopPolling() {
    if (state.job.timer) { clearInterval(state.job.timer); state.job.timer = null; }
  }

  function paintTimeline(stage, detail) {
    const curIdx = TL_ORDER.indexOf(stage);
    // "queued" (curIdx -1): everything pending; the first step reads as active-in-waiting
    TL_ORDER.forEach((key, i) => {
      const step = $(`.tl-step[data-stage-key="${key}"]`);
      if (!step) return;
      let ns;
      if (stage === "ready") ns = "done";
      else if (curIdx === -1) ns = (i === 0 ? "active" : "pending");
      else if (i < curIdx) ns = "done";
      else if (i === curIdx) ns = "active";
      else ns = "pending";
      const prev = step.dataset.state;
      step.dataset.state = ns;
      if (ns !== prev) animateStep(step, ns);   // draw only on the transition, not every poll
    });

    // enrich detail lines with real counts as they arrive
    if (stage === "drafted") {
      state.draftDetail = detail;   // keep records/documents for the ready summary
      const bits = [];
      if (detail.documents != null) bits.push(`${detail.documents} documents`);
      if (detail.customers != null) bits.push(`${detail.customers} customers`);
      if (detail.records != null) bits.push(`${detail.records} records`);
      if (bits.length) setDetail("drafted", bits.join(" · "));
    } else if (stage === "embedding" && detail.count != null) {
      setDetail("embedding", `Embedding ${detail.count} documents…`);
    }
  }
  function setDetail(key, text) {
    const el = $(`.tl-detail[data-detail="${key}"]`);
    if (el) el.textContent = text;
  }

  /* Draw a timeline node's authored icon as it becomes active; draw the check —
     the celebratory one on "ready" — as it completes. Skipped without GSAP;
     CSS state changes still carry the meaning. */
  function animateStep(step, ns) {
    if (!HAS_GSAP) return;
    if (ns === "active") {
      drawIn($(".tl-ic", step), { dur: 0.5, stagger: 0.05, ease: "power3.out" });
    } else if (ns === "done") {
      const isReady = step.dataset.stageKey === "ready";
      drawIn($(".tl-done", step), { dur: isReady ? 0.7 : 0.45, ease: "power3.out" });
      const node = $(".tl-node", step);
      if (node) g.fromTo(node, { scale: isReady ? 0.82 : 0.9 },
        { scale: 1, duration: isReady ? 0.6 : 0.4, ease: "back.out(2)", clearProps: "transform" });
    }
  }

  function showSummary(s, detail) {
    const customers = Array.isArray(s.customers) ? s.customers : [];
    const dd = state.draftDetail || {};
    const gid = s.data_generation_id || detail.data_generation_id || state.job.id;
    const docs = detail.doc_count ?? detail.documents ?? dd.documents ?? null;
    const custN = detail.customer_count ?? customers.length ?? dd.customers ?? null;
    const records = detail.records ?? dd.records ?? null;

    state.world = {
      gid,
      company: $("#genCompany").textContent,
      customers,
      counts: { docs, customers: custN, records },
      mode: "fresh",
    };

    $("#summaryCompany").textContent = state.world.company;
    loadExamples(state.world.gid);   // grounded prompts for the freshly-generated world

    // stats
    const stats = $("#summaryStats");
    stats.innerHTML = "";
    const statDefs = [["Documents", docs], ["Customers", custN], ["Records", records]]
      .filter(([, v]) => v != null);
    if (!statDefs.length) statDefs.push(["Customers", customers.length]);
    statDefs.forEach(([label, val]) => {
      const d = document.createElement("div");
      d.className = "stat";
      d.innerHTML = `<b>0</b><span>${label}</span>`;
      stats.appendChild(d);
      countTo($("b", d), Number(val) || 0);
    });

    // tier breakdown
    const tiers = groupByTier(customers);
    const tw = $("#summaryTiers");
    tw.innerHTML = "";
    ["VIP", "Premium", "Standard"].forEach((tier) => {
      const list = tiers[tier] || [];
      if (!list.length) return;
      const names = list.map((c) => c.display_name).filter(Boolean).slice(0, 3).join(", ");
      const row = document.createElement("div");
      row.className = "tier-row";
      row.innerHTML =
        `<span class="tier-badge" data-tier="${tier}">${tier}</span>` +
        `<span class="tier-row-count">${list.length}</span>` +
        `<span class="tier-row-names">${esc(names)}${list.length > 3 ? "…" : ""}</span>`;
      tw.appendChild(row);
    });

    $("#summaryCard").hidden = false;
    if (HAS_GSAP) animIn([$("#summaryCard")], { y: 20, dur: 0.55 });
  }

  function showFail(message) {
    $("#failMessage").textContent = message;
    $("#failCard").hidden = false;
    if (HAS_GSAP) animIn([$("#failCard")], { y: 16 });
  }

  $("#btnEnterCall").addEventListener("click", () => { prepareLive(); goStage("live"); });
  $("#btnRetry").addEventListener("click", () => {
    $("#failCard").hidden = true;
    $("#btnGenerate").disabled = false;
    goStage("configure");
  });
  $("#btnBackConfig").addEventListener("click", () => {
    $("#failCard").hidden = true;
    $("#btnGenerate").disabled = false;
    goStage("configure");
  });

  function groupByTier(customers) {
    const out = { VIP: [], Premium: [], Standard: [] };
    customers.forEach((c) => { out[canonTier(c.loyalty_tier)].push(c); });
    return out;
  }
  function canonTier(t) {
    const s = String(t || "").toLowerCase();
    if (s.includes("vip")) return "VIP";
    if (s.includes("prem")) return "Premium";
    return "Standard";
  }

  /* =============================================================================
     STAGE 3 — LIVE (caller picker + governed voice call)
     ========================================================================== */
  const callerPicker = $("#callerPicker");
  const btnConnect = $("#btnConnect");
  const connectError = $("#connectError");

  function prepareLive() {
    $("#liveCompany").textContent = state.world.company;
    state.caller = null;
    btnConnect.disabled = true;
    connectError.hidden = true;
    $("#callerSetup").hidden = false;
    $("#callLive").hidden = true;
    buildCallerPicker();
    resetPillars();
    renderExamples();   // reflect whatever examples were loaded for this world
  }

  function buildCallerPicker() {
    callerPicker.innerHTML = "";
    const note = $("#callerNote");

    if (state.world.mode === "fresh" && state.world.customers.length) {
      note.textContent = "Choose who's calling — the tier drives the routing you'll see.";
      const tiers = groupByTier(state.world.customers);
      ["VIP", "Premium", "Standard"].forEach((tier) => {
        const list = tiers[tier];
        if (!list.length) return;
        callerPicker.appendChild(callerGroup(tier, list.map((c) => ({
          kind: "customer", id: c.customer_id, name: c.display_name || c.customer_id, tier,
        }))));
      });
    } else {
      // existing world: no per-caller list in the contract — offer an operator affordance.
      note.textContent = "Paste a known customer ID to demo a specific tier — the governed tier is revealed live in Choice.";
      const manual = document.createElement("div");
      manual.className = "field field--tight";
      manual.innerHTML =
        `<label for="fManual">Known customer ID <span class="field-hint">optional</span></label>` +
        `<input id="fManual" type="text" placeholder="e.g. 6953…-C3" autocomplete="off" />`;
      callerPicker.appendChild(manual);
      $("#fManual", manual).addEventListener("input", (e) => {
        const v = e.target.value.trim();
        if (v) {
          $$(".caller", callerPicker).forEach((c) => c.setAttribute("aria-checked", "false"));
          state.caller = { kind: "manual", id: v, name: "", tier: null };
          btnConnect.disabled = false;
        } else if (state.caller && state.caller.kind === "manual") {
          state.caller = null; btnConnect.disabled = true;
        }
      });
    }

    // Anonymous is always available.
    callerPicker.appendChild(callerGroup("Anonymous", [
      { kind: "anonymous", id: "", name: "Anonymous caller", tier: null },
    ]));

    // radiogroup keyboard nav
    callerPicker.addEventListener("keydown", radioKeyNav);
  }

  function callerGroup(tier, options) {
    const grp = document.createElement("div");
    grp.className = "caller-group";
    grp.dataset.tier = tier;
    grp.innerHTML = `<div class="caller-group-label"><span class="dot"></span>${tier}</div>`;
    const items = document.createElement("div");
    items.className = "caller-group-items";
    options.forEach((opt) => {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "caller";
      b.setAttribute("role", "radio");
      b.setAttribute("aria-checked", "false");
      const initial = (opt.name || "A").trim().charAt(0).toUpperCase() || "A";
      b.innerHTML =
        `<span class="caller-avatar">${esc(initial)}</span>` +
        `<span class="caller-name">${esc(opt.name)}</span>` +
        `<svg class="caller-check" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6L9 17l-5-5"/></svg>`;
      b.addEventListener("click", () => selectCaller(b, opt));
      items.appendChild(b);
    });
    grp.appendChild(items);
    return grp;
  }

  function selectCaller(btn, opt) {
    $$(".caller", callerPicker).forEach((c) => c.setAttribute("aria-checked", "false"));
    btn.setAttribute("aria-checked", "true");
    const manual = $("#fManual");
    if (manual) manual.value = "";
    state.caller = opt;
    btnConnect.disabled = false;
    btn.focus();
  }

  function radioKeyNav(e) {
    if (!["ArrowDown", "ArrowUp", "ArrowLeft", "ArrowRight"].includes(e.key)) return;
    const radios = $$(".caller", callerPicker);
    const cur = radios.indexOf(document.activeElement);
    if (cur === -1) return;
    e.preventDefault();
    const next = (e.key === "ArrowDown" || e.key === "ArrowRight")
      ? (cur + 1) % radios.length : (cur - 1 + radios.length) % radios.length;
    radios[next].focus();
    radios[next].click();
  }

  /* ---- connect / call lifecycle --------------------------------------- */
  btnConnect.addEventListener("click", connect);
  $("#btnEnd").addEventListener("click", () => endCall(true));
  $("#btnLiveBack").addEventListener("click", () => { endCall(false); goStage("configure"); });

  async function connect() {
    if (!state.caller) return;
    connectError.hidden = true;
    btnConnect.disabled = true;
    btnConnect.classList.add("is-connecting");   // "reaching out" pulse on the CTA
    setConn("connecting", "Connecting…");
    try {
      const t = await apiToken(state.world.gid, state.caller.id, $("#fName").value.trim());

      const room = new LK.Room({ adaptiveStream: true, dynacast: true });
      state.room = room;

      // --- preserved working wiring ---
      room.on(LK.RoomEvent.TrackSubscribed, (track) => {
        if (track.kind === "audio") {
          const el = track.attach();
          el.autoplay = true;
          document.body.appendChild(el);
        }
      });
      room.on(LK.RoomEvent.DataReceived, (payload, participant, kind, topic) => {
        handleEvidence(payload, topic);
      });
      room.on(LK.RoomEvent.TranscriptionReceived, (segments, participant) => {
        handleTranscription(segments, participant);
      });

      // --- added: connection / speakers / disconnect ---
      room.on(LK.RoomEvent.ConnectionStateChanged, (s) => onConnState(s));
      room.on(LK.RoomEvent.ActiveSpeakersChanged, (speakers) => onSpeakers(speakers));
      room.on(LK.RoomEvent.Disconnected, () => { if (state.stage === "live") resetCallUI(); });

      await room.connect(t.serverUrl, t.token);
      await room.localParticipant.setMicrophoneEnabled(true);

      enterCallUI(t);
      startMicMeter();
    } catch (err) {
      setConn("failed", "Connection failed");
      btnConnect.classList.remove("is-connecting");
      connectError.textContent = errMsg(err);
      connectError.hidden = false;
      btnConnect.disabled = false;
      state.room = null;
    }
  }

  function enterCallUI(token) {
    $("#callerSetup").hidden = true;
    $("#callLive").hidden = false;
    btnConnect.classList.remove("is-connecting");
    document.body.dataset.connected = "true";
    setConn("connected", "Connected · live");

    const name = state.caller.name || token.name || "Anonymous caller";
    $("#callerLiveName").textContent = state.caller.kind === "anonymous" ? "Anonymous caller" : (name || "Caller");
    $("#callerAvatar").textContent = (name || "A").trim().charAt(0).toUpperCase() || "A";
    // operator-facing hint only; actual governed tier is confirmed live via `bind`
    $("#callerLiveTier").textContent = state.caller.tier
      ? `Selected as ${state.caller.tier} · confirmed live`
      : "Governed tier resolves on connect";

    if (HAS_GSAP) animIn([$("#callLive")], { y: 12, dur: 0.5 });
    startMicSignal();   // broadcast arcs draw on, then breathe while live
  }

  function endCall(toSetup) {
    try { if (state.room) state.room.disconnect(); } catch { /* noop */ }
    state.room = null;
    stopMicMeter();
    if (toSetup) resetCallUI();
  }

  function resetCallUI() {
    document.body.dataset.connected = "false";
    setConn("idle", "Not connected");
    btnConnect.classList.remove("is-connecting");
    $("#callLive").hidden = true;
    $("#callerSetup").hidden = false;
    btnConnect.disabled = !state.caller;
    onSpeakers([]);
    stopMicSignal();
  }

  function setConn(stateName, label) {
    const chip = $("#connChip");
    chip.dataset.state = stateName;
    $(".conn-label", chip).textContent = label;
  }

  function onConnState(s) {
    const v = String(s).toLowerCase();
    if (v.includes("reconnect")) setConn("reconnecting", "Reconnecting…");
    else if (v.includes("connected")) setConn("connected", "Connected · live");
    else if (v.includes("connecting")) setConn("connecting", "Connecting…");
    else if (v.includes("disconnected")) { if (document.body.dataset.connected === "true") setConn("idle", "Call ended"); }
  }

  function onSpeakers(speakers) {
    let agent = false, you = false;
    (speakers || []).forEach((p) => { if (p.isAgent) agent = true; if (p.isLocal) you = true; });
    $("#spkAgent").dataset.active = String(agent);
    $("#spkYou").dataset.active = String(you);
  }

  /* =============================================================================
     EVIDENCE (topic "ug_evidence") — feeds Choice / Control / Context / Costs
     ========================================================================== */
  function handleEvidence(payload, topic) {
    if (topic && topic !== "ug_evidence") return; // lenient: act only on our topic when known
    let obj;
    try { obj = JSON.parse(new TextDecoder().decode(payload)); }
    catch { return; } // ignore non-JSON, per contract
    if (!obj || obj.type !== "ug_evidence") return;
    if (obj.bind) applyBind(obj.bind);
    if (obj.retrieval) applyRetrieval(obj.retrieval);
    if (obj.usage) applyUsage(obj.usage);
  }

  function applyBind(bind) {
    // CHOICE: governed tier -> routed model (operator view)
    $("#choiceAwait").hidden = true;
    $(".choice-live", $("#pChoice")).hidden = false;
    if (bind.tier) $("#choiceTier").textContent = canonTier(bind.tier);
    if (bind.model) $("#choiceModel").textContent = bind.model;
    if (bind.company) $("#choiceCompany").textContent = `Bound for ${bind.company}`;
    if (HAS_GSAP) {
      g.fromTo($(".route", $("#pChoice")), { scale: 0.96, opacity: 0.4 },
        { scale: 1, opacity: 1, duration: 0.5, ease: "back.out(1.6)", clearProps: "transform" });
      // the route locks in: the connector draws left→right and the shackle closes
      const lock = $(".route-lock");
      if (lock) {
        drawIn($$(".rl-arrow, .rl-shackle", lock), { dur: 0.55, stagger: 0.14, delay: 0.08, ease: "power3.out" });
        g.fromTo($$(".rl-lock-fill", lock), { opacity: 0 },
          { opacity: 1, duration: 0.5, delay: 0.12, ease: "power2.out", clearProps: "opacity" });
      }
    }

    // CONTROL: render governed directives
    if (bind.directives) renderDirectives(bind.directives);

    // COSTS: derive indicative cost tier from the bound model
    if (bind.model) applyCost(bind.model);
  }

  const DIRECTIVE_LABELS = {
    recognition_tone: "Recognition tone",
    be_proactive: "Be proactive",
    thoroughness: "Thoroughness",
    offer_human_escalation: "Human escalation",
  };
  function renderDirectives(d) {
    const wrap = $("#directives");
    wrap.innerHTML = "";
    const grid = document.createElement("div");
    grid.className = "directive-grid";
    Object.keys(DIRECTIVE_LABELS).forEach((key) => {
      if (!(key in d)) return;
      const val = d[key];
      const isBool = typeof val === "boolean";
      const cell = document.createElement("div");
      cell.className = "directive";
      const shown = isBool ? (val ? "Yes" : "No") : String(val);
      cell.innerHTML = `<span class="dk">${DIRECTIVE_LABELS[key]}</span>` +
        `<span class="dv"${isBool ? ` data-bool="${val}"` : ""}>${esc(shown)}</span>`;
      grid.appendChild(cell);
    });
    // include any extra directive keys the backend sends, defensively
    Object.keys(d).forEach((key) => {
      if (key in DIRECTIVE_LABELS) return;
      const cell = document.createElement("div");
      cell.className = "directive";
      cell.innerHTML = `<span class="dk">${esc(pretty(key))}</span><span class="dv">${esc(String(d[key]))}</span>`;
      grid.appendChild(cell);
    });
    wrap.appendChild(grid);
    if (HAS_GSAP) animIn($$(".directive", grid), { y: 8, stagger: 0.05, dur: 0.4 });
  }

  function applyCost(model) {
    state.boundModel = model;
    const info = COST[model];
    const bars = $("#costBars");
    bars.dataset.level = info ? String(info.level) : "0";
    bars.title = info ? `${info.label} cost · typical for ${info.tierHint}` : "Cost tier unmapped for this model";
    // COST METER: fill length = indicative level (dashoffset 96=empty, 0=full).
    // The CSS transition animates it smoothly with or without GSAP.
    const fill = $("#costFill");
    if (fill) fill.style.strokeDashoffset = String(Math.round(96 * (1 - (info ? info.level : 0) / 3)));
    renderProjection();
  }

  function applyRetrieval(r) {
    const feed = $("#retrievalFeed");
    const await0 = $(".await", feed);
    if (await0) await0.remove();

    state.retrievals += 1;
    $("#retrievalCount").textContent = String(state.retrievals);
    // CONTEXT: each real retrieval pings the scan motif + pops the count
    if (HAS_GSAP) {
      const pl = $("#retrievalPulse");
      if (pl) pulse($$(".cp-wave", pl), { from: 0.4, dur: 0.7, stagger: 0.1, origin: "12 12" });
      g.fromTo($("#retrievalCount"), { scale: 1.35 }, { scale: 1, duration: 0.45, ease: "back.out(2)", clearProps: "transform" });
    }

    const kind = (r.kind === "record") ? "record" : "semantic";
    const item = document.createElement("div");
    item.className = "retrieval-item";
    let html = `<div class="retrieval-item-top"><span class="rkind" data-kind="${kind}">${kind}</span>`;
    html += `<span class="rhits">${hitCount(r.hits)} hits</span></div>`;
    if (r.query) html += `<div class="rquery">${esc(String(r.query))}</div>`;
    const titles = hitTitles(r.hits);
    if (titles.length) {
      html += `<ul class="rhit-list">${titles.map((t) => `<li>${esc(t)}</li>`).join("")}</ul>`;
    }
    item.innerHTML = html;
    feed.prepend(item);
    // transform/opacity only — never animate layout height (no reflow jank)
    if (HAS_GSAP) g.fromTo(item, { opacity: 0, y: -8 },
      { opacity: 1, y: 0, duration: 0.45, ease: "power3.out", clearProps: "transform" });
  }

  function hitCount(hits) {
    if (Array.isArray(hits)) return hits.length;
    if (typeof hits === "number") return hits;
    if (hits && typeof hits === "object" && typeof hits.count === "number") return hits.count;
    return 0;
  }
  function hitTitles(hits) {
    if (!Array.isArray(hits)) return [];
    return hits.slice(0, 3).map((h) => {
      if (typeof h === "string") return h;
      if (h && typeof h === "object") return h.title || h.name || h.id || h.text || h.chunk || JSON.stringify(h);
      return String(h);
    }).filter(Boolean);
  }

  /* =============================================================================
     TRANSCRIPT
     ========================================================================== */
  function handleTranscription(segments, participant) {
    const isAgent = !!(participant && participant.isAgent);
    const who = isAgent ? "agent" : "you";
    const feed = $("#transcript");
    const await0 = $(".await", feed);
    if (await0) await0.remove();

    const nearBottom = feed.scrollHeight - feed.scrollTop - feed.clientHeight < 60;

    (segments || []).forEach((seg) => {
      const id = seg.id || `${who}-${seg.startTime || Date.now()}`;
      let el = state.turns.get(id);
      if (!el) {
        el = document.createElement("div");
        el.className = "turn";
        el.dataset.who = who;
        el.innerHTML = `<span class="turn-who">${who === "agent" ? "Agent" : "Caller"}</span><span class="turn-text"></span>`;
        feed.appendChild(el);
        state.turns.set(id, el);
      }
      $(".turn-text", el).textContent = seg.text || "";
      el.dataset.interim = seg.final === false ? "true" : "false";
    });

    if (nearBottom) feed.scrollTop = feed.scrollHeight;
  }

  function applyUsage(u) {
    const inTok = Math.max(0, Math.round(u.input_tokens || 0));
    const outTok = Math.max(0, Math.round(u.output_tokens || 0));
    const total = inTok + outTok;
    const prev = (state.metrics.inTok || 0) + (state.metrics.outTok || 0);
    state.metrics.inTok = inTok;
    state.metrics.outTok = outTok;
    state.metrics.perTurn.push(Math.max(0, total - prev));
    if (state.metrics.perTurn.length > 24) state.metrics.perTurn.shift();
    countTo($("#tokensVal"), total);
    renderTurnSpark();
    renderProjection();
  }

  function renderProjection() {
    const el = $("#costProj"), rateEl = $("#costRate");
    if (!el) return;
    const total = (state.metrics.inTok || 0) + (state.metrics.outTok || 0);
    const info = COST[state.boundModel];
    if (!total) { el.textContent = "—"; rateEl.textContent = ""; return; }
    const rate = info ? info.rate : null;
    const cost = rate ? (total / 1e6) * rate : null;
    el.textContent = cost == null ? "n/a" : cost < 1 ? `$${cost.toFixed(4)}` : `$${cost.toFixed(2)}`;
    rateEl.textContent = rate ? `@ $${rate}/1M tok` : "";
  }

  /* Per-turn token trace as an authored SVG sparkline (area + line + newest dot).
     Built to measured pixel dims so the dot stays round and the stroke crisp;
     the line draws on once, later turns pop the latest point. Real data only. */
  function renderTurnSpark() {
    const spark = $("#turnSpark");
    if (!spark) return;
    const arr = state.metrics.perTurn || [];
    if (!arr.length) { spark.innerHTML = ""; state.metrics.sparkSeen = false; return; }
    const W = Math.max(60, Math.round(spark.clientWidth || 160));
    const H = Math.max(24, Math.round(spark.clientHeight || 44));
    const pad = 3, max = Math.max(...arr, 1), n = arr.length;
    const xy = arr.map((v, i) => {
      const x = n === 1 ? W - pad : pad + (i / (n - 1)) * (W - pad * 2);
      const y = H - pad - (v / max) * (H - pad * 2);
      return [Math.round(x * 10) / 10, Math.round(y * 10) / 10];
    });
    const line = xy.map((p) => p.join(",")).join(" ");
    const area = `${xy[0][0]},${H} ${line} ${xy[n - 1][0]},${H}`;
    const lx = xy[n - 1][0], ly = xy[n - 1][1];
    spark.innerHTML =
      `<svg class="draw-svg" viewBox="0 0 ${W} ${H}" width="100%" height="100%" aria-hidden="true">` +
        `<polygon class="sp-area" points="${area}" fill="currentColor" stroke="none"/>` +
        `<polyline class="sp-line" points="${line}" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>` +
        `<circle class="sp-dot" cx="${lx}" cy="${ly}" r="2.8" fill="currentColor" stroke="none"/>` +
      `</svg>`;
    if (!HAS_GSAP) return;
    const lineEl = $(".sp-line", spark), dot = $(".sp-dot", spark), areaEl = $(".sp-area", spark);
    if (!state.metrics.sparkSeen) { drawIn([lineEl], { dur: 0.6, ease: "power2.out" }); state.metrics.sparkSeen = true; }
    if (areaEl) g.fromTo(areaEl, { opacity: 0 }, { opacity: 0.14, duration: 0.5, ease: "power2.out", clearProps: "opacity" });
    if (dot) g.fromTo(dot, { scale: 0 }, { scale: 1, duration: 0.45, ease: "back.out(2.4)", svgOrigin: `${lx} ${ly}`, clearProps: "transform" });
  }

  /* =============================================================================
     LIVE MIC METER — real input level via WebAudio (graceful fallback)
     ========================================================================== */
  function startMicMeter() {
    stopMicMeter();
    try {
      const pub = state.room.localParticipant.getTrackPublication(LK.Track.Source.Microphone);
      const mst = pub && pub.track && pub.track.mediaStreamTrack;
      if (!mst) return;
      const AC = window.AudioContext || window.webkitAudioContext;
      const ctx = new AC();
      const src = ctx.createMediaStreamSource(new MediaStream([mst]));
      const analyser = ctx.createAnalyser();
      analyser.fftSize = 64;
      analyser.smoothingTimeConstant = 0.75;
      src.connect(analyser);
      const bins = new Uint8Array(analyser.frequencyBinCount);
      const bars = $$("#micMeter span");
      state.audio.ctx = ctx;
      state.audio.els = bars;

      const tick = () => {
        analyser.getByteFrequencyData(bins);
        const step = Math.floor(bins.length / bars.length) || 1;
        bars.forEach((bar, i) => {
          const v = bins[i * step] / 255;
          bar.style.transform = `scaleY(${Math.max(0.14, v)})`;
          bar.style.opacity = String(0.35 + v * 0.65);
        });
        state.audio.raf = requestAnimationFrame(tick);
      };
      tick();
    } catch { /* WebAudio blocked — legend still driven by ActiveSpeakers */ }
  }

  function stopMicMeter() {
    if (state.audio.raf) cancelAnimationFrame(state.audio.raf);
    state.audio.raf = null;
    if (state.audio.ctx) { try { state.audio.ctx.close(); } catch { /* noop */ } state.audio.ctx = null; }
    $$("#micMeter span").forEach((b) => { b.style.transform = "scaleY(0.14)"; b.style.opacity = "0.35"; });
    stopMicSignal();
  }

  /* Broadcast motif: draws its arcs on connect, then breathes while live —
     a "connected, listening" companion to the input meter. Transform/opacity
     only, killed on disconnect; static (drawn) under reduced motion / no GSAP. */
  function startMicSignal() {
    stopMicSignal();
    const sig = $(".mic-signal svg");
    if (!sig) return;
    drawIn($$(".msg-arc", sig), { dur: 0.5, stagger: 0.08, ease: "power3.out" });
    if (!HAS_GSAP) return;
    const arcs = $$(".msg-arc", sig);
    if (arcs.length) motion.mic = g.to(arcs,
      { opacity: 0.4, duration: 1.4, repeat: -1, yoyo: true, ease: "sine.inOut", stagger: 0.15 });
  }
  function stopMicSignal() {
    if (motion.mic) { motion.mic.kill(); motion.mic = null; }
    const arcs = $$(".mic-signal .msg-arc");
    if (HAS_GSAP && arcs.length) g.set(arcs, { opacity: 1, clearProps: "opacity" });
  }

  /* ---- pillar reset between calls -------------------------------------- */
  function resetPillars() {
    $("#choiceAwait").hidden = false;
    $(".choice-live", $("#pChoice")).hidden = true;
    $("#choiceTier").textContent = "—";
    $("#choiceModel").textContent = "—";
    $("#choiceCompany").textContent = "";
    $("#directives").innerHTML = `<p class="directives-await">Directives resolve on connect…</p>`;
    $("#retrievalFeed").innerHTML = `<div class="await">No retrievals yet. Ask the assistant a question.</div>`;
    $("#retrievalCount").textContent = "0";
    $("#costBars").dataset.level = "0";
    const cf = $("#costFill"); if (cf) cf.style.strokeDashoffset = "96";   // meter back to empty
    $("#tokensVal").textContent = "—";
    $("#costProj").textContent = "—";
    $("#costRate").textContent = "";
    $("#turnSpark").innerHTML = "";
    state.retrievals = 0;
    state.metrics = { inTok: 0, outTok: 0, perTurn: [] };
    state.turns.clear();
    $("#transcript").innerHTML = `<div class="await">The conversation will appear here once you connect.</div>`;
  }

  /* =============================================================================
     TRY ASKING — grounded read-aloud prompts (GET /api/examples)
     ========================================================================== */
  async function loadExamples(gid) {
    state.examples = [];
    renderExamples();                 // hide immediately so no stale chips flash
    if (!gid) return;
    const qs = await apiExamples(gid);
    // a newer world may have been chosen while this was in flight — ignore if so
    if (state.world && String(state.world.gid) === String(gid)) {
      state.examples = qs;
      renderExamples();
    }
  }

  function renderExamples() {
    const panel = $("#askPanel"), wrap = $("#askChips"), copied = $("#askCopied");
    if (!panel || !wrap) return;
    wrap.innerHTML = "";
    if (copied) copied.textContent = "";
    const qs = state.examples || [];
    if (!qs.length) { panel.hidden = true; return; }
    qs.forEach((q) => {
      const chip = document.createElement("button");
      chip.type = "button";
      chip.className = "ask-chip";
      chip.innerHTML =
        `<svg class="ask-chip-ic" viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20.5 11.3a7.5 7.5 0 0 1-8 7.7 8.9 8.9 0 0 1-3.4-.7L4 19.5l1.3-4.4a7.5 7.5 0 1 1 15.2-3.8z"/></svg>` +
        `<span class="ask-chip-t">${esc(q)}</span>`;
      chip.addEventListener("click", () => copyExample(chip, q));
      wrap.appendChild(chip);
    });
    panel.hidden = false;
    if (HAS_GSAP) animIn($$(".ask-chip", wrap), { y: 8, stagger: 0.05, dur: 0.4 });
  }

  let copyTimer = null;
  function copyExample(chip, text) {
    // display-only by contract; copying is a nice-to-have and must never throw
    try {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(() => flagCopied(chip)).catch(() => {});
      }
    } catch { /* clipboard unavailable — chips stay display-only */ }
  }
  function flagCopied(chip) {
    $$("#askChips .ask-chip").forEach((c) => { c.dataset.copied = "false"; });
    chip.dataset.copied = "true";
    const c = $("#askCopied");
    if (c) c.textContent = "Copied to clipboard";
    clearTimeout(copyTimer);
    copyTimer = setTimeout(() => {
      chip.dataset.copied = "false";
      if (c) c.textContent = "";
    }, 1600);
  }

  /* =============================================================================
     UTILITIES
     ========================================================================== */
  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }
  function num(v) { return (v == null || isNaN(v)) ? "—" : String(v); }
  function pretty(k) { return String(k).replace(/[_-]+/g, " ").replace(/\b\w/g, (c) => c.toUpperCase()); }
  function errMsg(e) { return (e && e.message) ? e.message : String(e); }

  /* =============================================================================
     INIT
     ========================================================================== */
  function init() {
    document.body.dataset.stage = "configure";
    document.body.dataset.connected = "false";
    initTheme();
    renderPresets();
    loadDraft();
    setStepper("configure");
    // intro reveal
    animIn($$("[data-anim]"), { y: 22, stagger: 0.08, dur: 0.7, delay: 0.05 });
    if (HAS_GSAP) {
      g.to(".bg-aurora--a", { xPercent: 8, yPercent: 6, duration: 18, repeat: -1, yoyo: true, ease: "sine.inOut" });
      g.to(".bg-aurora--b", { xPercent: -6, yPercent: -8, duration: 22, repeat: -1, yoyo: true, ease: "sine.inOut" });
    }
    startHeroMotif();   // draws the governed-voice motif, then loops its signal packet
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
