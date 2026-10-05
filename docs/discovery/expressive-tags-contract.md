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
- **Run-to-run variation.** Each tag was rendered once per carrier, and the noise floor comes from one pair of plain renderings per carrier, shared by every tag. In the full run that pair differed by 0.08 s (one audio step), so the bar was 0.16 s, barely above its 0.15 s floor. In an earlier 4-synthesis smoke run on the same voice the first carrier's pair differed by more than 0.27 s, and `whispers`, `performed` here, came back `no-audible-effect`. Verdicts near the bar are therefore not stable from run to run, and a tag that is read aloud only now and then could pass two renderings.
