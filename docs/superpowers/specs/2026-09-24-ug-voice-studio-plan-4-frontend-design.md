# UAIG Voice Studio — Plan 4: Polished Front End + In-App Story Generation

**Goal:** Replace the throwaway test harness with a demo-grade "studio" that (1) lets a
presenter configure a company/assistant, (2) generates its synthetic *world* in-app with
narrated progress, then (3) drops into a live governed voice call with a four-pillar
(**Choice / Control / Context / Costs**) evidence dashboard. Local-first; deploy later.

This doc is the CONTRACT both the front-end and backend work build against. It is the
single source of truth for the API shapes and the UI stages.

## Design decisions (locked)

- **Single-page staged SPA:** Configure → Generating → Live, with GSAP stage transitions.
- **Aesthetic:** dark, premium, startup-grade. Purposeful motion. **Real data only** — no
  fabricated metrics. Every number on screen traces to the agent, the gateway, or a client
  measurement.
- **Local-first:** served by the existing stdlib web tier (`app/web_server.py`) on a free
  port (default 8760; `:8000` collides with the local model proxy). Structured so a
  Databricks App wrapper can come later.
- **Generation runs in the web tier** (background thread), narrated to the UI via polling.
- **Costs pillar is derived client-side** from a model→cost-index map + measured per-turn
  latency. No agent changes required for it.
- **Reuse fallback:** presenters can also pick an already-generated world (demo reliability
  if live generation is slow or rate-limited).

## Governance invariants (unchanged, must hold)

- The caller never hears their loyalty tier. The **operator dashboard** MAY show tier→model
  (it is the presenter's view, not the caller's).
- The agent still never receives the raw tier; routing stays a governed Lakebase lookup.
- No secrets in the SPA. Tokens are minted server-side only.

## API contract (web tier)

```
GET  /                         -> the studio SPA (index.html + assets in app/web/public/)
GET  /api/datasets             -> {datasets: [{data_generation_id, company_name, status,
                                   doc_count, customer_count, created_at}]}
                                   ready-only, newest first
POST /api/generate             body {company, role, system_prompt}
                                   -> 202 {job_id}   (starts background generation)
GET  /api/generate/status?job_id=...
                                   -> {stage, detail, done, error,
                                       data_generation_id?, customers?}
     stage in: queued | drafting | drafted | embedding | saving | ready | failed
     detail: {documents?, customers?, records?, count?, message?}
     when stage == "ready": data_generation_id + customers:
        [{customer_id, display_name, loyalty_tier}]   (so the UI offers tier picks)
GET  /api/token?dataset=&customer=&name=
                                   -> {serverUrl, roomName, identity, token, name,
                                       data_generation_id, customer_id}   (EXISTING; unchanged)
```

Job store is in-memory (dict + lock), keyed by `job_id`. A background thread runs
`asyncio.run(...)` over `create_pool()` + `generate_dataset(..., progress=cb)`; the callback
writes stage/detail into the job store; on ready it also queries `ug.customers` for the new
`data_generation_id` and stores `[{customer_id, display_name, loyalty_tier}]`.

## Evidence channel (LiveKit data, topic `ug_evidence`) — EXISTING, consumed by the Live screen

```
{type:"ug_evidence", bind:{company, tier, model,
   directives:{recognition_tone, be_proactive, thoroughness, offer_human_escalation}}}
{type:"ug_evidence", retrieval:{kind:"semantic"|"record", query?, hits}}
```
Plus `RoomEvent.TranscriptionReceived` for the transcript, `RoomEvent.TrackSubscribed`
(audio) and connection-state events.

## Backend change: `generate_dataset` progress hook (`src/generate.py`)

Add an optional keyword `progress: Callable[[str, dict], None] | None = None` to
`generate_dataset`. Emit, in order:
- `("drafting", {})` — before the LLM draft (`complete_json`)
- `("drafted", {"documents": n, "customers": n, "records": n})` — after `build_rows`
- `("embedding", {"count": n})` — before `embed_texts`
- `("saving", {})` — before the insert transaction
- `("ready", {"data_generation_id": gid, "doc_count": n, "customer_count": n})` — after commit
On failure (existing except branch): `("failed", {"error": str(exc)})` before re-raising.
Backward compatible: default `None` → no callback. Never let a progress callback exception
break generation (wrap callback invocations).

## Presets (Configure screen)

Five one-click presets, each prefilling `{company, role, system_prompt}`, plus a fully
custom option:
- **Cascade Airlines** (airline support) · **Northwind Outfitters** (outdoor gear) ·
  **Meridian Bank** (retail banking) · **Lumen Mobile** (telecom) · **Forge Analytics** (B2B SaaS).
System prompts: concise, voice-friendly, "answer only from tools; never invent."

## UI stages

**Configure** — hero with the four-pillar brand strip; preset chips; company / role /
system-prompt fields; primary CTA "Generate the world"; secondary "use an existing world"
(populated from `/api/datasets`).

**Generating** — a narrated stage timeline mapped to the progress stages
(drafting policies & FAQ → creating customers across Standard/Premium/VIP → embedding
documents → ready), animated with GSAP. On ready: a summary card (company, N docs,
N customers broken down by tier, N records) and an "Enter the call" CTA.

**Live** — caller picker (the generated customers grouped by tier; picking VIP vs Standard
is how the presenter shows routing) + optional courtesy name; "Connect & talk". Then the
four-pillar live dashboard + a live transcript + connection/mic state:
- **Choice** — routed model + the governed tier that chose it (operator view).
- **Control** — tier came from the governed Lakebase lookup, not the conversation;
  a "stated status ignored" indicator; tools are read-only.
- **Context** — live retrieval events (semantic / record hits) as they stream in.
- **Costs** — model cost-index (map model→relative cost) + per-turn latency (client-measured).

## Build constraints

- External libs: **GSAP from cdnjs or jsdelivr only.** Everything else inline or vendored in
  `app/web/public/`. (The page already vendors `livekit-client`.)
- Files: the front end lives under `app/web/public/` (`index.html` + `studio.css` +
  `studio.js`, or inline — builder's call). It REPLACES the throwaway harness.
- The Live screen keeps the working token→dispatch→data-channel flow from the current
  `index.html` (that part is verified working); redesign the presentation around it.
- Tests (backend): `generate_dataset` progress ordering (mock `complete_json` / `embed_texts`
  / pool); web_server job-store + `/api/generate/status` shape + `/api/datasets` (stubbed
  generator/pool). Full suite must stay green.
- **Subagents implement + test only; they DO NOT commit.** The orchestrator verifies the
  whole suite and commits sequentially (avoids concurrent-git races).
