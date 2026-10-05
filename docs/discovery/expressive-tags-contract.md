# Discovery: expressive audio tags for the Halloween voice (Plan 6)

_Verified 2026-10-04 against `eleven_v3_conversational` at stability `0.5`, with the voice configured as `UG_HALLOWEEN_VOICE_ID`, through `livekit-plugins-elevenlabs` 1.8.3, judged by Deepgram nova-3 transcript and audio duration (not by ear)._

## Method

Each of the 67 candidate tags was synthesized once in front of each of two neutral carrier sentences ("Somebody left the back door open again tonight." and "The lights went dark and the whole house grew still."), and each carrier was also synthesized twice with no tag to measure run-to-run noise: 67 × 2 + 4 = 138 syntheses, through the same plugin path the agent uses (`tts.stream()` on the text-to-dialogue connection, with `stability` as the only voice setting). Deepgram nova-3 transcribed every clip. A tag is `spoken-aloud` if, on either carrier, one of its words (three letters or more) is heard that the plain carrier does not contain; otherwise it is `performed` if the clip length differs from the plain carrier by at least `max(MIN_DELTA_S = 0.15 s, NOISE_FACTOR = 2.0 × the gap between the two plain renderings)` or the transcript differs; otherwise it is `no-audible-effect`. The tool is `tools/expressive_bakeoff.py`.

Positive control: with `UG_HALLOWEEN_TTS_MODEL=eleven_turbo_v2_5`, a model that speaks a tag instead of performing it, the same tool and voice reported `laughs` and `whispers` as `spoken-aloud` (Deepgram heard "laughs somebody left the back door open again tonight"), so a tag read aloud is caught.

## Results

| Tag | Verdict | Note |
|---|---|---|
| `deep breaths` | performed | Δ+1.36s |
| `exhales` | performed | Δ+0.31s |
| `inhales deeply` | performed | Δ+2.25s |
| `sighs` | performed | Δ+1.62s |
| `gasps` | performed | Δ+1.28s |
| `gulps` | performed | Δ+0.81s |
| `clears throat` | performed | Δ+1.36s |
| `heavy breathing` | performed | Δ+1.70s |
| `shaky breath` | performed | Δ-1.12s |
| `laughs` | performed | Δ+2.09s |
| `laughing` | performed | Δ+1.36s |
| `chuckles` | performed | Δ+1.85s |
| `evil laugh` | performed | Δ+2.64s |
| `maniacal laughter` | performed | Δ+4.81s |
| `giggles` | performed | Δ+1.59s |
| `snorts` | performed | Δ+0.16s |
| `wheezing` | performed | Δ+0.97s |
| `groans` | performed | Δ+1.12s |
| `exhales sharply` | performed | Δ-0.47s |
| `panting` | performed | Δ+0.57s |
| `menacing laugh` | performed | Δ+3.06s |
| `whispers` | performed | Δ-0.16s |
| `whisper` | performed | Δ+0.73s |
| `whispering` | performed | Δ+0.65s |
| `soft` | performed | Δ-0.24s |
| `softly` | performed | Δ-0.63s |
| `quietly` | performed | Δ+0.31s |
| `hushed` | performed | Δ+0.50s |
| `low voice` | performed | Δ+0.81s |
| `deep voice` | performed | Δ+0.73s |
| `growls` | performed | Δ+0.89s |
| `raspy` | performed | Δ-0.89s |
| `rumbling` | performed | Δ+1.62s |
| `dismissive` | performed | Δ-0.31s |
| `mischievously` | performed | Δ+0.50s |
| `nervously` | performed | Δ+0.89s |
| `menacing` | performed | Δ+0.73s |
| `sinister` | performed | Δ+0.81s |
| `ominous` | performed | Δ+1.28s |
| `eerie` | performed | Δ+0.81s |
| `sarcastic` | performed | Δ-0.24s |
| `curious` | performed | Δ+0.16s |
| `amused` | performed | Δ+0.31s |
| `dramatic` | performed | Δ+0.57s |
| `somber` | performed | Δ+0.65s |
| `fearful` | performed | Δ-0.39s |
| `cold` | performed | Δ+0.24s |
| `gleeful` | performed | Δ+0.42s |
| `smug` | performed | Δ-0.16s |
| `playful` | performed | Δ+0.81s |
| `excited` | performed | Δ+0.65s |
| `panicking` | performed | Δ+0.81s |
| `deadpan` | performed | Δ+0.39s |
| `thoughtful` | performed | Δ-0.55s |
| `building tension` | performed | Δ+1.04s |
| `pause` | performed | Δ-0.16s |
| `long pause` | performed | Δ+1.20s |
| `dramatic pause` | performed | Δ-1.12s |
| `slowly` | performed | Δ+0.34s |
| `suspenseful` | performed | Δ+0.81s |
| `hesitates` | performed | Δ+0.65s |
| `trailing off` | performed | Δ-0.97s |
| `rushed` | performed | Δ-0.39s |
| `measured` | performed | Δ-0.31s |
| `slow` | performed | Δ+0.16s |
| `short pause` | performed | Δ+0.31s |
| `drawn out` | performed | Δ-0.78s |

