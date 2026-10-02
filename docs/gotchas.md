# Gotchas — UG Voice Studio

Running list of non-obvious traps, platform constraints, and corrections hit while building
the Voice Studio. **Append a new entry whenever a design review, verification pass, or docs
check turns up something that would surprise the next engineer.** Keep each entry to: the trap →
the consequence → what to do, with a source anchor.

Legend: ✅ verified against installed source/repo this session · 📄 from vendor docs · ⚠️ unverified / to confirm.

---

## LiveKit (livekit-agents 1.5.6)

- **LiveKit's managed "Expressive mode" requires LiveKit Inference — we deliberately do NOT use it.** 📄
  The feature at `docs.livekit.io/agents/models/tts/expressive` needs an `inference.TTS` and only supports
  Fish Audio / Inworld / Cartesia / Gemini / SpaceXAI — **ElevenLabs is not available**, and the TTS routes
  through LiveKit's hosted gateway. Plan 5 hand-rolls expressive tags instead: the Databricks LLM emits them,
  a custom `tts_text_transforms` pipeline protects them, and the `livekit-plugins-elevenlabs` provider plugin
  (BYO `ELEVEN_API_KEY` → `api.elevenlabs.io`) performs them. **Do not "simplify" by switching to `inference.TTS`** —
  it breaks the Databricks-inference boundary and drops ElevenLabs. (Decision locked 2026-10-02; this is why the
  §7.8 tag pipeline exists.)
- **`filter_markdown` stalls streaming TTS on a bare `[`.** ✅ The stock filter holds back the rest of the reply
  when it sees a `[` that isn't a complete `[text](url)` link — so bare `[whispers]` would freeze audio until
  stream end. Run the tag-aware encode→`filter_markdown`→`filter_emoji`→decode pipeline (spec §7.8) so no bare `[`
  reaches it. (`filters.py:79-85,124-130`)
- **No runtime TTS swap.** ✅ `AgentSession.tts` is getter-only; there is no `update_tts()`; `Agent.update_options(tts=)`
  does not exist at 1.5.6. Change voices by overriding `tts_node`, never by mutating the session. (`agent_session.py:1597`)
- **The session closes on the 4th consecutive LLM/TTS unrecoverable error, not the 3rd.** ✅ `max_unrecoverable_errors=3`,
  but the guard closes only when the count *exceeds* 3 (off-by-one). Keep the Halloween TTS **outside** the session's
  error count (own its failures via `mark_degraded`) so a vendor hiccup can't kill the call. (`agent_session.py:131,1370-1406`)
- **`on_user_turn_completed` is awaited before the reply, and an exception there drops the whole turn.** ✅ The AI-Decide
  hook must be time-bounded (≤ cue-wait) and must never raise. `turn_ctx` is a per-reply temp copy: patch it for a
  same-turn switch, and also call `update_instructions` for later turns. (`agent_activity.py:1964-1977,373-388`)
- **Text transforms apply to the TTS branch only; the transcript branch gets raw LLM text.** ✅ Tags must be stripped
  separately in `transcription_node` (and again client-side). (`agent_activity.py:2407-2419,2495`)

## Databricks platform

- **No native TTS, and no realtime STT.** 📄 FMAPI is chat/reasoning/embedding/image only; the AI Gateway exposes no
  audio paths. Batch STT exists (`ai_transcribe`, Beta, SQL-only) but nothing streaming. Voice I/O must be vendor-direct
  (Deepgram STT, ElevenLabs TTS). The only Databricks-side TTS path is self-serving an OSS model on GPU Model Serving
  (cold starts "one to several minutes", no documented audio streaming) — impractical for a realtime turn.
- **`ai_decide` REST response has no `error_message` field.** 📄 That field belongs to the SQL VARIANT envelope, not the
  REST response (which documents only `response` + `metadata.version`). Detect failure as: non-2xx HTTP, null/missing
  `response`, unknown label, or confidence outside 0–1.
- **`ai_decide` question types are `noul` / `choice` / `score` — there is no `bool`.** 📄 The probability type is `noul`.
  Never write `"type":"bool"` in code or the plan.
- **`ai_decide` is Beta, Previews-gated, and region-limited.** 📄 Must be enabled per-workspace (admin → Previews).
  Supported AWS regions include us-east-1/-2 and us-west-2 (our workspace is us-east-1). `options.version:"1.0"` pins the
  **function API**, not the model — the served model can change and probability calibration can drift without a version
  bump, so re-check thresholds after any change.
- **No official `ai_decide` latency or pricing.** 📄⚠️ The 0.8–2 s figure circulating is measured on TypeSafe's *Jev*,
  not Databricks-hosted `ai_decide`. Keep a p50/p95 measurement gate in discovery; neither pricing page lists `ai_decide` yet.
- **Claude models on the gateway reject `response_format: json_object`** (they accept `json_schema` only). 📄 Keep the
  `uaig_chat` fallback decider on a GPT-family model. Also: `/ai-gateway/openai/v1/chat/completions` with bare
  `databricks-gpt-*` names is used in-repo but only `/responses` + `/embeddings` were formally verified in discovery —
  live-test chat-completions or use `/ai-gateway/mlflow/v1`.

## Repo (voice-agents-ug-demo)

- **`src/services/gateway.post()` is sync-only** (blocking `requests`). ✅ Don't call it (or `uaig_chat.complete_json`)
  from the async turn hook — it blocks the event loop. The decider must use its own `httpx.AsyncClient` (httpx is already pinned).
- **`_SPAN_TYPES` (`app/tracing.py:25`) has no `CHAIN` entry, and the exporter JSON-encodes the value.** ✅ To tag the
  `ug.ai_decide` span `mlflow.spanType="CHAIN"`, add a `"ug.ai_decide": "CHAIN"` entry to the map — don't set a bare raw string.
- **The evidence router dispatches on fragment keys, not a type discriminator.** ✅ `handleEvidence` (`studio.js:944-953`)
  checks `obj.bind` / `obj.retrieval` / `obj.usage`. A new `voice_mode` payload needs its own `if (obj.voice_mode) applyVoiceMode(...)` branch.
- **The transcript renders `seg.text` verbatim with no stripping today.** ✅ `handleTranscription` (`studio.js:1077-1105`)
  sets `textContent = seg.text` directly — add client-side tag stripping there as the belt-and-braces layer.

## Worktrees & deploy

- **Apps runtime is Python 3.11; local `.venv` is 3.12.** Recompile `agent-requirements.txt` for **3.11** (including the
  ElevenLabs plugin + its `codecs` extra) or the deploy build breaks.
- **`uv.lock` diverges across worktrees.** The main checkout has an uncommitted `uv.lock` change on `plan-4-frontend`;
  Plan 5 relocks in its own worktree — reconcile at merge.
- **The git stash stack is shared across all worktrees.** Never use bare `git stash` / `git stash pop` (you could pop
  another session's work) — prefer a WIP commit, or `git stash push -u -m "<unique-tag>"` then `apply <sha>`.
- **New secrets aren't declared in `app.yaml` directly.** ✅ `valueFrom:` points at an **app resource key** bound to the
  `ug-voice-studio` scope out-of-band. Adding `ELEVEN_API_KEY` = create the secret in the scope + attach an app resource
  (`elevenlabs-api-key`) + add the `valueFrom` entry in `app.yaml`.
