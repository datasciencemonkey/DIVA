# Unity Gateway Voice Studio — Design Spec

**Date:** 2026-09-24
**Status:** Draft for review (brainstorming → design gate)
**Working dir:** `voice-agents-ug-demo`
**Reference primitive:** `<reference-app>`, read-only.

---

## 1. Summary

A **governed voice-agent "studio"** that presents **Unity AI Gateway (UAIG)** through the lens of a
customer-support voice agent. You type a **company name, an assistant role, and a system prompt**,
flip a **"Generate data"** toggle, and the app synthesizes a small dataset (support docs + customer
records) into **one shared, generic Lakebase schema**, tagged with a **`data_generation_id`**. A
LiveKit voice agent — LLM served through **UAIG** — then answers questions for that company using
**generic, read-only tools** (semantic + exact search on Lakebase) scoped to that id. The caller's
**loyalty tier (Standard / Premium / VIP)**, read from a **governed lookup at session bind**, selects
**which UAIG-served model handles the call** (the "bake in") and sets LLM-safe treatment directives.
Every turn is traced to **MLflow / Unity Catalog** via OTel. A **brand-new, animated UI** narrates the
four pillars — **Choice, Control, Context, Costs** — live during the call.

The demo is **reusable**: swapping the company is a new prompt + a data generation, not a code change.

## 2. Goals & non-goals

**Goals (v1 build):**
- G1. Configurable studio: company name + assistant role + system prompt + "Generate data" toggle → a
  synthetic dataset in shared tables under a new `data_generation_id`.
- G2. Generic, read-only tool surface (`semantic_search`, `record_lookup`) scoped by `data_generation_id`.
- G3. Loyalty → model routing across **3 tiers** (Standard / Premium / VIP), decided by a **governed
  Lakebase lookup at session bind**, never by what the caller says.
- G4. Brand-new animated web UI (impeccable + GSAP) organized around **Choice / Control / Context / Costs**.
- G5. OTel → UC/MLflow tracing (async, post-call), carrying `ug.*` attributes.
- G6. Single-container Databricks App deploy, reusing ReferenceApp's proven boot/deploy patterns.
- G7. Governance invariants preserved end to end (see §12).

**Non-goals (v1) — narrated, not built:**
- Phone / SIP dial-in (web/app WebRTC first; "same agent also on the phone" is narrated).
- Governed **write** actions (v1 is read-only Q&A + lookup; the write invariant is preserved for a later phase).
- Tool calls as governed **MCP** services through UAIG (LLM goes through UAIG — that *is* the governed
  path shown; "managed MCP tool calls" is narrated as productionization).
- Downstream analytics loop: knowledge-extraction → UC Delta → **Genie Ontology → Genie One →
  dashboards / agents / apps**. Trace + table schema are designed to feed it; an **optional single Genie
  aggregate** may ship as the "reuse proof" (as ReferenceApp did).
- Local-language support; fine-tuning ASR/LLM/TTS; multi-cloud / sovereign-DC deployment; Lakebase read
  replicas; LakeFlow-Connect ingestion (the generator is the demo stand-in for content prep).

## 3. The demo narrative (the four pillars)

| Pillar | What it shows | Data source |
|---|---|---|
| **Choice** | The UAIG-served model this tier routed to — and what the other tiers *would* have used. | Routing decision at bind (§11). |
| **Control** | Governed caller identity (bound, not spoken); the routing/treatment decision runs *outside* the LLM; the system-prompt guardrails; UAIG rate/budget governance. | Bind + policy + narrated UAIG governance. |
| **Context** | Exactly what `semantic_search` / `record_lookup` returned to ground each answer. | Tool outputs streamed to the UI (§15). |
| **Costs** | Running tokens + \$ per turn and per call for the routed model, with a cross-tier comparison ("this call on VIP vs Standard"). | Per-turn LLM usage × a per-model price table (§15, risk R3). |

## 4. Reference architecture (from the provided diagram) → our build