performed=67  spoken-aloud=0  no-audible-effect=0  error=0

`Note` is the clip-length change against the plain carrier, taken from the carrier where it was larger (negative: shorter).

## Final palette

Every `performed` tag, in candidate order. A category with no performed tag is omitted. `tools/expressive_bakeoff.py --from-palette` re-verifies it, and `tests/test_expressive_palette.py` fails if `src/expressive_palette.py` drifts from this block.

All 67 candidates were `performed`, so the palette is the whole candidate list. Read "Limits" before treating that as 67 proven cues.

```palette
breath: deep breaths | exhales | inhales deeply | sighs | gasps | gulps | clears throat | heavy breathing | shaky breath | laughs | laughing | chuckles | evil laugh | maniacal laughter | giggles | snorts | wheezing | groans | exhales sharply | panting | menacing laugh
volume: whispers | whisper | whispering | soft | softly | quietly | hushed | low voice | deep voice | growls | raspy | rumbling
emotion: dismissive | mischievously | nervously | menacing | sinister | ominous | eerie | sarcastic | curious | amused | dramatic | somber | fearful | cold | gleeful | smug | playful | excited | panicking | deadpan | thoughtful
pacing: building tension | pause | long pause | dramatic pause | slowly | suspenseful | hesitates | trailing off | rushed | measured | slow | short pause | drawn out
```

## Excluded

Nothing is excluded by the rules: no candidate was `spoken-aloud` and none was `no-audible-effect`.

- The six reference-example tags (`laughing`, `building tension`, `soft`, `dismissive`, `deep breaths`, `whisper`) are all `performed` and are in the palette. `soft` and `whisper` moved the clip on the second carrier only.
- The seven Plan 5 tags (`whispers`, `sighs`, `laughs`, `mischievously`, `nervously`, `exhales`, `inhales deeply`) are all `performed`, none was read aloud, and all are in the palette. `exhales` moved the clip on the second carrier only, and `whispers` only just cleared the bar: it moved the clip by exactly the bar, 0.16 s, on each carrier (shorter on one, longer on the other).

## Stability (0.0 vs 0.5)

`tools/expressive_bakeoff.py --stability-check` reads the playground's reference passage (six cues) once at each setting and lists which cue names were heard:

```
model=eleven_v3_conversational  reference passage, 6 cues
  stability 0.0: 21.8s, read aloud: none
  stability 0.5: 22.5s, read aloud: none
```

Keep `UG_HALLOWEEN_STABILITY` at 0.5. Neither setting read a cue aloud, so nothing argues for moving it. One rendering per setting cannot show whether 0.0 performs the cues more strongly, and ElevenLabs documents 0.0 (Creative) as the least reliable setting, so judge that by ear if it is ever tried.

## Limits

