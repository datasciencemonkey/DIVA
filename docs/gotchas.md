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

## ElevenLabs / expressive-TTS

- **RESOLVED 2026-10-02 — bumped the livekit stack to 1.8.3** (commit `18c0b54`). 1.8.3 is the latest version common to all five
  livekit packages on the Databricks mirror `<internal-pypi-mirror>`; **pypi.org is blocked from the sandbox**, and the only
  "1.8.4" on the mirror is the unrelated `livekit-plugins-bithuman`. 1.8.3 adds the Text-to-Dialogue WebSocket, so the Halloween model
  is **`eleven_v3_conversational`** (streams audio tags at ~280 ms — meets the ≤400 ms gate), **not `eleven_v4_turbo`** (the 1.8.3 plugin
  has no `eleven_v4*` routing; v4 would need 1.8.4+, unavailable here). `UG_HALLOWEEN_TTS_MODEL` default = `eleven_v3_conversational`.
- **The bump forced extra pins:** `livekit` 1.1.5→1.1.18, `livekit-api` 1.1.0→1.2.1 (hard deps of agents 1.8.3). Re-bumping the livekit
  stack later is a ~7-pin edit.
- **`openai` SDK was downgraded 3.14→2.54** (livekit-agents 1.8.3 requires `openai<3`). The repo doesn't import `openai` directly and the
  suite is green, but the live UAIG Responses path hasn't run on 2.x — **verify against the live gateway at the live step.**
- **C1–C13 anchors now sit on 1.8.3 source** — the 1.5.6 line numbers in spec §4 are stale; re-verification against 1.8.3 is in progress.

### Original 1.5.6 limitation (kept as the "why")

- **Inline tags do NOT stream at `livekit-plugins-elevenlabs==1.5.6`.** 📄 The plugin's `.stream()` speaks only the
  `multi-stream-input` WebSocket, which doesn't carry the tag-performing models. `eleven_v3` / `eleven_v3_conversational` /
  `eleven_v4` / `eleven_v4_turbo` need the **Text-to-Dialogue** WebSocket — added in **livekit-agents 1.7.1**
  (v3/v3_conversational, ~280 ms TTFA) and **1.8.4** (v4/v4_turbo, ~100 ms). At 1.5.6 you can only *stream* Flash/turbo
  (no tags), or run `eleven_v3` over **HTTP `/stream` per-sentence** (~0.7–1.9 s TTFA — **fails** the ≤400 ms R8 gate).
  ⇒ **Streamed inline tags require bumping agents+plugins to ≥1.7.1 / 1.8.4, which forces re-verifying the C1–C13 1.5.6
  source anchors.** (Decision pending 2026-10-02.)
- **Missing `ELEVEN_API_KEY` raises `ValueError` at construction** — check the env before constructing, don't rely on a lazy failure.
- **`prewarm()` is a no-op on the ElevenLabs plugin** — the spec's "cue-time prewarm" has no effect; the WebSocket opens lazily on first `.stream()`.
- **A profile-owned TTS is not wired to the session.** The session subscribes `error`/`metrics_collected` and calls `prewarm()`
  only on its *own* `activity.tts`. For a profile-owned TTS: subscribe to its `error`/`metrics` yourself, build the
  `StreamAdapter` once, and `aclose()` it at shutdown (`StreamAdapter.aclose()` does NOT close the wrapped TTS).
- **Two `SPOOKY_TAGS` are undocumented** (`nervously`, `inhales deeply`); ElevenLabs documents `[whispers]`/`[laughs]`/`[sighs]`/`[exhales]`/`[mischievously]`, and v3 sometimes *speaks* a tag aloud — test each tag per voice/model (Task 1).
- **Low R13 risk:** the plugin adds no new transitive deps beyond numpy (`av` is already a core dep).

## LiveKit 1.8.3 re-verification (supersedes the stale 1.5.6 §4 anchors)

Re-verified against installed 1.8.3 (2026-10-02). C3–C13 + text-transform/PUA behavior all HOLD (new file:lines in the SDD ledger table); only C1 & C2 changed behavior, plus a new native subsystem:

