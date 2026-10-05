# Architecture: how DIVA works end to end

DIVA (Databricks Intelligent Voice Agents) is a blueprint for running real-time voice agents on Databricks. This page
shows every moving part of the reference implementation, the Unity Gateway Voice Studio, and the order in which they
talk to each other.

A call is a cascade. LiveKit carries the audio, Deepgram turns it into text, a Databricks-served model answers through
Unity Gateway (the caller's loyalty tier picks which one), and a text-to-speech voice speaks the reply. That voice is
Deepgram's by default. When the caller asks for a spooky voice, the call switches into an expressive Halloween mode that
speaks through ElevenLabs (or a darker Deepgram voice when ElevenLabs isn't configured). Databricks AI Decide classifies
what the caller wants on every turn and a small deterministic policy applies it, both outside the conversational model.

On the Databricks side DIVA builds on three pieces the
[Agent Bricks](https://www.databricks.com/blog/agent-bricks-dais-2026) announcement names: Databricks Apps hosts it,
Unity Gateway serves and governs its models, and Lakebase holds its data. The agent itself is a custom agent, a
LiveKit Agents worker. MLflow records every call as a trace, and AI Decide makes the per-turn voice-mode decision.

Everything runs in one of four places:

| Where | What runs there |
|---|---|
| **The caller's browser** | The studio single-page app and `livekit-client` |
| **External SaaS** | LiveKit Cloud (WebRTC media and agent dispatch), Deepgram (speech-to-text, the standard text-to-speech voice and the fallback Halloween voice) and ElevenLabs (the expressive Halloween voice, when configured; it speaks only while that mode is on) |
| **One Databricks App** | `start_app.py` boots both tiers in one container: the web / token tier (`app/web_server.py`) and the agent worker (`app/agent.py`) |
| **Your Databricks workspace** | Unity Gateway (chat and embeddings), Databricks AI Decide (the voice-mode classifier), Lakebase Postgres with [Lakebase Search](https://www.databricks.com/blog/lakebase-search-state-art-full-text-and-vector-search-postgres) (the worlds), Unity Catalog and MLflow (traces) |

Credentials (LiveKit, Deepgram, ElevenLabs and the Databricks token) come from the `ug-voice-studio` secret scope
through app secret resources (`valueFrom` in `app.yaml`), so no secret value lives in the repo or the image.

## The diagram

Numbers mark the order things happen: step 0 runs once per world, steps 1–4 establish the connection, and 5–9 run on
every call. Step 6 repeats on every caller turn and holds the one decision the boxes don't show, so it gets its own
sequence under the diagram.

```text
                              +- Databricks App (1 container) -+
+------------------+          | +----------------------------+ |
| Browser          |--[1][2]->| | Web / token tier           | |
| studio UI        |<---JWT---| | web_server.py              | |
| livekit-client   |          | | GET /   GET /api/token     | |
+------------------+          | +----------------------------+ |
    | [3]     ^ [8]           |                                |
    v         |               |                                |            Databricks workspace
+------------------+          | +----------------------------+ |          +----------------------+
| LiveKit Cloud    |--[4]---->| | Agent worker               | |-[6][7]-->| Unity Gateway        |
| WebRTC SFU       |<=[6][8]=>| | agent.py (LiveKit Agents)  | |          | /responses (LLM)     |
| + agent dispatch |          | |                            | |          | /embeddings          |
+------------------+          | | [5] bind: tier -> model    | |          +----------------------+
                              | |                            | |
+------------------+          | | [6] STT -> LLM -> TTS      | |          +----------------------+
| Deepgram         |<===[6]==>| | [6] voice mode: AI Decide  | |--[6]---->| Databricks AI Decide |
| STT nova-3       |          | |    standard  -> Deepgram   | |          | enter / exit / none  |
| TTS aura-2       |          | |    halloween -> ElevenLabs | |          | (REST API)           |
+------------------+          | |                            | |          +----------------------+
                              | | [7] tools: semantic_search | |
+------------------+          | |            record_lookup   | |          +----------------------+
| ElevenLabs       |<===[6]==>| |                            | |--[5]---->| Lakebase Postgres    |
| TTS eleven_v3    |          | | [8] publish evidence       | |--[7]---->| customers, records   |
| (Halloween only) |          | |                            | |          | Lakebase Search ANN  |
+------------------+          | | [9] flush OTLP spans       | |          +----------------------+
                              | |                            | |
                              | |                            | |          +----------------------+
                              | |                            | |--[9]---->| Unity Catalog table  |
                              | |                            | |          | -> MLflow traces     |
                              | +----------------------------+ |          +----------------------+
                              | start_app.py boots both tiers  |
                              +--------------------------------+

[0] Before any call - generate a world (studio "Generate" step):

    Browser --POST /api/generate--> Web tier --draft + embed--> Unity Gateway
                                       |
                                       +--write in 1 txn (data_generation_id)--> Lakebase Postgres

[6] One caller turn, voice-mode path (AI Decide runs beside the LLM, never inside it):

    STT final transcript --> AI Decide starts in the background (enter / exit / none)
    end of turn ----------> a cue, or already in Halloween mode? wait up to 0.8 s for the answer
         |
         +-- in time ---------> policy says switch --> SAME-TURN: this reply is in the new voice
         |
         +-- late or failed --> an explicit command in the words? the rule-based check switches
                                SAME-TURN as well. Otherwise this reply uses the current voice
                                and, if the answer was only late, when it lands:
                                |-- policy says switch, no newer turn ended --> ANNOUNCED switch
                                '-- a newer turn already ended ---------------> dropped
```

## Step by step

### Before any call

0. **Generate a world.** Once per company, the studio's Generate step posts to `/api/generate` (or run
   `uv run python generate.py "<company>"`). `src/generate.py` drafts documents, customers across the Standard
   / Premium / VIP tiers, and order and case records with a JSON-mode chat call through Unity Gateway,
   embeds the documents with the gateway's embedding model, and writes it all to Lakebase in one transaction
   under a fresh `data_generation_id`. The documents are indexed for
   [Lakebase Search](https://www.databricks.com/blog/lakebase-search-state-art-full-text-and-vector-search-postgres):
   `lakebase_ann` (cosine ANN via `lakebase_vector`) and `lakebase_bm25` (BM25 via `lakebase_text`; the call-time
   tools use the ANN index only).

### Establishing the connection

1. **Load the studio.** The browser loads the studio from the web tier (`GET /`) and lists the worlds stored
   in Lakebase (`GET /api/datasets`).
2. **Start a call.** `GET /api/token?dataset=…&customer=…&name=…` returns a signed HS256 LiveKit JWT. It lives
   15 minutes, names a fresh room, carries `{data_generation_id, customer_id}` as metadata, and dispatches the
   agent named by `AGENT_NAME` (default `ug-agent`). The caller's name is courtesy-only and sanitized.
3. **Join the room.** The browser connects to LiveKit Cloud over WebRTC with that token.
4. **Dispatch the agent.** LiveKit hands the job to the agent worker (`app/agent.py`), which keeps a WebSocket
   open to LiveKit and is registered as `ug-agent`.

### On every call

5. **Bind and route.** The worker waits for the participant, reads the token metadata, and looks up the
   caller's loyalty tier in Lakebase (`src/services/session_bind.py`, `src/services/loyalty_context.py`).
   `route_for(tier)` (`src/policy/routing.py`) picks the model from the `UG_MODEL_*` map. That choice is app
   logic: Unity Gateway only serves the model it names. The model never sees the tier, and the session is only
   built once the model is known.
6. **Talk.** Audio flows browser ⇄ LiveKit ⇄ worker. Deepgram STT (nova-3) turns speech into text and the routed
   LLM answers through Unity Gateway's OpenAI-compatible `/responses` API. A TTS voice speaks the reply: Deepgram
   TTS (aura-2) in the standard voice, ElevenLabs (`eleven_v3_conversational`) once the call is in Halloween mode
   and ElevenLabs is configured, a darker Deepgram voice if it isn't. Which voice applies is decided on every caller
   turn, outside the LLM: Databricks AI Decide classifies what the caller wants and a deterministic policy applies it
   (see [Voice mode](#voice-mode-how-ai-decide-picks-the-voice)).
7. **Ground.** The LLM can call two read-only tools (`app/tools.py`, `src/services/retrieval.py`).
   `semantic_search` embeds the question through the gateway's `/embeddings` and runs
   [Lakebase Search](https://www.databricks.com/blog/lakebase-search-state-art-full-text-and-vector-search-postgres)
   ANN (`ORDER BY embedding <=> $q` on the `lakebase_ann` index) over the world's documents; `record_lookup`
   is plain SQL that returns only the bound caller's records and abstains for unknown callers. Both are scoped
   to the bound `data_generation_id`.
8. **Show.** The worker publishes PII-free evidence packets (the routing decision, retrieval hits, cumulative
   token usage, and the voice mode) on the LiveKit data channel, and the studio renders them as the Choice / Control
   / Context / Costs pillars. The voice mode appears on the Control pillar as a live "Voice mode · AI Decide" row.
9. **Trace.** The worker installs one OpenTelemetry tracer provider when its process starts (`_setup_process` in
   `app/agent.py`), so the job's root span is captured. `app/tracing.py` enriches the spans as they export (`ug.*`
   attributes, MLflow span types), the root span is flushed as soon as it ends, and the spans go over OTLP into a
   Unity Catalog table, where they read as MLflow traces. Each voice-mode change adds a `ug.ai_decide` span. Tracing
   is fail-soft: with no trace table configured, calls still work.

## Voice mode: how AI Decide picks the voice

Every call starts in the standard voice. On each caller turn, Databricks AI Decide (`ai_decide`, an AI Function that
was in Beta at the time of writing and is opt-in per workspace) answers one multiple-choice question about the
caller's words: do they want the spooky voice (`enter`), want it gone (`exit`), or neither (`none`)? It only classifies.
A deterministic policy in the app decides whether that answer changes the mode. Setup, settings and how to verify the
voice are in [Halloween mode](halloween-voice.md).

That question runs outside the conversational LLM, so the answer doesn't depend on which tier's model is talking. The
decider's input is the caller's utterance, the agent's previous line (its last 200 characters, as context) and the
current mode. It is never handed the tier, name, customer id, dataset prompt or retrieved documents, so a retrieved
document can't talk to it directly. The one indirect route is the agent's previous line, which can echo whatever the
agent just said, including the caller's first name from the greeting. Unity Gateway doesn't decide intent itself; the
decision is app logic that calls a decision service.

As of 2026-10-05 the voice-mode path is unit-tested and has run live, on a local instance (see the Plan 5 live-run notes
in [gotchas](gotchas.md)) and on a deployed app, where AI Decide switched the mode in both directions. As shipped,
`app.yaml` leaves `UG_HALLOWEEN_VOICE_ID` unset, so Halloween mode speaks in the Deepgram fallback voice until it is
set. The ElevenLabs voice also needs the `elevenlabs-api-key` secret resource attached to the app before deploying (its
`valueFrom` only resolves once the resource exists) and outbound access to `api.elevenlabs.io`. AI Decide needs the
`ai_decide` function enabled on the workspace (admin, Previews).

### One decider: the `ai_decide` REST API

`src/services/ai_decide.py` makes one call per turn to the documented
[`ai_decide` REST API](https://docs.databricks.com/api/ai-functions/v1/ai-decide), and nothing else decides the
intent. The request follows the reference:

- `POST {DATABRICKS_HOST}/api/2.0/ai-functions/ai-decide`
- `state`: the caller's words, the agent's previous line and the current mode, which is all the decider sees
- `questions`: one `choice` question, `voice_mode`, with `instructions` and `criteria` for `enter`, `exit` and `none`
- `options.version`: `"1.0"`

The answer comes back at `response.answers.voice_mode`: the `choice`, a `probabilities` map and a `confidence`. The call
needs AI Decide enabled on the workspace (admin, Previews) and a token that may call the `ai-functions` API.
`classify()` fails closed: any error, timeout or unusable reply becomes `none`. To confirm the function itself is
answering, run `tools/ai_decide_check.py`: it sends four sample utterances to the real endpoint and prints what came
back.

`UG_AI_DECIDE=0` turns the feature off: no calls, and the call stays in the standard voice.

### What happens in a turn

1. **Start early.** The final transcript from Deepgram starts the AI Decide call in the background, so the silence
   before end of turn hides part of its latency. The call has its own time limit, `UG_DECIDE_TIMEOUT_S` (3 s).
2. **Wait only when it matters.** At end of turn the agent waits for the answer only if the words carry a cue (a
   request about the assistant's voice or mode, such as "spooky voice" or "normal voice"; a plain mention of Halloween
   doesn't count) or the call is already in Halloween mode, so even a terse "stop" gets the full wait. The wait is at
   most `UG_DECIDE_CUE_WAIT_S` (0.8 s). On any other turn the answer is used only if it has already arrived. On a cue
   the Halloween voice is also built early and a short warm stream is opened to ElevenLabs, even if the turn ends
   without a switch.
3. **Same-turn switch.** If a verdict is in time and the policy says switch, the mode changes before the reply is
   generated, so this reply already uses the new persona and voice. Going into Halloween mode, the agent first says a
   short bridge line in the outgoing voice ("One moment… let me set the scene."). The verdict is the engine's answer
   or, when the engine gave no usable one, the rule-based check's (an explicit exit always wins).
4. **Announced switch.** Otherwise the reply goes out in the current voice (the prompt tells the LLM to say "One
   moment…" to a voice request, never to refuse). When the engine's answer lands, the policy says switch and the
   caller hasn't finished a newer turn, the agent switches and says a short announcement in the new voice. An answer
   that lands after a newer turn has ended is dropped.

A switch takes effect only between utterances: each reply keeps the voice it started with, so the voice never changes
mid-sentence, and talking over an announcement doesn't undo the switch.

### The policy

`src/policy/voice_mode.py` is pure code with no I/O:

- Entering needs an `enter` verdict with a confidence of at least 0.7, and at most three entries per call. Leaving
  needs an `exit` verdict with a confidence of at least 0.5 and is never capped. Both thresholds are initial values,
  because AI Decide's confidences aren't calibrated.
- A rule-based check for explicit commands ("do a spooky voice", "go back to the normal voice") runs on every turn. An
  explicit exit always wins over the engine. For anything else the rule only counts when the engine gave no usable
  answer in time (late or failed), which is why plain requests still work when AI Decide is unavailable.
- At most one mode change happens per turn.
- Everything fails closed: a timeout, an HTTP error or an unparseable answer leaves the mode where it was, unless the
  rule-based check hears an explicit command.

### Voices and expressive tags

`StudioAgent.tts_node` (`app/studio_agent.py`) reads the active voice profile (`app/voice_profiles.py`) once per
utterance:

| Profile | Speaks through | Expressive tags |
|---|---|---|
| `standard` | The session's own Deepgram TTS, `aura-2-andromeda-en` | None |
| `halloween` | ElevenLabs, `eleven_v3_conversational` by default (`UG_HALLOWEEN_TTS_MODEL`); needs `ELEVEN_API_KEY` and `UG_HALLOWEEN_VOICE_ID` | Yes, on `eleven_v3*` models |
| `halloween_fallback` | Deepgram `aura-2-zeus-en` (`UG_HALLOWEEN_FALLBACK_VOICE`) | None |

`UG_HALLOWEEN_TTS` also accepts `openai` and `deepgram` in place of the default `elevenlabs`. If the ElevenLabs
settings are missing, the `halloween` profile is the fallback profile: a spooky persona in a darker Deepgram voice,
with no tags. If ElevenLabs fails during a call, the worker marks the call degraded and speaks Halloween mode in the
fallback voice for the rest of the call (the utterance in flight may be cut off). That TTS instance sits outside the
session's own error count, so a vendor failure can't close the call.

When the Halloween voice can perform tags (ElevenLabs on an `eleven_v3*` model), the prompt lets the LLM write cues
from a verified allow-list of 67 (`[whispers]`, `[building tension]`, `[laughs]` and more, in four groups; see
[Halloween mode](halloween-voice.md#the-cues)), which the voice is meant to perform rather than read out. Before the text reaches the TTS it runs through four stages: `encode_tags` swaps the allowed tags for
placeholders and drops any other tag, LiveKit's stock `filter_markdown` and `filter_emoji` run as usual, and
`decode_tags` puts the tags back (`app/expressive.py`). The encode step exists because the stock markdown filter holds
back everything after a bare `[`, which would stall streaming speech. The agent's lines in the on-screen transcript are
stripped of tags on the worker and again in the browser, and the standard and fallback voices have an empty tag
vocabulary, so they drop every tag. The filter fails closed, though a few malformed bracket shapes (nested or
multi-line brackets, very long ones, a tag glued to a parenthetical) are deliberately not handled.

### Shown and traced

The worker publishes a `voice_mode` evidence fragment on the LiveKit data channel (no transcript text, no personal
data) at bind, on every mode change, on every dropped late answer and on a degrade. A classified turn that changes
nothing emits nothing, unless its late answer is dropped. The studio's Control pillar renders the fragment as a "Voice
mode · AI Decide" row: the mode, the voice speaking, the engine's label, the confidence, the latency and the path (same
turn, announced or late dropped), plus the enter / exit / none probabilities when the engine returns them, and a
"Fallback voice" flag after a degrade. The row always names the engine, even when the rule-based check made the call;
the span's `ug.decide.source` attribute tells `model` from `rule`. The mode also restyles the whole studio while it is
on.

In the trace, each mode change and each dropped late answer is a `ug.ai_decide` span (engine, verdict and its source,
probabilities, latency, path, and the mode before and after; no utterance text). The root span carries `ug.voice_mode`,
`ug.voice_mode_transitions`, `ug.decide_engine`, `ug.expressive_tags` and `ug.voice_degraded`.

### Governance

The eight governance invariants in the [design spec](superpowers/specs/2026-09-24-unity-gateway-voice-studio-design.md)
(§12) still hold. The ones that shape this architecture: decisions are deterministic code outside the LLM, the model
never sees the raw tier, the caller's name is courtesy-only, the tier comes from a governed lookup rather than from
conversation, and the tools are read-only, scoped to the bound dataset, and abstain when nothing matches. Voice mode
adds five:

| ID | Invariant | How |
|---|---|---|
| **G9** | Mode is independent of tier: it never changes the routed model, the directives or escalation | The bind is a frozen dataclass the voice-mode controller never touches |
| **G10** | AI Decide sees only the caller | The engine's input is built from three fields: the utterance, the previous agent line and the current mode |
| **G11** | The persona can't override governance | Persona and cue rules sit before the governance block, and the one-reply switch notes end by re-asserting it; facts are spoken exactly as the tools return them. The one deliberate exception is a made-up spooky tale on request, which may contain no real orders, prices, policies or people |
| **G12** | An explicit exit is always honored | The explicit exit rule beats the engine, and exits are never capped |
| **G13** | Expressive tags are never read aloud and never shown | A fail-closed tag filter in front of the TTS, and tag-stripped transcripts (a few malformed bracket shapes are not handled) |

### When things fail

| What goes wrong | What happens |
|---|---|
| AI Decide isn't enabled, errors, times out or returns something unexpected | The engine's verdict is `none`, so the mode stays put unless the rule-based check hears an explicit command, which it applies at once |
| The answer misses the end of the turn | If the words aren't an explicit command, the reply uses the current voice; the switch lands as an announced switch, or is dropped if the caller has finished another turn in the meantime |
| `ELEVEN_API_KEY` or `UG_HALLOWEEN_VOICE_ID` is missing | Halloween mode runs on the Deepgram fallback voice: spooky persona, darker voice, no tags |
| ElevenLabs fails mid-call | The call is marked degraded (`ug.voice_degraded`, "Fallback voice" on the Control pillar) and Halloween mode carries on in the fallback voice for the rest of the call |
| The LLM writes a tag the voice can't perform, or old tags are left in the history after an exit | The tag filter drops them, so they are neither spoken nor shown |
| Any exception inside the voice-mode code | Swallowed. The turn carries on in the current mode and is never dropped |

## Check it's working locally

Start both tiers (see the [README quickstart](../README.md#quickstart-local)), for example with the web tier on
port 9090:

```bash
PORT=9090 uv run python app/web_server.py     # logs: listening on 0.0.0.0:9090 (agent: "ug-agent")
uv run python app/agent.py dev                # logs: registered worker
```

Then walk the connection steps:

| Step | Check | Healthy result |
|---|---|---|
| 1 | `curl -s -o /dev/null -w '%{http_code}' http://localhost:9090/` | `200` |
| 1 | `curl -s http://localhost:9090/api/datasets` | the worlds in Lakebase, each `ready` |
| 2 | `curl -s 'http://localhost:9090/api/token?dataset=<data_generation_id>&name=Test'` | `token`, `serverUrl` and `roomName`; the JWT dispatches `ug-agent` |
| 4 | the agent worker's log | `registered worker` |
| 5–9 | open http://localhost:9090, pick a world and a caller, and start a call | the agent greets you, answers from the world's data, and the four pillars fill in |
| 6 | during the call, say "Can you do a spooky Halloween voice?" | the Control pillar's "Voice mode · AI Decide" row changes to Halloween and the agent speaks in the spooky voice; "go back to the normal voice" returns it to Standard. A plain request like this is also caught by the rule-based check, so it works even when AI Decide is down |
| 6 | say "Change your voice to something creepy", which the rule-based check doesn't match | the same switch, decided by the engine, sometimes as a short announcement right after the agent's reply; the worker log has no `[ug] ai_decide engine=… failed` line |

The last row needs AI Decide enabled on the workspace. If either `ELEVEN_API_KEY` or
`UG_HALLOWEEN_VOICE_ID` is missing, Halloween mode speaks in the Deepgram fallback voice, and with `UG_AI_DECIDE=0` the
call stays in the standard voice.