- **One voice, one model, one stability.** The result holds for the voice configured as `UG_HALLOWEEN_VOICE_ID` on `eleven_v3_conversational` at stability 0.5, in English, with the tag at the start of a short sentence. A different voice, model, stability or plugin version can change which tags are performed, and a Professional Voice Clone loses its characteristics on v3 Conversational (ElevenLabs docs). Re-run the tool when the voice, model or plugin version changes. This contract covers the model the app runs today; ElevenLabs now calls v3 Conversational the previous generation, and changing the TTS model is out of scope.
- **`performed` is not "performed as intended".** The tool sees only the words Deepgram hears and the clip length, which moves in steps of about 78 ms. It cannot hear timbre, loudness or mood, so it cannot tell that `whisper` whispers or that `menacing` sounds menacing. It shows only that the voice did something other than read the word out. A listen is the last check (README, "Verifying the voice").
- **How strong the evidence is.** The strong result is that no tag was read aloud: none of the 134 tagged renderings contained a word of its tag, and the control above shows the check does fire. The `performed` verdict is weaker, because it rests on clip length. 49 of the 67 tags cleared the bar on both carriers and 18 on one carrier only (`exhales`, `snorts`, `exhales sharply`, `whisper`, `soft`, `quietly`, `sinister`, `sarcastic`, `dramatic`, `smug`, `playful`, `thoughtful`, `pause`, `long pause`, `slowly`, `hesitates`, `slow`, `short pause`). Six cleared it with no margin at all: their largest shift equals the bar exactly, 0.16 s or two audio steps, and five of them (`whispers`, `curious`, `smug`, `pause`, `slow`) would not be `performed` under a strict comparison. Their shifts on the two carriers were `snorts` (+0.16 s, 0.00 s), `whispers` (-0.16 s, +0.16 s), `curious` (+0.16 s, +0.16 s), `smug` (0.00 s, -0.16 s), `pause` (-0.16 s, 0.00 s) and `slow` (0.00 s, +0.16 s). Five renderings also differed from the plain transcript, each because Deepgram heard "were" for "went" on the second carrier (`shaky breath`, `giggles`, `snorts`, `sinister`, `amused`): recognition noise, not a tag word. Read the palette as "no tag failed", not as "every tag works". Check first by ear the six tags above and the volume cues (`whispers`, `whisper`, `whispering`, `soft`, `softly`, `quietly`, `hushed`), whose effect is loudness, which this method cannot measure.
- **The read-aloud check matches whole words.** It flags a tag as `spoken-aloud` only when one of the tag's own words is heard exactly, so an inflected rendering (for example "whispered" for `whispers`) could slip past it. None appeared in the 134 recorded tagged transcripts.
- **Run-to-run variation.** Each tag was rendered once per carrier, and the noise floor comes from one pair of plain renderings per carrier, shared by every tag. In the full run that pair differed by 0.08 s (one audio step), so the bar was 0.16 s, barely above its 0.15 s floor. In an earlier 4-synthesis smoke run on the same voice the first carrier's pair differed by more than 0.27 s, and `whispers`, `performed` here, came back `no-audible-effect`. Verdicts near the bar are therefore not stable from run to run, and a tag that is read aloud only now and then could pass two renderings.

## Do the sound cues make sound?

The bake-off shows that a cue changed the clip length and was not read aloud. It cannot tell a laugh from a longer silence, so a second, smaller check looked for sound directly. It synthesized each cue once in front of the first carrier sentence, through the same plugin, voice, model and stability as the agent, had Deepgram time the spoken words, and measured the level of every stretch of at least 0.25 s that lies more than 0.1 s away from any word. In the plain clip and in the `pause` control such stretches sit near -46 dB (silence and the tails of words); a laugh or a breath sits well above that.

| Cue | Longest stretch with no word | Level |
|---|---|---|
| `laughs` | 1.3 s before the sentence | -22.0 dB |
| `chuckles` | 0.7 s inside the sentence | -16.8 dB |
| `evil laugh` | 0.5 s after the sentence | -28.3 dB |
| `gasps` | 0.4 s before the sentence | -27.2 dB |
| `clears throat` | 1.0 s before the sentence | -23.3 dB |
| `sighs` | 0.5 s before the sentence | -33.5 dB |
| `inhales deeply` | 0.8 s before the sentence | -36.8 dB |
| `heavy breathing` | 1.7 s before the sentence | -37.3 dB |
| `giggles` | 0.5 s after the sentence | -49.4 dB (about the floor) |
| `groans` | none of 0.25 s | n/a |
| plain clip, `pause` (controls) | 0.3 s and 0.6 s after the sentence | -46.4 dB, -47.4 dB |

