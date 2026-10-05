# Halloween mode

Ask the agent for a spooky voice and it switches, and the whole studio switches with it. The tier, the model and the
governance rules stay as they were (one exception, under [Two choices worth knowing](#two-choices-worth-knowing): on
request the monster tells a made-up story, which needs no tool). Three pieces make it work, and all of them are optional.

| Piece | What it does | Where |
|---|---|---|
| **[AI Decide](https://www.databricks.com/blog/introducing-aidecide-make-fast-decisions-your-governed-data)** | Databricks [`ai_decide`](https://docs.databricks.com/aws/en/sql/language-manual/functions/ai_decide) (Beta) classifies each caller turn as *enter*, *exit* or *none*, outside the conversational LLM. A deterministic policy applies at most one change per turn, and an explicit exit request always wins. | `src/services/ai_decide.py`, `src/policy/voice_mode.py`, `app/voice_mode.py` |
| **Expressive voice** | The Databricks LLM plays a campfire-storyteller monster and writes inline cues such as `[whispers]` or `[building tension]`. ElevenLabs (`eleven_v3_conversational`) performs them. Cues are never read aloud or shown. Ask for a scary story and it makes one up; facts about orders, prices and policies still come only from the tools. | `src/agent_prompt.py`, `src/expressive_palette.py`, `app/expressive.py`, `app/voice_profiles.py`, `app/studio_agent.py` |
| **Themed UI** | The Control pillar shows each mode change live (mode, confidence, latency, path), and the studio cross-fades into an "All Hallows' Console" theme, then back when the agent exits. Honors `prefers-reduced-motion`. | `app/web/public/` |

If ElevenLabs isn't set up or is unavailable, the call carries on in a darker Deepgram voice (`aura-2-zeus-en`),
without cues. `UG_AI_DECIDE=0` turns the whole feature off. How AI Decide is called, and what it may and may not see,
is in [the architecture walkthrough](architecture.md#voice-mode-how-ai-decide-picks-the-voice).

## Turn it on

`.env.example` lists every setting, with comments. The deployed app reads the same names from `app.yaml`, which carries
most of them (the voice id is only a commented placeholder there). The ones you'll touch:

| Setting | What it does |
|---|---|
| `UG_AI_DECIDE` | `1` (default) asks the decision engine (AI Decide by default) every turn. `0` is the kill switch: no decision calls, and the agent stays in the standard voice. |
| `UG_DECIDE_ENGINE` | `ai_decide` (default), or `uaig_chat` to decide with a gateway-served chat model (`UG_DECIDE_MODEL`) instead. |
| `ELEVEN_API_KEY` | A secret. Together with `UG_HALLOWEEN_VOICE_ID` it switches on the ElevenLabs voice. Without both, Halloween mode uses the Deepgram fallback. |
| `UG_HALLOWEEN_VOICE_ID` | The ElevenLabs voice to use. |
| `UG_HALLOWEEN_FALLBACK_VOICE` | The Deepgram voice used when ElevenLabs isn't set up. |

On a deployed app, four things live outside the repo, so the repo can only reference them:

1. **Enable `ai_decide` on the workspace** (admin, Previews). The agent's `DATABRICKS_TOKEN` must also be allowed to
   call the `ai-functions` API. Without `ai_decide` the default engine returns errors; explicit requests ("switch to
   the spooky voice") still work, or set `UG_DECIDE_ENGINE=uaig_chat`.
2. **Create the ElevenLabs key** as a secret in your app's secret scope (`ug-voice-studio` in this repo) and attach it
   to the app as the resource `elevenlabs-api-key`. `app.yaml` reads it with `valueFrom`; the value is never committed. Attach it before
   deploying, since `valueFrom` only resolves once the resource exists.
3. **Pick a voice and set `UG_HALLOWEEN_VOICE_ID`** (a commented placeholder in `app.yaml`). Until it is set,
   Halloween mode uses the Deepgram fallback voice.
4. **Confirm the app can reach `api.elevenlabs.io`.** Outbound egress to it has not been verified yet.

## The cues

The monster may write only the cues in the palette (`src/expressive_palette.py`: 67 cues in four groups). Each was
synthesized through the same ElevenLabs plugin and voice the app uses, then transcribed back with Deepgram. None was
read aloud, and each made the voice do something other than read the word. Any other bracketed cue or stage direction
is dropped from the audio and the transcript, so a cue the voice doesn't perform never reaches the caller or the
screen.

| Group | Examples |
|---|---|
| **Breath and sounds** | `laughs`, `gasps`, `sighs`, `deep breaths`, `evil laugh`, `clears throat` |
| **Volume** | `whispers`, `soft`, `hushed`, `low voice`, `growls`, `raspy` |
| **Attitude** | `mischievously`, `menacing`, `eerie`, `sarcastic`, `gleeful`, `deadpan` |
| **Pacing and tension** | `building tension`, `dramatic pause`, `slowly`, `hesitates`, `trailing off` |

Change the palette only by re-running the bake-off and updating [the contract](discovery/expressive-tags-contract.md),
which holds the full list, the method, the results and their limits. "Did something other than read the word" is
weaker than "sounds right": the check hears words and clip length, not tone or loudness, so the listening test below
matters.

## Verify the voice

Two manual tools under `tools/` check the voice against the live services. They aren't part of the test suite, they
read `.env.local`, and they never print a key, host or token.

```bash
uv run python tools/expressive_bakeoff.py --from-palette   # every shipped cue is performed, none read aloud
uv run python tools/expressive_llm_check.py --dry-run      # what the LLM check would do; drop --dry-run to run it
```

- **Bake-off.** Needs `ELEVEN_API_KEY`, `UG_HALLOWEEN_VOICE_ID` and `DEEPGRAM_API_KEY`. It synthesizes each cue in
  front of two plain sentences (138 syntheses for the palette), transcribes the audio and prints a verdict per cue.
  The line to look for is `spoken-aloud=0`. `--stability-check` compares stability 0.0 with 0.5. Its other flags and
  its exit codes are in the tool's docstring.
- **LLM check.** Sends scripted caller utterances to each tier's model through Unity Gateway, with the exact Halloween
  instructions the agent uses. It reports cues per 100 words, distinct cues, how many cues are on the list, stage
  directions, stacked cues, sound cues in the stories, laughter written out as words, and facts kept verbatim. A
  default run makes 69 calls (3 tiers × (7 utterances × 3 samples + 2 probe calls)) and refuses to start above 100.
  Flags: `--dry-run` prints the number of calls and stops, `--samples N` sets the replies per utterance (default 3),
  `--tiers Standard,Premium,VIP` picks the tiers (default all three), and `--json PATH` saves every reply with its
  metrics (keep the file outside the repo). Exit codes: `0` every tier passes, `1` a target was missed, `2`
  configuration or budget (missing `.env.local` values, an unknown tier, too many calls), `3` a gateway request failed.
- **Small samples.** The last full run used 2 samples per utterance, and the laughter rule was spot-checked only (three
  stories per tier). Read the results as encouraging, not proof, and re-run after any change to the prompt, a tier
  model, the palette or the voice. The figures are in
  [the contract](discovery/expressive-tags-contract.md#llm-compliance-results).

Then listen once. Run the studio locally (see the [Quickstart](../README.md#quickstart-local)), start a call, say
"switch to the spooky voice", then "tell me a scary story". You should hear breaths, whispers and pauses, and no cue
read out loud. The transcript shows no brackets. Check that the story stays make-believe: no real order, price, policy
or person in it.

## Two choices worth knowing

- **A story is play, not information.** Without a carve-out, the Premium and VIP models answered "tell me a scary
  story" with "I don't have a story from my tools", because the governance says to answer only from the tools. The
  persona now says a spooky story is play (four or five short sentences, no tool needed) and keeps real orders,
  prices, policies and people out of it. Governance is unchanged and still comes last in the prompt.
- **Laughter is a cue, never a word.** Left alone, the models rarely laughed (sound cues were 1 to 12% of their cues),
  and "ha ha" or `*laughs*` would be read aloud. The prompt asks for laughs, gasps, sighs and breaths only as cues from
  the "Breath and sounds" group, and every story must include a sound at the scare and a laugh on the last line. The
  price is that stories are formulaic (a gasp, then a laugh), so listen for repetition. Only the ElevenLabs Halloween
  voice sees this part of the prompt.

The design docs are
[Plan 5: AI Decide and Halloween](superpowers/specs/2026-10-02-ug-voice-studio-plan-5-ai-decide-halloween-design.md) and
[Plan 6: expressive tags](superpowers/specs/2026-10-04-ug-voice-studio-plan-6-expressive-halloween-tags-design.md).
