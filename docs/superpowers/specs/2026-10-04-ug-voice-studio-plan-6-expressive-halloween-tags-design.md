# UG Voice Studio — Plan 6: Expressive Halloween Monster Voice (richer ElevenLabs audio tags)

**Date:** 2026-10-04
**Status:** Design (autonomous run under the owner's goal; written so the owner can correct it in the PR)
**Issues:** epic #28; tasks T1 #29 · T2 #30 · T3 #31 · T4 #32 · T5 #33
**Builds on:** Plan 5 (`docs/superpowers/specs/2026-10-02-ug-voice-studio-plan-5-ai-decide-halloween-design.md`), gotchas G9–G13 (`docs/gotchas.md`)

---

## 1. Summary

In Halloween mode the agent speaks through ElevenLabs `eleven_v3_conversational`, which *performs* inline
audio tags such as `[whispers]` or `[building tension]`. The owner saw the ElevenLabs playground produce a far
more theatrical horror narration from tags like these:

> [laughing] They said she disappeared on a Tuesday. [building tension] No note. No struggle. No trace. [soft]
> Just an open window and a cold cup of coffee. [dismissive] Nobody talked about it much after that.
> [deep breaths] Nobody wanted to. [whisper] But everybody knew.

They want the same from our agent: **whenever Halloween content is chosen, the LLM is automatically given a rich
cue palette and style guide, and writes its replies with those cues.** The voice id and model do not change.

## 2. What the owner said vs. what I assumed

| Said | Assumed (and how it was cleared) |
|---|---|
| "when we have the drafting stuff ... send the expressive tags for the LLM" | "Drafting" = the LLM's reply prompt in Halloween mode. Cleared by code search: `web_server.py` and `generate.py` have **no** Halloween content path; the only place Halloween text is generated is the agent reply, whose prompt is built in `src/agent_prompt.py` (persona + cue rules) and re-sent on every mode flip by `VoiceModeController`. |
| "the voice ID can still be the same" | Keep `UG_HALLOWEEN_VOICE_ID`, `eleven_v3_conversational`, and the `stability`-only voice settings. Stability stays a tunable (`UG_HALLOWEEN_STABILITY`); T1 compares 0.0 vs 0.5 for tag obedience. |
| "the LLM needs to generate the expressive tags" | The model writes them inline; the pipeline (`encode_tags` → stock filters → `decode_tags`) carries them to the TTS and strips them from the transcript. |
| "way more expressive" | Measured, not felt: cues per 100 words, distinct cues, allowlist compliance, on all three tier models (§8). |

## 3. Why it is not expressive today (verified)

1. `src/agent_prompt.py:97` — the cue rules say "Use at most **two** cues in a reply".
2. `app/expressive.py:28-30` — the vocabulary is 7 tags (`whispers, sighs, laughs, mischievously, nervously, exhales, inhales deeply`).
3. `app/expressive.py` `encode_tags` **drops** every bracketed group that is not in the vocabulary (G13), from the
   audio *and* the transcript. So `[building tension]` or `[laughing]` written by a willing model never reaches the TTS.
4. `_cue_rules` renders the list alphabetically (`app/agent.py:246` passes `sorted(profile.tags)`) with no guidance
   on when a cue fits, and the persona is a mild "playful spooky host".

So both halves must change: *what the model is told* and *what is allowed through the filter*.

## 4. Goals and non-goals

**Goals**

- G-A (richness): Halloween replies carry varied, well-placed cues — targets in §8, tuned with evidence.
- G-B (safety): Plan 5's G9–G13 hold unchanged (§7).
- G-C (automatic): the palette and guide are in the instructions exactly when the active profile is the ElevenLabs
  v3 Halloween voice (same-turn and announced switches included), and absent otherwise.
- G-D (verified): every tag in the palette is proven *performed, not read aloud* on the configured voice/model.

**Non-goals:** a new voice or model; AI-Decide policy changes; UI work (the client strip already removes any
closed `[...]` ≤ 120 chars, `studio.js:1168`); sound-effect, crying and accent tags (startling, distressing or
risk of offensive impressions — the same exclusions Plan 5 made).

## 5. Approaches considered

| | Approach | Verdict |
|---|---|---|
| **A** | **Curated allowlist, verified by a bake-off, plus a richer prompt** (grouped palette, density + placement rules, worked example) | **Chosen.** Keeps G13 deterministic ("a cue the TTS does not perform is never read aloud"), is unit-testable, and needs no pipeline redesign. |
| B | Free-form tags: accept any short `[descriptive text]` | Maximum expressivity, but nothing guarantees `eleven_v3_conversational` performs rather than speaks an arbitrary tag; weakens G13 and is untestable. Rejected. |
| C | Prompt-only (no vocabulary change) | Dead on arrival: the filter drops unknown cues. Rejected. |

A is extended with one cheap robustness measure from B's side: **tolerant matching** — the model may write
`[Whispers]` or `[ building   tension ]`; the filter canonicalises case and spacing *before* the allowlist test,
so a near-miss is performed instead of silently lost. Only allowlist members can ever be spoken, so G13 is intact.

## 6. Design

### 6.1 Palette as data — `src/expressive_palette.py` (new, pure)

```python
PALETTE: dict[str, tuple[str, ...]]      # category -> verified tags, in display order
GROUP_HINTS: dict[str, str]              # category -> one-line "when to use it"
def flatten(palette=PALETTE) -> tuple[str, ...]
def grouped(tags: Iterable[str]) -> list[tuple[str, tuple[str, ...]]]   # palette order; only tags present
```

Four categories (final membership is fixed by T1's bake-off, not by this spec): `breath` (non-verbal: breathing,
laughter, sighs), `volume` (whisper / soft / low / loud intensity), `emotion` (attitude: dismissive, mischievous,
nervous, menacing ...), `pacing` (tension and timing: building tension, pause, slow ...). Every name is
lowercase, ≤ 32 characters (`MAX_TAG_LEN`), unique across categories.

### 6.2 Filter — `app/expressive.py`

- `SPOOKY_TAGS = flatten(PALETTE)` (name kept; `voice_profiles.py` and tests keep working).
- `canonical_tag(raw) = " ".join(raw.split()).lower()` applied in `_encode_pieces` before the membership test;
  the canonical name is what gets the placeholder and reaches `on_tag`.
- `_scan`, the placeholder scheme (U+E000 + index), `MAX_TAG_LEN` and `MAX_GROUP_LEN` are unchanged.
- `strip_tags` / `strip_tags_stream` already remove *every* bracketed group, so transcripts need no change.

### 6.3 Prompt — `src/agent_prompt.py`

- `HALLOWEEN_PERSONA`: from "playful, spooky Halloween host" to a **theatrical monster-storyteller** (slow-burn,
  tension, menace played for fun). All safety lines stay verbatim in spirit: never threatening, cruel or gory;
  facts stated plainly; drop the act if the caller sounds uncomfortable; honour "normal voice".
- `_cue_rules(tags)`: the "at most two" cap is replaced by a bounded density guide (a cue at the start of a beat;
  at most one per sentence and never two in a row; none inside a number, date, name or any other fact; "use no
  other bracketed text"), the palette **grouped by category with its hint** (only tags present in
  the active vocabulary — tags unknown to the palette, as in older tests, are listed ungrouped), and one short
  worked example showing cues *around* a fact (never inside it) plus a tension-building passage like the
  reference. Rendered only when the vocabulary is non-empty, so standard, Deepgram-fallback and OpenAI prompts stay
  byte-identical.
- `ON_NOTE` / `ANNOUNCE_ON`: invite one cue in the opening flourish; still consistent with `BRIDGE_ON` (T11).
- Governance stays the last block (G11).

### 6.4 Data flow (unchanged shape)

```
LLM reply with [cues] ──► encode_tags(vocabulary)  allowlisted cue -> private-use char; everything else dropped
                              │
                    filter_markdown ─ filter_emoji   (never see a bare "[")
                              │
                          decode_tags ──► ElevenLabs text-to-dialogue (performs the cue)
LLM reply ──► transcription_node: strip_tags_stream ──► transcript   (+ client CUE_RE strip, belt and braces)
```

### 6.5 Verification tools (`tools/`, manual, never in CI)

- `tools/expressive_bakeoff.py` (T1): per candidate tag, synthesize a short carrier **with and without** the tag
  through the production path (livekit-plugins-elevenlabs text-to-dialogue stream), transcribe with Deepgram, and
  classify: **performed** (audio changes, tag words absent from the transcript) / **spoken-aloud** (tag words in
  the transcript) / **no-audible-effect**. Compares stability 0.0 vs 0.5. Output feeds the palette and
  `docs/discovery/expressive-tags-contract.md`.
- `tools/expressive_llm_check.py` (T4): builds the exact Halloween instructions and runs scripted utterances
  against the Standard / Premium / VIP models through the same gateway endpoint, several samples each, and reports
  the §8 metrics. Metric code is pure and unit-tested offline.

Both read credentials from the gitignored `.env.local`, never print them, and cap their call volume.

## 7. Invariants (Plan 5 G9–G13) and how each is kept

| Invariant | How this plan keeps it |
|---|---|
| G9 the LLM never sees the tier | Added prompt text carries no tier/loyalty wording (test, as today). |
| G10 the decider sees only the caller | Untouched. |
| G11 governance is last | Persona, cue rules, example all sit before the governance block (test). |
| G12 exit always wins / ≤ 1 transition | Untouched. |
| G13 a cue the TTS doesn't perform is never read aloud | The vocabulary contains only tags T1 proved *performed*; canonicalisation maps only onto members; non-dialogue models and fallback voices still get an empty vocabulary, so every cue is dropped. |
| Facts stay exact | Rules forbid cues inside facts; the worked example demonstrates it; T4 measures "cues inside facts = 0" and "facts verbatim = 100%". |

## 8. Testing and success criteria

1. **Unit (TDD, offline):** palette invariants; every tag round-trips `encode → stock filters → decode`, also
   split across chunks; canonicalisation; non-members dropped from audio and transcript; empty vocabulary drops
   all; client `CUE_RE` parity (a test extracts the regex from `studio.js` and strips every palette tag); prompt
   contains the grouped palette only with a non-empty vocabulary, governance last, no tier words, standard prompt
   byte-identical; mode flips add and remove the palette. Suite baseline before this work: **891 passed**
   (live Lakebase test excluded).
2. **Live TTS→STT (T1):** every shipped tag is *performed*; none *spoken-aloud*.
3. **Live LLM (T4), initial targets** (adjust only with evidence; record why): story replies median ≥ 5 cues per
   100 words and ≥ 6 distinct cues across the suite per tier model; factual replies ≥ 1 cue, 0 cues inside a fact,
   facts 100% verbatim; allowlist compliance ≥ 95% after canonicalisation; 0 non-cue bracketed text.
4. **Human listen (T5 recipe):** the one check that needs ears; documented, not automated.

## 9. Risks and mitigations

| Risk | Mitigation |
|---|---|
| The weakest tier model (`gpt-5-nano`) follows a longer prompt less reliably | T4 measures every tier; the worked example and grouped palette are the mitigation; tune until the weakest model passes. |
| Over-tagging destabilises v3 audio | Bounded density rule; T1 checks stability 0.0 vs 0.5; density target tuned in T4. |
| Tag behaviour is voice- and model-specific | The contract records the voice/model/stability it was verified on; re-run the tool when either changes (gotcha entry, T5). |
| Prompt grows (latency, cost) in Halloween mode only | T3 measures the token overhead; standard mode is byte-identical. |
| The playground's tags are produced by ElevenLabs' own tagger, not a fixed list | Hence a verified allowlist rather than free-form tags (approach B rejected). |

## 10. Delivery

One feature branch (`feat/halloween-expressive-tags`), small checkpoint commits per task, **subagent-driven
development** (a fresh implementer per task, then a spec-compliance review and a code-quality review), tests run
before every push, one **draft PR** into `main` (there is no `develop` branch) linking #28–#33.

| Task | Issue | Output |
|---|---|---|
| T1 | #29 | `tools/expressive_bakeoff.py`, `docs/discovery/expressive-tags-contract.md` (verified palette) |
| T2 | #30 | `src/expressive_palette.py`, `app/expressive.py`, tests |
| T3 | #31 | `src/agent_prompt.py`, tests |
| T4 | #32 | `tools/expressive_llm_check.py`, prompt tuning, results in the contract |
| T5 | #33 | `docs/gotchas.md`, `README.md`, final gate, PR |