- **Consumer → WebRTC backend → LiveKit SFU agent worker** = web tier + agent worker (built).
- **3p/hosted ASR + TTS** = Deepgram STT/TTS, **cascade** pipeline (built).
- **AI gateway (MCP/LLMs), "managed tool calls"** = **UAIG**: LLM path **+ 3-tier model selection** (built;
  MCP-governed tools narrated).
- **Lakebase read replica(s) — "LAKEBASE SEARCH FOR INSTANT IQ (BM25, Sem Search)"** = native **Lakebase
  Search** for content retrieval + SQL for structured lookup, single instance in v1 (built; verify R1).
- **UC OTEL Delta Table (via zerobus) → MLflow Tracing/Evals** = tracing sink (built).
- **Content hydration (LF Connect + ETL → Ingestion/Index Updates)** = the **"Generate data"** generator
  (demo stand-in, built).
- **Knowledge extraction → UC Delta → Genie Ontology → Genie One → Agents/Dashboards/Apps** = downstream
  loop (**narrated**; optional Genie aggregate).
- **"Sovereign DC or Databricks"** = deployment-choice story (positioning only).

## 5. What we reuse from ReferenceApp vs build new

**Reuse (verbatim or lightly adapted) — grounded in code:**
- Tracing seam: `app/agent.py::_SpanEnrichmentExporter`, `_build_tracer_provider`, `_fill_trace_identity`,
  `_fill_conversation_preview` — reuse verbatim; swap the `referenceapp.*` enrichment for `ug.*` (§14).
- OTLP export target: `{host}/api/2.0/otel/v1/traces` with header
  `X-Databricks-UC-Table-Name: {catalog}.{schema}.{prefix}_otel_spans` (fail-soft when trace vars unset).
- Warm Lakebase pool before `session.start()`: `app/agent.py::_create_session_pool` + shutdown close;
  pool/auth pattern from `src/services/recovery.py::create_recovery_pool`.
- Speech + LLM stack: `AgentSession(stt=deepgram.STT("nova-3"), tts=deepgram.TTS("aura-2-…"),
  llm=openai.responses.LLM(base_url=f"{host}/ai-gateway/openai/v1", use_websocket=False, store=False),
  max_tool_steps=5)` — Silero VAD auto-provisioned. **LLM `model=` becomes per-session (§11).**
- Token-minting web tier (stdlib HS256 JWT, unique room per visit, token-embedded dispatch, input
  sanitization): `app/web_server.py::mint_token`, `clean_first_name`. **Extend the token to carry
  `data_generation_id` + `customer_id`** (§10).
- Single-container deploy: `app/start_app.py`, root `app.yaml`, `app/agent-requirements.txt`, and every
  §5-notes trap (port 8000; private `/tmp/agent-venv`; auto-installer-avoiding manifest name;
  `sync --exclude` the real manifests; pop `DATABRICKS_CLIENT_ID/SECRET` at boot; secrets via `valueFrom`).
- Degraded pattern: `app/simulation.py` shape (run `pool=None`, keep model + routing real).
- Governance eval structure: `eval/scenarios.py`, `eval/run_eval.py`.