Eight of the ten sound cues produce a distinct, audible sound. `giggles` and `groans` show none by this measure (the sound may be carried in the voice itself rather than as a separate stretch), so listen to those two first. This is one rendering per cue, made with a one-off script that is not in the repository, so it shows that a cue can make a sound, not how often it does, and a level above the floor is not proof that it sounds like a laugh.

## LLM compliance results

_Run 2026-10-04 with `tools/expressive_llm_check.py`: 7 scripted utterances x 2 samples on each tier's model (Standard / Premium / VIP), `reasoning.effort=low`, the exact Halloween instructions the agent sends, every request to the Databricks Unity Gateway. The baseline before tuning used 3 samples per utterance. Tuning was limited to one round and one re-run, so the final figures rest on 2 samples per utterance._

### After tuning (7 utterances x 2 samples)

**Standard** — `system.ai.gpt-5-nano`  (cue section ≈ +658 input tokens)
- story cues/100 words: median 6.7   factual: median 12.1
- median reply length (words): story 66   factual 16   short 16
- distinct cues: 13   compliance: 100% (exact spelling 100%)
- stage directions: 0   stacked: 0   cues inside facts: 0   replies missing a fact: 0
- verdict: PASS

**Premium** — `system.ai.gpt-5-5`  (cue section ≈ +658 input tokens)
- story cues/100 words: median 7.6   factual: median 12.5
- median reply length (words): story 66   factual 16   short 14
- distinct cues: 8   compliance: 100% (exact spelling 100%)
- stage directions: 0   stacked: 0   cues inside facts: 0   replies missing a fact: 0
- verdict: PASS

**VIP** — `system.ai.gpt-6-sol`  (cue section ≈ +658 input tokens)
- story cues/100 words: median 7.5   factual: median 10.6
- median reply length (words): story 58   factual 18   short 13
- distinct cues: 9   compliance: 100% (exact spelling 100%)
- stage directions: 0   stacked: 0   cues inside facts: 0   replies missing a fact: 0
- verdict: PASS

### Before -> after

Baseline: the Task 3 prompt and the first version of the harness, 7 utterances x 3 samples (cue section ≈ +599 input tokens on all three models). An asterisk marks replies that were refusals, not stories.

| | Standard | Premium | VIP |
|---|---|---|---|
| story cues/100 words (median) | 5.6 -> 6.7 | 9.1* -> 7.6 | 9.5* -> 7.5 |
| story reply length (median words) | 110 -> 66 | 22* -> 66 | 21* -> 58 |
| distinct cues | 12 -> 13 | 4 -> 8 | 5 -> 9 |
| compliance | 90% -> 100% | 100% -> 100% | 100% -> 100% |
| stage directions / stacked cues | 1 / 2 -> 0 / 0 | 0 / 0 -> 0 / 0 | 0 / 0 -> 0 / 0 |
| replies missing a fact | 0 -> 0 | 0 -> 0 | 9 -> 0 |
| verdict | FAIL -> PASS | FAIL -> PASS | FAIL -> PASS |

What the baseline showed and what changed:

- **Premium and VIP would not tell a story.** The governance block says to answer only from the tools, and a story is not in the tools, so both answered "tell me a scary story" with a 17-24 word deflection ("I don't have a story from my tools"). The deflection met the density and compliance targets but used only 4-5 distinct cues. The persona gained one bullet: a spooky story is play, not information; tell a made-up tale of four or five short sentences right away, no tool needed, and keep real orders, prices, policies and people out of it. Every other persona safety line and the whole governance block are unchanged, governance is still last, and facts about the business still come only from the tools (the "moon base" question is still answered with an "I don't have ..." abstention).
- **Standard wrote long stories (median 110 words) and strayed from the list.** It invented cues (`calm` four times, `gently`, `closing`), garbled one (`[dramat ic pause]`, which the tool counts as a stage direction because it has three words), and twice ended a line with `[pause]` just before a newline and the next cue, which the tool counts as stacked. The cue rules now say: use only cues from the lists and write none if none fits; open every reply with a cue; in a story cue almost every sentence; a short factual answer needs just one or two cues in all; never end a sentence or a line with a cue. The story bullet's "four or five short sentences" cut Standard's stories to a median of 66 words.
- **The story example grew** from two beats to four, with cues from four groups (pacing, volume, breath, attitude), so the models copy a length and a variety; the fourth, breath, slot is dropped for a voice with no cue left for it. The cue section grew from 2,095 to 2,331 characters, which the tool measures as +599 -> +658 input tokens.
- **Two harness corrections, not prompt changes.** (1) VIP had treated the lookup result pasted into the caller's message as the caller's claim and answered "I don't have a verified order lookup": all 9 factual replies lacked their facts. The tool now hands over a lookup result as a recorded tool call with its output, the way the agent's tools do, and the order question also asks for the total (a model that is not asked for the total rightly leaves it out). A 27-call probe with the original prompt showed the facts mostly restored (one reply left out the unasked total) and the story refusals unchanged. (2) A reply to a story request must be at least 25 words: every observed refusal was 24 words or fewer.

