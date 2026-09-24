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
  };

  /* =============================================================================
     PRESETS — voice-friendly system prompts, "answer only from tools; never invent".
     ========================================================================== */
  const PRESETS = [
    {
      id: "cascade", company: "Cascade Airlines", domain: "Airline support",
      role: "Airline support agent",
      system_prompt:
        "You are the voice assistant for Cascade Airlines. Answer only from your tools and " +
        "retrieved policies — never invent fares, schedules, baggage rules, or account details. " +
        "If a tool returns nothing, offer to connect a human agent. Reply in one or two brief, " +
        "calm spoken sentences. Never state or guess a caller's loyalty tier or status.",
    },
    {
      id: "northwind", company: "Northwind Outfitters", domain: "Outdoor gear",
      role: "Outdoor gear support agent",
      system_prompt:
        "You are the voice assistant for Northwind Outfitters, an outdoor gear retailer. Answer " +
        "only from your tools and product knowledge — never invent specs, prices, stock, or return " +
        "terms. If you can't find it, offer to connect a specialist. Keep replies to one or two " +
        "short spoken sentences, friendly and practical. Never mention a caller's loyalty tier.",
    },
    {
      id: "meridian", company: "Meridian Bank", domain: "Retail banking",
      role: "Retail banking assistant",
      system_prompt:
        "You are the voice assistant for Meridian Bank. Answer only from your tools and retrieved " +
        "policies — never invent rates, fees, balances, or account details. For anything sensitive " +
        "or unavailable, offer to connect a banker. Reply in one or two brief, precise spoken " +
        "sentences. Never state or guess a caller's tier, eligibility, or account status.",
    },
    {
      id: "lumen", company: "Lumen Mobile", domain: "Telecom",
      role: "Mobile support agent",
      system_prompt:
        "You are the voice assistant for Lumen Mobile. Answer only from your tools and retrieved " +
        "plans and policies — never invent prices, data allowances, coverage, or account details. " +
        "If a tool has no answer, offer to connect support. Keep replies to one or two short spoken " +
        "sentences, clear and upbeat. Never mention a caller's loyalty tier or account status.",
    },
    {
      id: "forge", company: "Forge Analytics", domain: "B2B SaaS",
      role: "Customer support engineer",
      system_prompt:
        "You are the voice assistant for Forge Analytics, a B2B analytics platform. Answer only " +
        "from your tools and product documentation — never invent features, limits, pricing, or " +
        "account details. If it isn't documented, offer to connect a support engineer. Reply in one " +
        "or two brief spoken sentences, professional and concise. Never state or guess a customer's plan tier.",
    },
  ];

  /* Model -> indicative cost tier (client-derived; labelled indicative in UI). */
  const COST = {
    "system.ai.gpt-6-sol":  { label: "High",   tierHint: "VIP",      level: 3 },
    "system.ai.gpt-5-5":    { label: "Medium", tierHint: "Premium",  level: 2 },
    "system.ai.gpt-5-nano": { label: "Low",    tierHint: "Standard", level: 1 },
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
      { backgroundColor: "rgba(63,224,197,.10)" },
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

    if (stage === "ready" || s.done) {
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
      if (stage === "ready") step.dataset.state = "done";
      else if (curIdx === -1) step.dataset.state = (i === 0 ? "active" : "pending");
      else if (i < curIdx) step.dataset.state = "done";
      else if (i === curIdx) step.dataset.state = "active";
      else step.dataset.state = "pending";
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
      connectError.textContent = errMsg(err);
      connectError.hidden = false;
      btnConnect.disabled = false;
      state.room = null;
    }
  }

  function enterCallUI(token) {
    $("#callerSetup").hidden = true;
    $("#callLive").hidden = false;
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
    $("#callLive").hidden = true;
    $("#callerSetup").hidden = false;
    btnConnect.disabled = !state.caller;
    onSpeakers([]);
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
  }

  function applyBind(bind) {
    // CHOICE: governed tier -> routed model (operator view)
    $("#choiceAwait").hidden = true;
    $(".choice-live", $("#pChoice")).hidden = false;
    if (bind.tier) $("#choiceTier").textContent = canonTier(bind.tier);
    if (bind.model) $("#choiceModel").textContent = bind.model;
    if (bind.company) $("#choiceCompany").textContent = `Bound for ${bind.company}`;
    if (HAS_GSAP) g.fromTo($(".route", $("#pChoice")), { scale: 0.96, opacity: 0.4 },
      { scale: 1, opacity: 1, duration: 0.5, ease: "back.out(1.6)", clearProps: "transform" });

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
  }

  function applyRetrieval(r) {
    const feed = $("#retrievalFeed");
    const await0 = $(".await", feed);
    if (await0) await0.remove();

    state.retrievals += 1;
    $("#retrievalCount").textContent = String(state.retrievals);

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
    if (HAS_GSAP) g.fromTo(item, { opacity: 0, y: -10, height: 0 },
      { opacity: 1, y: 0, height: "auto", duration: 0.45, ease: "power3.out", clearProps: "height,transform" });
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
     TRANSCRIPT + per-turn latency (client measurement)
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

      // --- latency: from caller's FINAL segment to first agent segment of the reply ---
      if (!isAgent && seg.final === true) {
        state.metrics.t0 = performance.now();
        state.metrics.awaiting = true;
      } else if (isAgent && state.metrics.awaiting && state.metrics.t0 != null) {
        recordLatency(performance.now() - state.metrics.t0);
        state.metrics.awaiting = false;
        state.metrics.t0 = null;
      }
    });

    if (nearBottom) feed.scrollTop = feed.scrollHeight;
  }

  function recordLatency(ms) {
    const v = Math.max(0, Math.round(ms));
    state.metrics.latencies.push(v);
    if (state.metrics.latencies.length > 24) state.metrics.latencies.shift();
    countTo($("#latencyVal"), v);
    renderSpark();
  }

  function renderSpark() {
    const spark = $("#latencySpark");
    const arr = state.metrics.latencies;
    const max = Math.max(...arr, 1);
    spark.innerHTML = "";
    arr.forEach((v) => {
      const b = document.createElement("span");
      b.style.transform = `scaleY(${Math.max(0.08, v / max)})`;
      spark.appendChild(b);
    });
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
    $("#latencyVal").textContent = "—";
    $("#latencySpark").innerHTML = "";
    state.retrievals = 0;
    state.metrics = { t0: null, awaiting: false, latencies: [] };
    state.turns.clear();
    $("#transcript").innerHTML = `<div class="await">The conversation will appear here once you connect.</div>`;
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
    renderPresets();
    loadDraft();
    setStepper("configure");
    // intro reveal
    animIn($$("[data-anim]"), { y: 22, stagger: 0.08, dur: 0.7, delay: 0.05 });
    if (HAS_GSAP) {
      g.to(".bg-aurora--a", { xPercent: 8, yPercent: 6, duration: 18, repeat: -1, yoyo: true, ease: "sine.inOut" });
      g.to(".bg-aurora--b", { xPercent: -6, yPercent: -8, duration: 22, repeat: -1, yoyo: true, ease: "sine.inOut" });
    }
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