**Build new:**
- The generic Lakebase schema (§8) + the **data generator** (§9).
- The **loyalty → model routing** policy + per-session model selection (§11) — the primary new capability.
- Generic **read-only** tools `semantic_search` / `record_lookup` (§13), replacing ReferenceApp's domain tools.
- **Lakebase Search** integration for retrieval (replaces ReferenceApp's served-model hot path; verify R1).
- The **brand-new animated UI** (§15) — ReferenceApp's `evidence.js` panel is *reference only*.
- Per-dataset **system prompt** loading + a fixed governance-guardrail wrapper (§10, §12).

**Dropped in v1:** ReferenceApp's served predictive model on the hot path (flight delay), its write action
(`hold_recovery_option`), and its airline domain tools. Retrieval is the new hot path.

## 6. Architecture & request lifecycle

```text
┌── STUDIO (config page, same web app) ─────────────────────────────────────────┐
│ company_name + assistant_role + system_prompt + [toggle] Generate data        │
│   POST /api/generate → generator (agent-venv): UAIG LLM drafts docs+records,  │
│     FMAPI embeds docs, writes rows tagged data_generation_id to Lakebase,     │
│     builds the Lakebase Search index, registers row in `datasets`             │
│   → returns data_generation_id                                                │
└───────────────────────────────────────────────────────────────────────────────┘
        │  Start call: pick/generate dataset + "call as" (customer) / type name
        ▼
Web tier  GET /api/token?dataset=&customer=&name=
   → mint_token: unique room; token carries data_generation_id + customer_id + courtesy name
        │  WebRTC media ⇄ LiveKit Cloud ⇄ agent worker
        ▼
Agent worker entrypoint (REORDERED vs ReferenceApp — routing needs identity first):
   1. build tracer (fail-soft); warm Lakebase pool
   2. ctx.connect(); await wait_for_participant()          ← identity known here
   3. read data_generation_id + customer_id + name from the participant token
   4. GOVERNED loyalty lookup: read_loyalty_context(pool, customer_id, data_generation_id) → tier
   5. route_for(tier) → { model, treatment directives }    ← DETERMINISTIC, OUTSIDE the LLM
   6. load system_prompt for data_generation_id + append fixed governance wrapper
   7. build AgentSession(llm=openai.responses.LLM(model=<routed model>, …), tools scoped to id)
   8. session.start(); governed greeting (courtesy name; warm tone only if directive says so)
   9. per turn: semantic_search / record_lookup (scoped) → grounding; evidence → 4-C's UI
  10. OTel spans → UC/MLflow at shutdown (async, fail-soft)
```

**Key adaptation (R4 to verify):** ReferenceApp builds `AgentSession` *before* `ctx.connect()`. Because the
routed model depends on the bound tier (known only after the participant joins), we **construct
`AgentSession` after `wait_for_participant()`**. Verify livekit-agents 1.5.6 permits this ordering
(connect first, then build+start), which ReferenceApp already relies on for `ctx.connect()` preceding
`session.start()`.

## 7. Speech stack (cascade)

Deepgram `nova-3` STT + Deepgram `aura-2-…` TTS; Silero VAD auto-provisioned; LLM =
`openai.responses.LLM` over UAIG (`/ai-gateway/openai/v1`, `use_websocket=False`, `store=False`).
**Responses API (not Chat Completions)** — reasoning models reject function tools on `/chat/completions`
(notes §7). Every tier model must support Responses passthrough **with function calling** (risk R2).

## 8. Generic Lakebase schema (partitioned by `data_generation_id`)

All tables carry `data_generation_id`; every tool filters on it → clean multi-scenario isolation.

- **`datasets`** — registry: `data_generation_id` (PK), `company_name`, `assistant_role`,
  `system_prompt`, `generator_model`, `created_at`, `status`, `doc_count`, `customer_count`.
- **`documents`** — semantic corpus: `data_generation_id`, `doc_id`, `title`, `chunk_text`,
  `embedding` (vector), `metadata`. Backed by a **Lakebase Search index** (BM25 + semantic; R1).
- **`customers`** — records + governance signal: `data_generation_id`, `customer_id`, `display_name`,
  `loyalty_tier ∈ {Standard, Premium, VIP}`, business attributes (status, tenure, etc.), `scored_at`.
- **`records`** — generic child rows for exact lookup (orders / cases / tickets — shape driven by the
  generated company): `data_generation_id`, `record_id`, `customer_id`, typed fields, `status`.
- **`otel_spans`** (UC, not Lakebase) — the trace sink table (`{catalog}.{schema}.{prefix}_otel_spans`).

Everything the agent reads on the hot path is in **Lakebase**; OTel spans land in **UC** async post-call.

## 9. Data generation (studio + generator)

- **Studio UI** (a page in the same web app): three text fields + a "Generate data" toggle. Submitting
  with the toggle on calls **`POST /api/generate`**.
- **Generator** (`generate.py`, runnable via `uv run` for dev; invoked by the app as an agent-venv
  subprocess so the stdlib web tier stays dependency-free — R5): 
  1. mint a fresh `data_generation_id`;
  2. **UAIG LLM** drafts ~5–8 support/policy/FAQ docs + ~15–40 customers spread across all three tiers
     + a handful of child records, all consistent with `{company, role, system_prompt}`;
  3. **FMAPI embeddings** (via the gateway) vectorize doc chunks;
  4. write all rows (tagged with the id) to Lakebase and build/refresh the Lakebase Search index;
  5. register the row in `datasets`; return the id.
- Small data → seconds to generate. All data is **clearly labeled synthetic**. The generator **dogfoods
  UAIG** for both drafting and embeddings — itself part of the story.

## 10. Session binding, identity, governed loyalty lookup

- **Identity is a governed selection carried on the token, never inferred by the LLM.** The caller UI
  offers a "call as" picker of generated customers (each with a known tier) and/or a typed courtesy name.
  The web tier puts `data_generation_id` + `customer_id` (when picked) + sanitized `name` on the token
  (`clean_first_name` reused; add an id sanitizer).
- At bind the agent resolves the customer: use `customer_id` when present; else a **deterministic DB
  match** of the typed name against this dataset's customers (a governed lookup at bind, outside the LLM);
  else default to **Standard** tier + a **default opening question**.
- `read_loyalty_context(pool, customer_id, data_generation_id) -> LoyaltyContext` (**never raises**; safe
  `Standard`/`stale=True` default on miss/error) — mirrors `src/services/value_context.py`.
- The per-dataset `system_prompt` becomes the agent instructions, **always wrapped with a fixed
  governance preamble/suffix** (§12) so a custom prompt cannot dilute the invariants. The courtesy name is
  appended as data, "never as instructions" (ReferenceApp's exact framing).

## 11. Loyalty → model routing (the new core)

Pure, TDD-tested policy, analogous to `src/policy/value_risk.py::treatment_for`:

```python
# policy/routing.py  (pure, no I/O, unit-tested)
TIER_MODEL_MAP = {   # from env/secret; EACH verified Responses+tools-compatible (R2)
    "Standard": os.getenv("UG_MODEL_STANDARD"),   # fast / low-cost FMAPI model
    "Premium":  os.getenv("UG_MODEL_PREMIUM"),    # mid capability
    "VIP":      os.getenv("UG_MODEL_VIP"),         # top reasoning model
}
UG_MODEL_FALLBACK = os.getenv("UG_MODEL_FALLBACK")  # known-good default

@dataclass(frozen=True)
class RoutingDecision:
    tier: str
    model: str
    directives: dict   # LLM-safe: recognition_tone, be_proactive, thoroughness,
                       # offer_human_escalation … — NEVER the raw tier

def route_for(loyalty_tier: str) -> RoutingDecision: ...
```

- The entrypoint sets `llm=openai.responses.LLM(model=decision.model, …)` per session (§6 step 7).
- Directives steer *behavior* only (tone/proactivity/verbosity/escalation), exactly as ReferenceApp's
  `_directives`; the **raw tier never reaches the LLM**.
- If the routed model fails a live Responses+tools check, fall back to `UG_MODEL_FALLBACK` and surface the
  fallback in the Control pillar.
- **Pluggable strategy (future-proofing):** `route_for` is the single routing seam. v1 ships a
  `StaticTierStrategy` (the tier→model map above). A future **model-based strategy** — a served "decision
  model" (see *Future extensions*) — drops in behind the same `RoutingDecision` contract without touching
  the agent, tools, or UI.

## 12. Governance invariants (adapted from ReferenceApp §4 — DO NOT DILUTE)

1. **Decision deterministic + outside the LLM:** `route_for(tier)` (model + directives) is a pure function.
2. **LLM sees directives + retrieved context, never raw signals:** the loyalty tier, and any tier field
   in a record, are never surfaced to the model; `record_lookup` returns business fields only.
3. **Spoken/typed name is courtesy-only:** sanitized (`clean_first_name`), never a lookup or auth key; it
   neutralizes prompt-injection via the name.
4. **Tier from a governed lookup, never from conversation:** the wrapped system prompt forbids changing
   treatment/tone/framing based on any status the caller states.
5. **Read-only v1:** no writes, so no blast radius. (The typed/reversible/idempotent/confirmed write
   invariant is retained in the design for a later phase.)
6. **Action boundary in code/grants/network:** the app identity is scoped to the demo catalog/schema and
   the Lakebase database; tools only ever read within the bound `data_generation_id`.
7. **Stale/missing → abstain, never fabricate:** retrieval miss → "I don't have that information";
   unknown caller → Standard default + default question; the agent never invents company facts.
8. **Observable without leaking signals:** traces + UI carry `data_generation_id`, tier, routed model,
   and retrieval refs — the raw tier is a bind-time signal, surfaced as a band/label, never as a reason
   the LLM can act on.

## 13. Generic tool surface (read-only)

- **`semantic_search(query: str)`** → Lakebase Search over `documents WHERE data_generation_id = <bound>`
  (hybrid BM25 + semantic), returns top-k `{doc_id, title, snippet, score}`. (Context)
- **`record_lookup(key_or_query: str)`** → SQL over `customers` / `records WHERE data_generation_id =
  <bound>`. For "my …" questions it is keyed by the bound `customer_id`; otherwise it looks up the
  referenced record within the dataset. Returns business fields only — **never** the governance tier. (Context)
- Both are `function_tool`s built by a `build_tools(pool, session_ctx)` seam (mirrors `app/tools.py`), with
  the bound `data_generation_id` on `session_ctx`. Each hop publishes a privacy-clean fragment to the
  evidence sink for the Context pillar (reuse `_make_evidence_sink` shape, new payload).
- `max_tool_steps=5` retained.

## 14. Tracing → UC / MLflow (+ downstream, narrated)

- Reuse the OTel seam verbatim; swap enrichment for `ug.*`: `ug.data_generation_id`, `ug.company`,
  `ug.loyalty_tier` (band label), `ug.routed_model`, `ug.retrieval` (doc ids / scores), plus the standard
  span-type map (`agent_session→AGENT`, `llm_node→LLM`, `function_tool→TOOL`) and conversation previews.
- Spans export async at shutdown to the UC OTEL Delta table; **fail-soft** (disabled if trace vars unset).
- **Downstream (narrated):** the UC OTEL table + generated UC tables feed knowledge-extraction →
  Genie Ontology → Genie One / dashboards. Optional: one small Genie aggregate as the reuse proof.

## 15. The four-C's UI (brand-new; impeccable + GSAP)

- **Aesthetic:** forward-thinking, minimal, startup-grade, motion-forward (GSAP). Designed via the
  **impeccable** skill in the implementation phase; ReferenceApp's `evidence.js` is reference only.
- **Information architecture:** a company/caller header; the live transcript; and the four-pillar panel
  (Choice / Control / Context / Costs) updating in real time. A studio/config view for company setup +
  "Generate data" + "call as".
- **Data channel:** reuse ReferenceApp's LiveKit `publish_data` evidence mechanism (reliable packets, merged
  partial updates) with a new `ug_evidence` payload: routing decision (Choice), identity/policy/guardrail
  facts (Control), per-turn retrieval (Context), per-turn + cumulative token/\$ (Costs).
- **Costs data (R3):** capture per-turn usage from the Responses API via `livekit-plugins-openai`; cost =
  tokens × a per-model price table (config, labeled illustrative); show a cross-tier comparison.
- **Build/deploy:** served as static assets by the stdlib web tier (vendored GSAP; no heavy framework
  required, light build acceptable) so it ships inside the single container.

## 16. Deploy + configuration

- **Single-container Databricks App** (web tier + agent worker), reusing `start_app.py` + root `app.yaml`
  + `agent-requirements.txt`, and every §5-notes trap.
- **Secrets via `valueFrom`** (scope chosen by the user, e.g. a fresh scope; do not reuse an existing scope
  blindly). Surface (superset of ReferenceApp's): LiveKit url/key/secret; Deepgram key; Databricks host/token;
  Lakebase endpoint/database; trace catalog/schema/prefix; otel service name; **tier→model map
  (`UG_MODEL_STANDARD/PREMIUM/VIP` + `UG_MODEL_FALLBACK`)**; **per-model price table**.
- **Profile is user-chosen; never auto-selected.** Run everything with `uv`.

**Prerequisites (confirm before build):**
- A **Lakebase-capable workspace** — ReferenceApp's `your-workspace` could *not* provision serverless Postgres and ran
  degraded; the studio needs Lakebase for semantic + exact + tier lookup. Confirm the target profile
  provides Lakebase, or budget for degraded/simulated mode.
- **UAIG serves the 3 tier models** on that workspace, each Responses-passthrough + function-calling
  compatible (R2). **FMAPI embeddings** available for generation.
- **Lakebase Search** available/GA on that workspace (R1).

## 17. Error / degraded modes

- **No Lakebase** → degraded mode (`simulation.py` pattern): run `pool=None` on a seeded in-memory
  dataset; keep model routing + LLM real.
- **Routed model incompatible/unavailable** → fall back to `UG_MODEL_FALLBACK`; surface in Control.
- **Retrieval miss / empty** → agent abstains (no fabrication).
- **Unknown caller** → Standard tier + default opening question.
- **Generation failure** → return a clear error; the studio keeps prior datasets; never ship half-written
  rows (transactional per `data_generation_id`, or mark `status='failed'`).
- **Tracing/evidence** → both fail-soft; never disrupt the voice pipeline.

## 18. Testing & governance eval

- **TDD (pure logic):** `route_for(tier)` — tier→model + directives mapping; deterministic + fallback.
- **Multi-tenant isolation:** tools never read across `data_generation_id` (property test over 2+ datasets).
- **Generation smoke:** valid embeddable docs + customers spanning all 3 tiers + records; idempotent per id.
- **Governance eval catalog (adapt `eval/scenarios.py`):** stated-status doesn't change treatment
  ("I'm VIP" → no change); prompt injection via name and via retrieved doc content (content is data, not
  instructions); unknown caller → Standard + default; retrieval miss → abstain; custom system prompt
  cannot override the governance wrapper.
- **Retrieval sanity:** `semantic_search` returns on-topic chunks for seeded questions.

## 19. Risks & discovery (must-verify before/within implementation)

- **R1 — Lakebase Search:** confirm current API / GA status and whether embeddings are internal or need
  FMAPI (blog: *announcing-lakebase-search-agent-native-retrieval-built-lakebase-postgres*). Fallback:
  hand-rolled `pgvector` + `tsvector` hybrid.
- **R2 — Per-model Responses+tools passthrough:** verify each tier model on the target gateway; resolve
  ReferenceApp's §7 model-config inconsistency; choose tier models from the verified set; wire a fallback.
- **R3 — Cost telemetry:** verify `livekit-plugins-openai` surfaces per-turn Responses usage; else
  estimate. Maintain a per-model price table; label costs illustrative.
- **R4 — Entrypoint reordering:** verify AgentSession can be built after `ctx.connect()` +
  `wait_for_participant()` in livekit-agents 1.5.6.
- **R5 — In-app generation trigger:** endpoint→subprocess (`uv run generate.py`) vs Databricks Job; keep
  the web tier stdlib-only.
- **R6 — Lakebase-capable workspace:** confirm the chosen profile; else degraded mode.
- **R7 — OTel ingest mechanism:** ReferenceApp uses the OTLP `/api/2.0/otel/v1/traces` endpoint; the diagram says
  "zerobus." Confirm the current recommended path; either lands spans in UC.

## 20. Open decisions deferred to the plan

- Exact tier→model choices (pending R2 discovery on the target workspace).
- Whether to ship the optional Genie aggregate reuse-proof in v1.
- UI build tooling (pure-stdlib build vs light bundler) — decided with the impeccable/frontend pass.
- Generator prompt design + how much record structure to synthesize per company.

## Future extensions (post-v1)

- **Model-based routing — a served "decision model" (e.g. *JEV*).** Replace `StaticTierStrategy` with a
  governed served model that chooses the conversational model from richer features (loyalty tier + query
  intent/complexity + cost/latency budget), returning the same `RoutingDecision`.
  - **Design fit:** ReferenceApp already runs a **served UC model → deterministic decision** on the hot path
    (flight-delay model → rules); the router revives that seam, pointed at model selection.
  - **Governed & on-brand:** the router is itself a UAIG / Model-Serving-hosted model — another governed
    model in the story, strengthening Choice + Control + Costs. Remains **app-side** routing (UAIG is not
    claimed to do the classification itself — §positioning).
  - **Invariant preserved:** the decision stays **outside the conversational LLM** and auditable; the
    conversational LLM never sees the raw tier/signals.
  - **Timing:** per-session at bind (drop-in to the v1 flow) first; **per-turn** dynamic routing later
    (easy turns → cheap model, hard turns → strong — the "cascade" idea), which requires swapping the
    session LLM mid-call and is materially more complex.
  - **Traced + evaluated:** log the router's inputs / decision / confidence to the OTel/UC trace and
    evaluate routing quality (cost saved vs quality held) with MLflow evals.
  - **Fallback:** low confidence / router unavailable → fall back to `StaticTierStrategy` (already built),
    so v1's policy is the safety net.
- **Governed write action** (typed / reversible / idempotent / confirmed) — re-introduce ReferenceApp
  invariants #5/#6.
- **Phone / SIP dial-in**, local-language support, and the downstream Genie / dashboards loop (§2 non-goals).

## 21. Component map (new repo, patterns from ReferenceApp)

| Concern | Path (this repo) | Source pattern |
|---|---|---|
| Agent worker + entrypoint (reordered) | `app/agent.py` | ReferenceApp `app/agent.py` |
| Generic tools (read-only) | `app/tools.py` | ReferenceApp `app/tools.py` shape |
| Routing policy (pure) | `policy/routing.py` | ReferenceApp `src/policy/value_risk.py` |
| Loyalty context reader | `services/loyalty_context.py` | ReferenceApp `src/services/value_context.py` |
| Retrieval (Lakebase Search + SQL) | `services/retrieval.py` | new (R1) |
| Lakebase pool | `services/db.py` | ReferenceApp `src/services/recovery.py` (pool/auth) |
| Data generator | `generate.py` | new (dogfoods UAIG) |
| Web tier (token minting) | `app/web_server.py` | ReferenceApp `app/web_server.py` |
| Studio + 4-C's UI | `app/web/*` | new (impeccable + GSAP) |
| Tracing seam | in `app/agent.py` | ReferenceApp `_SpanEnrichmentExporter` |
| Single-container boot | `app/start_app.py`, `app.yaml`, `app/agent-requirements.txt` | ReferenceApp verbatim + traps |
| Degraded mode | `app/simulation.py` | ReferenceApp pattern |
| Eval | `eval/*` | ReferenceApp `eval/*` |

---

*Bottom line: keep ReferenceApp's governed skeleton and invariants, replace its domain tools with a
data-driven generic layer scoped by `data_generation_id`, add loyalty→model routing as the headline
UAIG capability, and present it through a brand-new animated four-pillars UI.*