Reading these figures:

- **Small samples.** 2 replies per utterance is thin for targets that allow nothing: a single stray reply can add a stage direction, a stacked cue or a missing fact. Standard produced 3 such incidents (a stage direction and two stacked cues, in 2 of 21 replies) before tuning and none in 14 replies after, which is encouraging, not proof. Re-run the tool with its default 3 samples whenever the prompt, a tier model, the palette or the voice changes.
- **One-step emulation.** The tool shows the model a recorded tool result and a stand-in dataset prompt; it does not run the agent's tools or hold a multi-turn conversation.
- **Cues, not sound.** It measures what the models write. Whether a cue sounds right is the listen in the README ("Verifying the voice").

Targets (initial; changed only with evidence, recorded here): story median >= 5 cues per 100 words; >= 6 distinct cues per model; >= 1 cue in every factual reply; >= 95% of cues are vocabulary members once case/spacing are forgiven; 0 cues inside facts, stage directions, stacked cues, or missing facts. Added with the evidence above: no reply to a story request is shorter than 25 words. Added later with the laughter rule: at least 75% of the story replies carry a sound cue (laugh, gasp, sigh, breath), and no laughter is written out as words.

### Laughter and noises

The runs above showed the models rarely asked for laughter or noises: sound cues (the "Breath and sounds" group) were 1 of 37 cues on Standard, 4 of 39 on Premium and 4 of 33 on VIP, almost all `chuckles`. A later change added a cue rule: laughs, gasps, sighs and breaths only as cues, never written out as "ha ha" or `*laughs*` (the voice would read those aloud as words), and at least two sounds in every story, one at the scare and one on the last line. The tool now counts sound cues and laughter written as words.

- **First wording** (soft: "work in at least one sound"), full run, 7 utterances x 3 samples, cue section +730 input tokens. Premium passed (12 of 55 cues were sounds, every story had one). VIP passed (12 of 47, every story had one). Standard failed: 6 of 61 cues were sounds and 4 of 6 stories had one; 5 of its cues were `[gasp]`, which is not on the list, so the filter would have dropped them, and 3 cues were stacked. No laughter was written out as words in any of its 63 replies (re-scanned afterwards with the stricter detector).
- **Final wording** (firm, with exact spellings and where each sound goes), spot checks only: 3 story replies per tier (9 in all), and 6 earlier Standard replies read in full. Every reply had 2 to 3 sound cues (`[gasps]`, `[deep breaths]` or `[sighs]` at the scare; `[laughs]` or `[chuckles]` on the last line); none was off the list, written out as words, or stacked. The cue section now costs +779 input tokens (2,801 characters).
- **The price.** Stories are now formulaic: a gasp or breath, then a laugh, in much the same places. If that sounds repetitive, loosen the "Every story MUST include sounds" line.
- **Not verified.** The full 69-call run was not repeated with the final wording, and 9 to 15 replies is a small sample. The written-laughter check covers laughter only (`ha ha`, `Muhahaha`, `Heh heh`, `(laughs)`, `*laughs*`), not written noises such as "ahh". The rule quotes the strings it forbids, which could prime the small model to write them; none of the replies above did, so the wording is kept, and if a later run shows written laughter, switch to wording that names none of them.