- **NEW native `expressive` subsystem — keep it OFF (`expressive=False`).** 1.8.3 adds an `expressive: bool|ExpressiveOptions` kwarg (default False) on `AgentSession`/`Agent`/`update_options` that injects an LLM markup instruction, converts `<expression>/<sound>` XML tags → `[...]`, and strips them from transcripts — but ONLY for providers `cartesia/inworld/xai/fishaudio/gemini`. **ElevenLabs and OpenAI are NOT covered.** ⇒ (a) our hand-rolled `[whispers]` tags + `tts_text_transforms` encode/decode + `transcription_node` stripping are STILL REQUIRED for ElevenLabs; (b) **leave `expressive=False`** so the framework doesn't inject a competing markup block; (c) if the Halloween voice ever becomes one of those 5 providers, prefer the native pipeline.
- **`eleven_v3_conversational` (a "dialogue" model) honors only `stability`** among voice settings — `similarity_boost`/`style`/`speed` are dropped (plugin warns), as are `chunk_length_schedule`/`streaming_latency`/`enable_ssml_parsing`. ⇒ Task 5 `build_tts` sets only `stability` (= `UG_HALLOWEEN_STABILITY`). Routing: `is_dialogue_model(m) = m.startswith("eleven_v3")` → text-to-dialogue WS (tags performed + streamed). No `eleven_v4*` exists at 1.8.3.
- **C1 OBSOLETE:** `Agent.update_options(tts=...)` now EXISTS (live per-agent TTS swap via `_update_models`). Our `tts_node` override stays the approach (per-utterance profile routing, no agent mutation); session-level `tts` is still getter-only.
- **C2 OBSOLETE:** Deepgram `update_options` now takes model/encoding/sample_rate/bit_rate AND invalidates the pooled WS — an in-place model change now takes effect.
- **§7.8 tag filter integration:** TTS-branch filtering is now a configurable `text_transforms` list (applied in `generation.py`) and plain callables are accepted — the encode/decode transforms plug in there. **Confirm the exact `AgentSession` kwarg name (`tts_text_transforms` vs `text_transforms`) when wiring Task 10.** Transcript stripping still needs `transcription_node`.
- **C6:** `preemptive_generation=` is deprecated → set via `turn_handling=TurnHandlingOptions(...)`; default behavior unchanged (ON).
- **C3 nuance:** `on_user_turn_completed` exceptions are now caught (raise → drop turn + log, no session crash); "never raise / time-box" is still good practice but soft now.

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

## Plan 5 build findings (policy / expressive / profiles)

- **The decision policy rule is a conservative safety-net; the ENGINE is the primary path.** `explicit_command`/`has_cue` deliberately defer ambiguous phrasings to the engine. Accepted false-exit residuals (low-likelihood; an explicit exit rule beats the engine, so these matter — but the engine is enabled in the demo and Task 7 drops exit-rule verdicts in STANDARD): "please switch from airplane mode to normal mode" + short-gap "how do I switch the spooky voice to your regular voice" (older request-verb frame); "swap the voice plan for a regular voice plan"; "I prefer not to use the normal voice" (negation between verb + mention); a contrived 7–9-word move ("please switch the old main voice assistant on the app to regular mode"). **Structural backstop: Task 7 drops any exit-rule verdict while in STANDARD** (an exit is a no-op there, can only shadow an engine enter).
- **Expressive pipeline — G13 residual shapes** (reach the TTS as text; low-likelihood, prompt-forbidden): nested `[a [b]]`, a newline inside brackets, a closed group ≥119 inner chars, and an allowed tag glued to a parenthetical `[sighs](softly)` (parsed as a markdown link → "sighs" spoken). By design, any closed non-link `[...]` of 33–118 chars is DROPPED from audio AND transcript even in standard mode (a legit aside like `[Note: closes at 5 PM]` is lost — fine for a voice assistant). Task 11's client strip must match: closed `[...]` ≤120, not followed by `(`.
- **ElevenLabs `VoiceSettings` requires `similarity_boost`.** `VoiceSettings(stability=…)` alone raises `TypeError`. Pass `similarity_boost=NOT_GIVEN` (the plugin drops it → only `stability` is sent, no "ignored option" warning). For `eleven_v3*` dialogue models `stability` is the ONLY effective setting (style/speed/similarity are ignored + warned).
- **Expressive tags are gated on the MODEL, not just the vendor.** Only `eleven_v3*` models perform inline audio tags; flash/turbo/multilingual speak `[laughs]` literally. The halloween profile attaches `SPOOKY_TAGS` only when the model starts with `eleven_v3` (mirrors the plugin's `DIALOGUE_TTS_MODEL_PREFIX`). If Task 1 pins a turbo model for latency, tags are automatically off (dark voice, no inline cues) — fail-closed for G13.
- **Missing `UG_HALLOWEEN_VOICE_ID` → Deepgram fallback** (NOT ElevenLabs' stock voice). So `{ELEVEN_API_KEY set, no voice_id}` → the Halloween profile is the Deepgram dark voice. Task 12 must add `UG_HALLOWEEN_VOICE_ID` to `app.yaml` + `.env.example`; Task 13 must set it.
- **`build_tts` can raise** `ValueError` (missing voice id / plugin key check) or `ImportError` (plugin absent) → Task 8/10 wrap it and degrade. The ElevenLabs plugin calls `Plugin.register_plugin` on first import, which raises off the main thread — so import it at the TOP of `app/agent.py` (the entrypoint runs on the subprocess main thread), not lazily inside a worker thread.
