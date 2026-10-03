# Unity Gateway Voice Studio

A governed voice-agent studio presenting **Unity AI Gateway** — Choice / Control / Context / Costs —
through a LiveKit customer-support agent with loyalty→model routing, generic Lakebase-scoped tools,
and generated per-company datasets.

- **Spec:** `docs/superpowers/specs/2026-09-24-unity-gateway-voice-studio-design.md`
- **Plan (wave 1):** `docs/superpowers/plans/2026-09-24-ug-voice-studio-plan-1-foundations.md`
- **Plan 5 (Halloween mode):** spec `docs/superpowers/specs/2026-10-02-ug-voice-studio-plan-5-ai-decide-halloween-design.md`, plan `docs/superpowers/plans/2026-10-02-ug-voice-studio-plan-5-ai-decide-halloween.md`
- **Gotchas:** `docs/gotchas.md` — non-obvious traps and platform constraints; read it before touching the voice stack or the deploy.
- **Conventions:** run everything with `uv`; the Databricks profile is user-chosen (`--profile <name>`), never auto-selected.

## Build target

- **Profile:** `DEFAULT` (`.databrickscfg`) — chosen by the user, never auto-selected.
- **Lakebase:** endpoint `ep-your-endpoint-id` (`…database.us-east-1.cloud.databricks.com`),
  database `databricks_postgres`, user `you@example.com`.
- **Lakebase-capable:** yes — user-provided live instance (endpoint reachability confirmed in Task 5 discovery).

## Halloween mode (Plan 5)

Ask the agent for a spooky voice and it switches. Tier, model and governance stay exactly as they were.

- **AI Decide.** Databricks `ai_decide` (Beta) classifies each caller turn as enter, exit or none, outside the conversational LLM. A deterministic policy applies at most one change per turn, and an exit always wins. `UG_DECIDE_ENGINE=uaig_chat` swaps in a gateway-served chat model as the decider; `UG_AI_DECIDE=0` turns the feature off.
- **Expressive voice.** In Halloween mode the Databricks LLM writes inline cues such as `[whispers]`, and ElevenLabs (`eleven_v3_conversational`) performs them. Cues are never read aloud or shown. If ElevenLabs is unavailable the call carries on in a darker Deepgram voice (`aura-2-zeus-en`).
- **Visible.** The Control pillar shows each decision live: mode, confidence, latency and path.

Settings live in `app.yaml` and are mirrored in `.env.example`: `UG_AI_DECIDE`, `UG_DECIDE_ENGINE`, `UG_DECIDE_MODEL`, `UG_DECIDE_TIMEOUT_S`, `UG_DECIDE_CUE_WAIT_S`, `UG_HALLOWEEN_TTS`, `UG_HALLOWEEN_TTS_MODEL`, `UG_HALLOWEEN_VOICE_ID`, `UG_HALLOWEEN_STABILITY`, `UG_HALLOWEEN_FALLBACK_VOICE`, plus the secret `ELEVEN_API_KEY`.

Four things are the owner's to do before the ElevenLabs voice works on a deployed app; the repo can only reference them:

1. **Enable `ai_decide` on the workspace** (admin, Previews). The agent's `DATABRICKS_TOKEN` must also be allowed to call the `ai-functions` API. Without `ai_decide` the default engine returns errors; explicit requests still work, or set `UG_DECIDE_ENGINE=uaig_chat`.
2. **Create the ElevenLabs key** as a secret in the `ug-voice-studio` scope and attach it to the app as the resource `elevenlabs-api-key`. `app.yaml` reads it with `valueFrom`; the value is never committed. Attach it before deploying, since `valueFrom` only resolves once the resource exists.
3. **Pick a voice and set `UG_HALLOWEEN_VOICE_ID`** (a commented placeholder in `app.yaml`). Until it is set, Halloween mode uses the Deepgram fallback voice.
4. **Confirm the app can reach `api.elevenlabs.io`.** Outbound egress to it has not been verified yet.

The Apps runtime is Python 3.11 while the local venv is 3.12, so `agent-requirements.txt` is compiled for 3.11, never exported from the lock: `uv pip compile agent-requirements.in --python-version 3.11 -o agent-requirements.txt`. The reusable write-up of these patterns is in `skills/building-voice-agents-on-databricks/references/`.
