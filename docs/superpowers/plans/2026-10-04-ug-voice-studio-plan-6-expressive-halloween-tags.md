# Expressive Halloween Monster Voice (Plan 6) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When the studio speaks in the Halloween voice, the LLM writes replies with a rich, verified palette of ElevenLabs audio tags (`[laughing]`, `[building tension]`, `[soft]`, `[dismissive]`, `[deep breaths]`, `[whisper]`, ...) that `eleven_v3_conversational` performs, instead of today's 7 tags capped at two per reply.

**Architecture:** A closed, verified allowlist (the palette) stays the only thing that can put bracketed text on the air (G13). It becomes single-sourced data (`src/expressive_palette.py`) that feeds both the filter vocabulary (`app/expressive.py`) and a grouped, rule-bearing prompt section (`src/agent_prompt.py`). Two manual tools prove it live: a TTS→STT bake-off decides which tags are safe, and an LLM harness measures compliance on the three tier models.

**Tech Stack:** Python 3.12, pytest (asyncio auto mode), livekit-agents 1.8.3 + livekit-plugins-elevenlabs 1.8.3, Deepgram REST (nova-3), the `openai` SDK 2.54 pointed at the Unity Gateway Responses API, `uv`.

**Spec:** `docs/superpowers/specs/2026-10-04-ug-voice-studio-plan-6-expressive-halloween-tags-design.md` (issues: epic #28; T1 #29, T2 #30, T3 #31, T4 #32, T5 #33)

## Global Constraints

Every task's requirements implicitly include all of these.

- Run everything through `uv`, always with `--frozen` (`uv run --frozen ...`). Plain `uv run` / `uv sync` re-locks `uv.lock` to an internal package proxy.
- Test command: `uv run --frozen pytest -q --ignore=tests/test_integration_data_plane.py`. **Never run `tests/test_integration_data_plane.py`**: with a `.env.local` present it writes real datasets to a live Lakebase. Baseline before this work: **891 passed**.
- No test under `tests/` may touch the network or read `.env.local`. Live behaviour lives only in `tools/`.
- Git: stage explicit paths only (never `git add -A` / `git commit -a`). The commit identity is already `datasciencemonkey <datasciencemonkey@gmail.com>`. End every commit message with the trailer line `Co-authored-by: Isaac <no-reply@databricks.com>`. Never write `[SC-` in a commit message (a pre-push guard rejects it). After every `git commit`, run `git status --short`; a Databricks hook rewrites `uv.lock`, so if it shows ` M uv.lock` run `git restore -- uv.lock`.
- This is a **public repository**. No workspace host or name, Lakebase project or endpoint, catalog or schema, SQL warehouse id, email address, voice id, API key or token may appear in any file, test, doc, log excerpt or commit message. Credentials are read from the gitignored `.env.local` (`ELEVEN_API_KEY`, `UG_HALLOWEEN_VOICE_ID`, `DEEPGRAM_API_KEY`, `DATABRICKS_HOST`, `DATABRICKS_TOKEN`, `UG_MODEL_*`) and are never printed. The ElevenLabs key is scoped: it can synthesize speech but gets HTTP 401 on account and model endpoints, so never call those.
- Palette: four categories named `breath`, `volume`, `emotion`, `pacing` (display order); every tag one or two lowercase words (ElevenLabs: best performance with 1-2 words; longer tags get read aloud), letters and single spaces only, at most 32 characters, unique across categories.
- Plan 5 invariants stay true: G9 (the LLM never sees the tier), G10 (the decider sees only the caller), G11 (the governance block is the last block of the instructions), G12 (an exit always wins; at most one transition per turn), G13 (a cue the TTS does not perform is never read aloud, and tags never reach the transcript or UI).
- Standard, Deepgram-fallback and OpenAI prompts and audio paths must not change: no cue rules, no palette, every cue dropped.
- **The backend LLM does not change.** The agent's model stays the Databricks-served Unity Gateway model for the caller's tier (`UG_MODEL_*`, `system.ai.*`, reached by `openai.responses.LLM` at `{host}/ai-gateway/openai/v1`). This plan changes prompt text and the TTS tag filter only. Do not touch `app/agent.py`, `app/voice_profiles.py`, `src/policy/`, `src/services/`, `app.yaml`, `.env.example`, `pyproject.toml`, `uv.lock` or any requirements file, and add no other LLM provider. (The `openai` package used by `tools/expressive_llm_check.py` is only the client library: every request goes to the Databricks gateway.)
- Live budgets: the bake-off makes at most 150 syntheses per run; the LLM check at most 100 calls per run (about 400 across the whole plan).

## Review Focus

The input classes most likely to bite, each with the test that pins it:

1. **The model writes a cue with the wrong case or spacing** (`[Whispers]`, `[ building   tension ]`): it must be performed as the canonical tag, never silently lost, never leaking brackets. T2: `test_case_and_spacing_from_the_model_are_forgiven`, `test_inner_spacing_of_a_multi_word_tag_is_collapsed`.
2. **The model puts a cue inside a fact** (`Order 48[sighs]213`): the audio would pause mid-number. T3 writes the rule and the example around it; T4: `test_measure_flags_a_cue_that_splits_a_fact`.
3. **A caller passes tags the palette does not know** (older tests, a custom vocabulary): listed under "Other cues", never a crash. T3: `test_tags_the_palette_does_not_know_are_listed_as_other_cues`.
4. **The vocabulary is empty or lacks a whole category** (non-v3 model, fallback voice, or the bake-off removed a group): no cue section, or no worked example, never an empty heading. T3: `test_no_example_when_the_voice_lacks_a_whole_category`, `test_standard_and_fallback_prompts_carry_no_cue_section_and_no_brackets`.
5. **A bracketed stage direction or an unknown cue reaches the TTS stream** (`[whispers menacingly into the microphone]`, `[explosion]`): dropped from audio and transcript. Existing G13 tests stay green; T2: `test_canonicalising_never_widens_what_can_be_spoken`; T4: `test_measure_counts_stage_directions`.

---

## File Structure

| File | Change | Responsibility |
|---|---|---|
| `tools/__init__.py` | create | marks the manual-tools package |
| `tools/expressive_bakeoff.py` | create (T1) | TTS→STT bake-off: which tags are performed vs read aloud |
| `docs/discovery/expressive-tags-contract.md` | create (T1), extend (T4, T5) | the verified palette, method, results, limits |
| `src/expressive_palette.py` | create (T2) | palette data + `flatten` / `grouped` helpers (stdlib only) |
| `app/expressive.py` | modify (T2) | `SPOOKY_TAGS` from the palette; `canonical_tag`; tolerant matching |
| `src/agent_prompt.py` | modify (T3, tuned in T4) | monster persona, grouped cue rules, worked example |
| `tools/expressive_llm_check.py` | create (T4) | live LLM compliance harness (pure metrics + runner) |
| `tests/test_expressive_bakeoff.py` | create (T1) | offline tests for the bake-off's pure logic |
| `tests/test_expressive_palette.py` | create (T2) | palette invariants + contract parity |
| `tests/test_expressive.py` | modify (T2) | vocabulary, round-trip, canonicalisation, client-strip parity |
| `tests/test_agent_prompt.py` | modify (T3) | new cue-section behaviour |
| `tests/test_expressive_tools.py` | create (T4) | offline tests for the LLM harness's pure metrics |
| `docs/gotchas.md`, `README.md` | modify (T5) | findings and how to re-verify |

## Delivery protocol (controller, between tasks)

After each task's implementer reports DONE: the controller re-runs the full suite itself, dispatches a spec-compliance reviewer and then a code-quality reviewer, fixes findings with the same implementer, then pushes the branch, comments on the task's issue with the commit and the test evidence, and ticks the epic's checklist.

---

### Task 1: Bake-off tool and the verified palette (issue #29)

**Files:**
- Create: `tools/__init__.py`, `tools/expressive_bakeoff.py`
- Create: `tests/test_expressive_bakeoff.py`
- Create: `docs/discovery/expressive-tags-contract.md`

**Interfaces:**
- Produces (for T2): the fenced block in the contract labelled `palette`, one line per category in the form `category: tag | tag | tag`, categories in the order `breath`, `volume`, `emotion`, `pacing` (a category with no performed tag is omitted). T2 copies it into `src/expressive_palette.py` and a test keeps the two equal.
- Produces (for T4, T5): `python tools/expressive_bakeoff.py --from-palette` re-verifies the shipped palette.

- [ ] **Step 1: Write the failing tests**

`tests/test_expressive_bakeoff.py`:

```python
"""Offline tests for tools/expressive_bakeoff.py: the pure logic only (no network, no credentials)."""
import io
import re
import wave

import pytest

from tools import expressive_bakeoff as bake


def _trial(**kw):
    base = dict(transcript="somebody left the back door open again tonight",
                seconds=3.0,
                base_transcript="somebody left the back door open again tonight",
                base_seconds=3.0, noise_s=0.0)
    base.update(kw)
    return bake.Trial(**base)


def test_wav_round_trip():
    pcm = b"\x01\x00" * 2400
    wav = bake.pcm_to_wav(pcm, sample_rate=24000)
    assert bake.wav_seconds(wav) == pytest.approx(0.1)
    with wave.open(io.BytesIO(wav), "rb") as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (1, 2, 24000)


def test_tag_words_drop_short_words_and_punctuation():
    assert bake.tag_words("building tension") == ["building", "tension"]
    assert bake.tag_words("sighs") == ["sighs"]
    assert bake.tag_words("a b") == []


def test_a_tag_read_aloud_is_spoken_aloud():
    heard = _trial(transcript="building tension somebody left the back door open again tonight")
    assert bake.spoken_aloud("building tension", heard)


def test_a_word_the_plain_carrier_already_has_is_not_counted_as_read_aloud():
    both = _trial(transcript="the door is soft somebody left", base_transcript="the door is soft")
    assert not bake.spoken_aloud("soft", both)


def test_verdict_prefers_spoken_aloud_over_everything():
    heard = _trial(transcript="deep breaths somebody left the back door open again tonight", seconds=9.0)
    assert bake.verdict("deep breaths", [heard]) == "spoken-aloud"


def test_verdict_performed_when_the_audio_changes_without_the_words():
    longer = _trial(seconds=4.2)
    assert bake.verdict("sighs", [longer]) == "performed"
    laugh = _trial(transcript="ha ha somebody left the back door open again tonight")
    assert bake.verdict("laughs", [laugh]) == "performed"


def test_verdict_no_audible_effect_when_nothing_changes():
    assert bake.verdict("sighs", [_trial()]) == "no-audible-effect"


def test_run_to_run_noise_raises_the_bar_for_a_duration_change():
    noisy = _trial(seconds=3.2, noise_s=0.2)          # 0.2 s apart but the plain carrier itself varies by 0.2 s
    assert bake.verdict("sighs", [noisy]) == "no-audible-effect"


def test_one_spoken_trial_condemns_the_tag():
    good, bad = _trial(seconds=4.5), _trial(transcript="pause somebody left the back door open again tonight")
    assert bake.verdict("pause", [good, bad]) == "spoken-aloud"


def test_leaked_tags_finds_tags_read_aloud_in_a_passage():
    plain = "They said she disappeared on a Tuesday. No note."
    heard = "laughing they said she disappeared on a tuesday no note"
    assert bake.leaked_tags(["laughing", "building tension"], heard, plain) == ["laughing"]


def test_candidates_are_well_formed_and_unique():
    flat = [t for tags in bake.CANDIDATES.values() for t in tags]
    assert len(flat) == len(set(flat))
    assert set(bake.CANDIDATES) <= {"breath", "volume", "emotion", "pacing"}
    for tag in flat:
        assert re.fullmatch(r"[a-z]+(?: [a-z]+)?", tag) and len(tag) <= 32, tag


def test_the_reference_example_tags_are_all_candidates():
    flat = {t for tags in bake.CANDIDATES.values() for t in tags}
    assert {"laughing", "building tension", "soft", "dismissive", "deep breaths", "whisper"} <= flat


def test_carriers_do_not_contain_any_candidate_word():
    words = {w for tags in bake.CANDIDATES.values() for t in tags for w in bake.tag_words(t)}
    for carrier in bake.CARRIERS:
        assert not words & set(bake.words(carrier)), carrier


def test_plan_counts_syntheses_and_stays_in_budget_by_default():
    tags = [t for tags in bake.CANDIDATES.values() for t in tags]
    jobs = bake.plan_jobs(tags, bake.CARRIERS)
    assert len(jobs) == len(tags) * len(bake.CARRIERS) + 2 * len(bake.CARRIERS)
    assert len(jobs) <= bake.DEFAULT_BUDGET


def test_a_job_puts_the_tag_in_front_of_the_carrier():
    jobs = bake.plan_jobs(["sighs"], bake.CARRIERS[:1])
    assert [j.text for j in jobs if j.kind == "tag"] == [f"[sighs] {bake.CARRIERS[0]}"]
    assert [j.text for j in jobs if j.kind == "base"] == [bake.CARRIERS[0]] * 2


def test_dry_run_touches_nothing_and_reports_the_count(capsys):
    assert bake.main(["--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "syntheses" in out and str(bake.DEFAULT_BUDGET) in out


def test_a_typo_in_only_is_refused_before_any_call(capsys):
    assert bake.main(["--dry-run", "--only", "[sighs]"]) == 2
    assert "not a valid tag" in capsys.readouterr().out
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `uv run --frozen pytest tests/test_expressive_bakeoff.py -q`
Expected: collection error / FAIL (`ModuleNotFoundError: No module named 'tools'`).

- [ ] **Step 3: Create the package and the tool**

`tools/__init__.py`:

```python
"""Manual developer tools for verifying the expressive Halloween voice. Never run in CI; they spend real
API credits and read credentials from the gitignored .env.local."""
```

`tools/expressive_bakeoff.py`:

```python
"""Bake-off: which ElevenLabs audio tags does the Halloween voice PERFORM, and which does it READ ALOUD?

For each candidate tag it synthesizes a short carrier sentence with the tag in front and without it, through the
same plugin, model, voice and voice settings the Halloween profile uses (app/voice_profiles.py), transcribes the
audio with Deepgram, and classifies the tag:

  spoken-aloud       the tag's own words are heard in the audio (G13: never allowed on the air)
  performed          not read aloud, and the audio differs from the plain carrier beyond run-to-run noise
  no-audible-effect  not read aloud, but indistinguishable from the plain carrier (safe, but useless)

Judged by transcript and duration, not by ear: "performed" means the voice did something other than read the tag,
not that it did the intended thing. A human listen is the last check (README, "Verifying the voice").

Credentials come from the gitignored .env.local and are never printed. The ElevenLabs key may be scoped to speech
only, so this tool never calls an account or model endpoint.

    uv run --frozen python tools/expressive_bakeoff.py --dry-run
    uv run --frozen python tools/expressive_bakeoff.py --only "whispers,building tension" --carriers 1
    uv run --frozen python tools/expressive_bakeoff.py --json /tmp/bakeoff.json
    uv run --frozen python tools/expressive_bakeoff.py --from-palette      # re-verify the shipped palette
    uv run --frozen python tools/expressive_bakeoff.py --stability-check   # 0.0 vs 0.5 on the reference passage
"""
from __future__ import annotations

import argparse
import asyncio
import io
import json
import os
import re
import sys
import wave
from dataclasses import asdict, dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

DEFAULT_BUDGET = 150          # the most syntheses one run may make
MIN_DELTA_S = 0.15            # a cue must move the duration by at least this much ...
NOISE_FACTOR = 2.0            # ... and by this many times the run-to-run spread of the plain carrier

# Short, neutral carriers: long enough for v3 to behave, free of every candidate's own words.
CARRIERS: tuple[str, ...] = (
    "Somebody left the back door open again tonight.",
    "The lights went dark and the whole house grew still.",
)

# The ElevenLabs playground example that started this work, verbatim.
REFERENCE_TEXT = (
    "[laughing] They said she disappeared on a Tuesday. [building tension] No note. No struggle. No trace. "
    "[soft] Just an open window and a cold cup of coffee. [dismissive] Nobody talked about it much after that. "
    "[deep breaths] Nobody wanted to. [whisper] But everybody knew."
)

# Candidate cues by group, spelled as ElevenLabs documents them or as the playground example writes them.
# Left out on purpose: crying (distressing), sound effects (startling), accents (risk of offensive impressions),
# and shouting (startling to a caller).
CANDIDATES: dict[str, tuple[str, ...]] = {
    "breath": ("deep breaths", "exhales", "inhales deeply", "sighs", "gasps", "gulps", "clears throat",
               "heavy breathing", "shaky breath", "laughs", "laughing", "chuckles", "evil laugh",
               "maniacal laughter", "giggles", "snorts", "wheezing", "groans", "exhales sharply", "panting",
               "menacing laugh"),
    "volume": ("whispers", "whisper", "whispering", "soft", "softly", "quietly", "hushed", "low voice",
               "deep voice", "growls", "raspy", "rumbling"),
    "emotion": ("dismissive", "mischievously", "nervously", "menacing", "sinister", "ominous", "eerie",
                "sarcastic", "curious", "amused", "dramatic", "somber", "fearful", "cold", "gleeful", "smug",
                "playful", "excited", "panicking", "deadpan", "thoughtful"),
    "pacing": ("building tension", "pause", "long pause", "dramatic pause", "slowly", "suspenseful",
               "hesitates", "trailing off", "rushed", "measured", "slow", "short pause", "drawn out"),
}

_VALID_TAG = re.compile(r"[a-z]+(?: [a-z]+)?")


# ------------------------------------------------------------------ pure logic (unit-tested offline)

def words(text: str) -> list[str]:
    return re.findall(r"[a-z']+", text.lower())


def tag_words(tag: str) -> list[str]:
    """The words of a tag that could be heard: lowercase, at least three letters."""
    return [w for w in words(tag) if len(w) >= 3]


@dataclass(frozen=True)
class Trial:
    transcript: str        # what Deepgram heard with the tag in front
    seconds: float         # audio length with the tag
    base_transcript: str   # what it heard for the plain carrier
    base_seconds: float
    noise_s: float         # |difference| between two plain renderings of the same carrier


def spoken_aloud(tag: str, trial: Trial) -> bool:
    """The tag's own words were heard, and the plain carrier did not already contain them."""
    heard, base = set(words(trial.transcript)), set(words(trial.base_transcript))
    return any(w in heard and w not in base for w in tag_words(tag))


def audible_effect(trial: Trial) -> bool:
    """The audio differs from the plain carrier beyond normal run-to-run variation."""
    threshold = max(MIN_DELTA_S, NOISE_FACTOR * trial.noise_s)
    if abs(trial.seconds - trial.base_seconds) >= threshold:
        return True
    return words(trial.transcript) != words(trial.base_transcript)   # a laugh or breath that was transcribed


def verdict(tag: str, trials: list[Trial]) -> str:
    if any(spoken_aloud(tag, t) for t in trials):
        return "spoken-aloud"
    if any(audible_effect(t) for t in trials):
        return "performed"
    return "no-audible-effect"


def leaked_tags(tags: list[str], transcript: str, plain_text: str) -> list[str]:
    """Which of `tags` were read aloud in a passage: their words are heard but are not in the plain text."""
    heard, plain = set(words(transcript)), set(words(plain_text))
    return [t for t in tags if any(w in heard and w not in plain for w in tag_words(t))]


def pcm_to_wav(pcm: bytes, sample_rate: int, channels: int = 1) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm)
    return buf.getvalue()


def wav_seconds(wav: bytes) -> float:
    with wave.open(io.BytesIO(wav), "rb") as w:
        return w.getnframes() / w.getframerate()


@dataclass(frozen=True)
class Job:
    kind: str        # "base" (the plain carrier) | "tag"
    tag: str         # "" for a plain carrier
    carrier_ix: int
    repeat: int      # 0 or 1 for the two plain renderings; 0 for a tagged one
    text: str


def plan_jobs(tags: list[str], carriers: tuple[str, ...]) -> list[Job]:
    jobs = [Job("base", "", ci, rep, c) for ci, c in enumerate(carriers) for rep in (0, 1)]
    jobs += [Job("tag", tag, ci, 0, f"[{tag}] {c}") for tag in tags for ci, c in enumerate(carriers)]
    return jobs


def select_tags(only: str | None, from_palette: bool) -> list[str]:
    if only:
        chosen = [t.strip() for t in only.split(",") if t.strip()]
    elif from_palette:
        from app.expressive import SPOOKY_TAGS
        chosen = list(SPOOKY_TAGS)
    else:
        chosen = [t for tags in CANDIDATES.values() for t in tags]
    return list(dict.fromkeys(chosen))


def render_table(rows: list[tuple[str, str, str]]) -> str:
    out = ["| Tag | Verdict | Note |", "|---|---|---|"]
    out += [f"| `{tag}` | {v} | {note} |" for tag, v, note in rows]
    return "\n".join(out)


# ------------------------------------------------------------------ live I/O (not unit-tested)

def _require_env(*names: str) -> dict[str, str]:
    missing = [n for n in names if not os.environ.get(n, "").strip()]
    if missing:
        print(f"Missing in .env.local: {', '.join(missing)} (names only; nothing was printed about their values)")
        raise SystemExit(2)
    return {n: os.environ[n].strip() for n in names}


async def synthesize_wav(tts, text: str) -> bytes:
    """WAV bytes for `text`, driving the plugin the way app/studio_agent.py does."""
    frames = []
    async with tts.stream() as stream:
        stream.push_text(text)
        stream.end_input()
        async for ev in stream:
            frames.append(ev.frame)
    if not frames:
        raise RuntimeError("the TTS returned no audio")
    first = frames[0]
    return pcm_to_wav(b"".join(bytes(f.data) for f in frames), first.sample_rate, first.num_channels)


def transcribe(wav: bytes, api_key: str) -> str:
    import httpx

    r = httpx.post(
        "https://api.deepgram.com/v1/listen",
        params={"model": "nova-3", "language": "en", "punctuate": "false", "smart_format": "false"},
        headers={"Authorization": f"Token {api_key}", "Content-Type": "audio/wav"},
        content=wav, timeout=60,
    )
    r.raise_for_status()
    return r.json()["results"]["channels"][0]["alternatives"][0]["transcript"]


async def _run_live(args, tags: list[str], carriers: tuple[str, ...]) -> int:
    import aiohttp
    from livekit.agents import NOT_GIVEN
    from livekit.plugins import elevenlabs

    env = _require_env("ELEVEN_API_KEY", "UG_HALLOWEEN_VOICE_ID", "DEEPGRAM_API_KEY")
    model = os.environ.get("UG_HALLOWEEN_TTS_MODEL", "").strip() or "eleven_v3_conversational"
    stability = args.stability if args.stability is not None else float(os.environ.get("UG_HALLOWEEN_STABILITY") or 0.5)

    async with aiohttp.ClientSession() as session:
        def make_tts(stab: float):
            return elevenlabs.TTS(
                model=model, voice_id=env["UG_HALLOWEEN_VOICE_ID"],
                voice_settings=elevenlabs.VoiceSettings(stability=stab, similarity_boost=NOT_GIVEN),
                http_session=session)

        async def clip(tts, text: str) -> tuple[str, float]:
            wav = await synthesize_wav(tts, text)
            return await asyncio.to_thread(transcribe, wav, env["DEEPGRAM_API_KEY"]), wav_seconds(wav)

        if args.stability_check:
            plain = re.sub(r"\[[^\]]*\]\s*", "", REFERENCE_TEXT)
            ref_tags = ["laughing", "building tension", "soft", "dismissive", "deep breaths", "whisper"]
            print(f"model={model}  reference passage, {len(ref_tags)} cues")
            bad = 0
            for stab in (0.0, 0.5):
                tts = make_tts(stab)
                text, secs = await clip(tts, REFERENCE_TEXT)
                leaked = leaked_tags(ref_tags, text, plain)
                bad += len(leaked)
                print(f"  stability {stab}: {secs:.1f}s, read aloud: {leaked or 'none'}")
                await tts.aclose()
            return 1 if bad else 0

        tts = make_tts(stability)
        jobs = plan_jobs(tags, carriers)
        sem = asyncio.Semaphore(2)
        clips: dict[tuple, tuple[str, float]] = {}
        failures: list[str] = []

        async def one(job: Job) -> None:
            async with sem:
                try:
                    clips[(job.kind, job.tag, job.carrier_ix, job.repeat)] = await clip(tts, job.text)
                except Exception as exc:  # noqa: BLE001 - record and keep going
                    failures.append(f"{job.text[:40]!r}: {type(exc).__name__}")

        await asyncio.gather(*(one(j) for j in jobs))
        await tts.aclose()

    rows, raw = [], {}
    for tag in tags:
        trials = []
        for ci in range(len(carriers)):
            try:
                with_tag = clips[("tag", tag, ci, 0)]
                b0, b1 = clips[("base", "", ci, 0)], clips[("base", "", ci, 1)]
            except KeyError:
                continue
            trials.append(Trial(with_tag[0], with_tag[1], b0[0], b0[1], abs(b0[1] - b1[1])))
        if not trials:
            rows.append((tag, "error", "no clips"))
            continue
        v = verdict(tag, trials)
        delta = max(trials, key=lambda t: abs(t.seconds - t.base_seconds))
        rows.append((tag, v, f"Δ{delta.seconds - delta.base_seconds:+.2f}s"))
        raw[tag] = {"verdict": v, "trials": [asdict(t) for t in trials]}

    print(f"model={model} stability={stability} carriers={len(carriers)} syntheses={len(jobs)}")
    print(render_table(rows))
    counts = {v: sum(1 for _, x, _ in rows if x == v) for v in ("performed", "spoken-aloud", "no-audible-effect", "error")}
    print("\n" + "  ".join(f"{k}={n}" for k, n in counts.items()))
    if failures:
        print("failures:", "; ".join(failures[:5]))
    if args.json:
        Path(args.json).write_text(json.dumps(raw, indent=2), encoding="utf-8")
    return 1 if (failures or counts["error"]) else 0


# ------------------------------------------------------------------ CLI

def parse_args(argv):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--only", help='comma-separated tags, e.g. "whispers,building tension"')
    p.add_argument("--from-palette", action="store_true", help="verify the shipped palette instead of the candidates")
    p.add_argument("--carriers", type=int, default=len(CARRIERS), choices=range(1, len(CARRIERS) + 1))
    p.add_argument("--stability", type=float, default=None, help="override UG_HALLOWEEN_STABILITY")
    p.add_argument("--stability-check", action="store_true", help="read the reference passage at 0.0 and 0.5")
    p.add_argument("--max-syntheses", type=int, default=DEFAULT_BUDGET)
    p.add_argument("--dry-run", action="store_true", help="print the plan; no credentials, no network")
    p.add_argument("--json", help="write raw per-tag results to this path (keep it outside the repo)")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    tags = select_tags(args.only, args.from_palette)
    bad = [t for t in tags if not _VALID_TAG.fullmatch(t) or len(t) > 32]
    if bad:
        print(f"not a valid tag (one or two lowercase words, at most 32 characters): {bad}")
        return 2
    carriers = CARRIERS[: args.carriers]
    planned = 2 if args.stability_check else len(plan_jobs(tags, carriers))
    if planned > args.max_syntheses:
        print(f"{planned} syntheses planned, over the budget of {args.max_syntheses}; narrow with --only or --carriers")
        return 2
    if args.dry_run:
        print(f"{len(tags)} tags x {len(carriers)} carriers -> {planned} syntheses (budget {args.max_syntheses})")
        return 0
    from dotenv import load_dotenv
    load_dotenv(REPO_ROOT / ".env.local", override=False)
    return asyncio.run(_run_live(args, tags, carriers))


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `uv run --frozen pytest tests/test_expressive_bakeoff.py -q`
Expected: all pass. If `test_carriers_do_not_contain_any_candidate_word` fails, change the carrier wording (not the candidates).

- [ ] **Step 5: Smoke-run the live tool on two tags (≈ 6 syntheses)**

Run: `uv run --frozen python tools/expressive_bakeoff.py --only "whispers,building tension" --carriers 1`
Expected: a table with two rows and a `performed= spoken-aloud= no-audible-effect= error=` line. If a plugin API error appears, read `.venv/lib/python3.12/site-packages/livekit/plugins/elevenlabs/tts.py` and fix `synthesize_wav` / `make_tts` (the tts must get `http_session=`, outside a job context). Do not print or log any credential.

- [ ] **Step 6: Run the full bake-off and the stability check**

Run: `uv run --frozen python tools/expressive_bakeoff.py --json /tmp/bakeoff.json` (about 120 syntheses), then `uv run --frozen python tools/expressive_bakeoff.py --stability-check` (2 syntheses).
Keep the output. If a few tags error (network), re-run just those with `--only`.

- [ ] **Step 7: Write the contract**

Create `docs/discovery/expressive-tags-contract.md` from the real output, in this shape (no voice id, no keys, no account details):

````markdown
# Discovery: expressive audio tags for the Halloween voice (Plan 6)

_Verified <YYYY-MM-DD> against `<model>` at stability `<value>`, with the voice configured as `UG_HALLOWEEN_VOICE_ID`, through `livekit-plugins-elevenlabs` <version>, judged by Deepgram nova-3 transcript and audio duration (not by ear)._

## Method
<2-3 sentences: carriers, with/without tag, two plain renderings for the noise floor, the three verdicts, thresholds MIN_DELTA_S / NOISE_FACTOR.>

## Results
<the markdown table printed by the tool, one row per candidate, plus the counts line>

## Final palette
Every `performed` tag, in candidate order. A category with no performed tag is omitted. `tools/expressive_bakeoff.py --from-palette` re-verifies it, and `tests/test_expressive_palette.py` fails if `src/expressive_palette.py` drifts from this block.

```palette
breath: <tag> | <tag> | ...
volume: <tag> | ...
emotion: <tag> | ...
pacing: <tag> | ...
```

## Excluded
<each `spoken-aloud` or `no-audible-effect` tag with its verdict; say explicitly what happened to the six reference-example tags (`laughing`, `building tension`, `soft`, `dismissive`, `deep breaths`, `whisper`) and to the seven Plan 5 tags (`whispers`, `sighs`, `laughs`, `mischievously`, `nervously`, `exhales`, `inhales deeply`).>

## Stability (0.0 vs 0.5)
<the --stability-check output and a one-line recommendation for UG_HALLOWEEN_STABILITY>

## Limits
<verified for one voice/model/stability; "performed" is not "performed as intended"; run-to-run variation; a Professional Voice Clone loses its characteristics on v3 Conversational (ElevenLabs docs); this contract covers the model the app runs today (ElevenLabs now calls v3 Conversational the previous generation; changing the TTS model is out of scope); re-run the tool when the voice, model or plugin version changes>
````

Decision rules: the palette holds only `performed` tags (never lower the bar). If fewer than 10 tags are `performed`, or any of `whispers`, `sighs`, `laughs`, `exhales`, `mischievously` is `spoken-aloud`, finish the contract anyway and report `DONE_WITH_CONCERNS` with the table, so the controller can decide.

- [ ] **Step 8: Run the whole suite and commit**

Run: `uv run --frozen pytest -q --ignore=tests/test_integration_data_plane.py` (expect 891 + the new tests, 0 failed).

```bash
git add tools/__init__.py tools/expressive_bakeoff.py tests/test_expressive_bakeoff.py docs/discovery/expressive-tags-contract.md
git commit -F - <<'EOF'
feat(expressive): TTS->STT bake-off and the verified tag palette (#29)

tools/expressive_bakeoff.py synthesizes each candidate cue with and without the
tag through the production plugin path, transcribes with Deepgram, and classifies
it performed / spoken-aloud / no-audible-effect. The verified palette is recorded
in docs/discovery/expressive-tags-contract.md.

Refs #28 #29

Co-authored-by: Isaac <no-reply@databricks.com>
EOF
git status --short
```
If `git status` shows ` M uv.lock`, run `git restore -- uv.lock`.

---

### Task 2: Palette data, filter vocabulary and tolerant matching (issue #30)

**Files:**
- Create: `src/expressive_palette.py`, `tests/test_expressive_palette.py`
- Modify: `app/expressive.py` (the `SPOOKY_TAGS` block near line 26, `_encode_pieces`, `encode_tags`)
- Modify: `tests/test_expressive.py`

**Interfaces:**
- Consumes (T1): the `palette` block in `docs/discovery/expressive-tags-contract.md`.
- Produces (for T3, T4): `src.expressive_palette.PALETTE: dict[str, tuple[str, ...]]`, `GROUP_HINTS: dict[str, str]`, `OTHER: str` (`"other"`), `OTHER_HINT: str`, `flatten(palette=PALETTE) -> tuple[str, ...]`, `grouped(tags: Iterable[str]) -> list[tuple[str, tuple[str, ...]]]`; and `app.expressive.canonical_tag(raw: str) -> str` plus `SPOOKY_TAGS` (now the flattened palette).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_expressive_palette.py`:

```python
"""The expressive-cue palette (src/expressive_palette.py): data invariants, helpers, and parity with the
contract docs/discovery/expressive-tags-contract.md that verified it."""
import re
from pathlib import Path

from src.expressive_palette import GROUP_HINTS, OTHER, PALETTE, flatten, grouped

ROOT = Path(__file__).resolve().parent.parent
CATEGORIES = ("breath", "volume", "emotion", "pacing")


def test_categories_are_known_non_empty_and_in_display_order():
    assert PALETTE, "the palette is empty"
    names = tuple(PALETTE)
    assert set(names) <= set(CATEGORIES)
    assert names == tuple(c for c in CATEGORIES if c in PALETTE)
    assert all(PALETTE[c] for c in PALETTE)


def test_every_tag_is_a_short_lowercase_name_the_bracket_scan_can_carry():
    for tag in flatten():
        assert re.fullmatch(r"[a-z]+(?: [a-z]+)?", tag), tag   # one or two words, letters and single spaces only
        assert len(tag) <= 32, tag                              # MAX_TAG_LEN - 2


def test_no_tag_appears_twice():
    tags = flatten()
    assert len(tags) == len(set(tags))


def test_every_category_has_a_hint():
    assert set(GROUP_HINTS) >= set(PALETTE)
    assert all(GROUP_HINTS[c].strip() for c in PALETTE)


def test_flatten_keeps_palette_order():
    assert flatten() == tuple(t for members in PALETTE.values() for t in members)
    assert flatten({"a": ("x", "y"), "b": ("z",)}) == ("x", "y", "z")


def test_grouped_keeps_only_the_requested_tags_in_palette_order():
    some = (flatten()[-1], flatten()[0])                        # given out of order
    out = grouped(some)
    assert [t for _, members in out for t in members] == [t for t in flatten() if t in some]


def test_grouped_drops_empty_categories():
    first = next(iter(PALETTE))
    assert [c for c, _ in grouped((PALETTE[first][0],))] == [first]


def test_grouped_collects_tags_the_palette_does_not_know_last_and_sorted():
    out = grouped(("zzz", flatten()[0], "aaa"))
    assert out[-1] == (OTHER, ("aaa", "zzz"))
    assert out[0][1] == (flatten()[0],)


def test_grouped_of_nothing_is_empty():
    assert grouped(()) == []


def test_palette_matches_the_verified_contract():
    text = (ROOT / "docs" / "discovery" / "expressive-tags-contract.md").read_text(encoding="utf-8")
    block = re.search(r"```palette\n(.*?)```", text, re.S)
    assert block, "the contract has no ```palette block"
    expected = {}
    for line in block.group(1).strip().splitlines():
        category, tags = line.split(":", 1)
        expected[category.strip()] = tuple(t.strip() for t in tags.split("|") if t.strip())
    assert PALETTE == expected
```

In `tests/test_expressive.py`: add `import re` next to the other imports, then **replace** `test_spooky_vocabulary_is_the_spec_set` and **append** the new tests at the end of the file:

```python
def test_spooky_vocabulary_is_the_flattened_palette():
    from src.expressive_palette import PALETTE, flatten

    assert SPOOKY_TAGS == flatten(PALETTE)
    # crying, sound effects (gunshot, explosion) and accent tags are left out on purpose (§7.8)
    assert not {"crying", "gunshot", "explosion"} & set(SPOOKY_TAGS)
```

```python
# ------------------------------------------------------------------ Plan 6: the richer palette

MULTI_WORD = next((t for t in SPOOKY_TAGS if " " in t), None)


@pytest.mark.asyncio
@pytest.mark.parametrize("tag", SPOOKY_TAGS)
async def test_every_palette_tag_round_trips_whole_and_split_into_characters(tag):
    whole = await collect(encode_tags(all_tags), [f"a [{tag}] b"])
    assert await collect(decode_tags, [whole]) == f"a [{tag}] b"
    split = await collect(encode_tags(all_tags), list(f"a [{tag}] b"))   # one character per chunk
    assert await collect(decode_tags, [split]) == f"a [{tag}] b"


@pytest.mark.asyncio
async def test_case_and_spacing_from_the_model_are_forgiven():
    tag = SPOOKY_TAGS[0]
    mid = await collect(encode_tags(all_tags), [f"x [  {tag.upper()}  ] y"])
    assert has_pua(mid) and await collect(decode_tags, [mid]) == f"x [{tag}] y"


@pytest.mark.asyncio
@pytest.mark.skipif(MULTI_WORD is None, reason="the palette has no multi-word tag")
async def test_inner_spacing_of_a_multi_word_tag_is_collapsed():
    first, *rest = MULTI_WORD.split()
    messy = f"[{first.title()}   {'    '.join(rest)}]"
    mid = await collect(encode_tags(all_tags), [messy])
    assert await collect(decode_tags, [mid]) == f"[{MULTI_WORD}]"


@pytest.mark.asyncio
async def test_canonicalising_never_widens_what_can_be_spoken():
    for bad in ("[not-a-cue]", "[whisper-ish]", "[Explosion]", "[ ]"):
        mid = await collect(encode_tags(all_tags), [f"a {bad} b"])
        assert mid == "a  b", bad                  # dropped, and nothing decodes to it


@pytest.mark.asyncio
async def test_on_tag_receives_the_canonical_name():
    seen = []
    tag = SPOOKY_TAGS[0]
    await collect(encode_tags(all_tags, seen.append), [f"[{tag.title()}]"])
    assert seen == [tag]


@pytest.mark.asyncio
async def test_a_vocabulary_written_with_odd_case_or_spacing_still_matches():
    mid = await collect(encode_tags(lambda: frozenset({"Sinister  Laugh"})), ["[sinister laugh]"])
    assert has_pua(mid)


def test_the_studios_client_side_strip_removes_every_palette_tag():
    js = (Path(__file__).resolve().parent.parent / "app" / "web" / "public" / "studio.js").read_text(encoding="utf-8")
    found = re.search(r"const CUE_RE = /(.+)/g;", js)
    assert found, "CUE_RE not found in studio.js"
    cue = re.compile(found.group(1))                # plain enough to mean the same thing in Python
    for tag in SPOOKY_TAGS:
        assert cue.sub("", f"a [{tag}] b") == "a  b", tag
    assert cue.sub("", "see [a link](http://x) here") == "see [a link](http://x) here"
    assert cue.sub("", "plain text, no cues") == "plain text, no cues"
```

- [ ] **Step 2: Run the new tests and confirm they fail**

Run: `uv run --frozen pytest tests/test_expressive_palette.py tests/test_expressive.py -q`
Expected: FAIL / collection error (`ModuleNotFoundError: No module named 'src.expressive_palette'`).

- [ ] **Step 3: Create `src/expressive_palette.py`**

Generate the dict literal from the contract so nothing is retyped:

```bash
uv run --frozen python - <<'EOF'
import re
text = open("docs/discovery/expressive-tags-contract.md", encoding="utf-8").read()
block = re.search(r"```palette\n(.*?)```", text, re.S).group(1).strip()
for line in block.splitlines():
    cat, tags = line.split(":", 1)
    items = [t.strip() for t in tags.split("|") if t.strip()]
    print(f'    "{cat.strip()}": (' + ", ".join(f'"{t}"' for t in items) + ",),")
EOF
```

Create the module, pasting that output into `PALETTE` (wrap long tuples over several lines, ≤ 120 columns):

```python
"""The Halloween voice's expressive-cue palette: the ElevenLabs v3 audio tags that
docs/discovery/expressive-tags-contract.md shows the configured voice PERFORMS (never reads aloud), grouped by
purpose. Pure data and two helpers; standard library only.

`app.expressive.SPOOKY_TAGS` is this palette flattened (the only tags that may reach the TTS, G13), and
`src.agent_prompt` renders it grouped for the model. Change it only by re-running tools/expressive_bakeoff.py and
updating the contract; a test keeps the two equal.
"""
from __future__ import annotations

from collections.abc import Iterable

# category -> tags, in the order they are shown to the model. Lowercase, at most 32 characters, unique overall.
PALETTE: dict[str, tuple[str, ...]] = {
    # <paste the generated lines here: "breath": (...), "volume": (...), ...>
}

# category -> "Label (when to use it)", printed before the group's tags in the prompt.
GROUP_HINTS: dict[str, str] = {
    "breath": "Breath and sounds (reactions, laughter, breathing)",
    "volume": "Volume (how softly or heavily a line is spoken)",
    "emotion": "Attitude (the feeling behind a line)",
    "pacing": "Pacing and tension (timing and suspense)",
}

OTHER = "other"
OTHER_HINT = "Other cues"


def flatten(palette: dict[str, tuple[str, ...]] = PALETTE) -> tuple[str, ...]:
    """Every tag, category by category, in display order."""
    return tuple(tag for members in palette.values() for tag in members)


def grouped(tags: Iterable[str]) -> list[tuple[str, tuple[str, ...]]]:
    """[(category, tags)] for the given tags: palette order, empty categories left out, and any tag the palette
    does not know collected (sorted) under a final `OTHER` category."""
    wanted = set(tags)
    out = []
    for category, members in PALETTE.items():
        present = tuple(t for t in members if t in wanted)
        if present:
            out.append((category, present))
    unknown = tuple(sorted(wanted - set(flatten())))
    if unknown:
        out.append((OTHER, unknown))
    return out
```

(The comment inside `PALETTE` is replaced by the pasted entries; the finished file has no placeholder.)

- [ ] **Step 4: Change `app/expressive.py`**

Replace the `SPOOKY_TAGS` block (currently a 7-tag tuple) with:

```python
from src.expressive_palette import PALETTE, flatten

# The audio tags the Halloween voice performs: the verified palette (src/expressive_palette.py), flattened. Left
# out on purpose: crying (distressing), sound effects such as gunshot or explosion (startling, off-brand) and
# accent tags (risk of offensive impressions).
SPOOKY_TAGS: tuple[str, ...] = flatten(PALETTE)
```

(put the `from src...` import with the other imports at the top of the file, after `from collections.abc import ...`). Add, above `_placeholder`:

```python
def canonical_tag(raw: str) -> str:
    """The form a tag is matched in: lowercase, inner whitespace collapsed to single spaces, ends trimmed. The
    model may write `[Whispers]` or `[ building   tension ]`; both reach the voice as the canonical tag."""
    return " ".join(raw.split()).lower()
```

In `_encode_pieces`, replace the `elif` branch so the test and everything downstream use the canonical name:

```python
        # a group: a short allowed name is a tag; anything else (an unknown tag, a direction) is dropped
        elif len(name := canonical_tag(text)) <= MAX_TAG_LEN - 2 and name in allowed:
            out.append(_placeholder(name))
            if on_tag is not None:
                try:
                    on_tag(name)
                except Exception:  # a counter must never be able to silence the reply
                    logger.warning("on_tag callback failed for %r", name, exc_info=True)
```

In `encode_tags.encode`, read the vocabulary in canonical form:

```python
        allowed = frozenset(canonical_tag(t) for t in vocabulary())
```

Update the module docstring sentence "The vocabulary is the only thing that can put bracketed text on the air" to add: "(matched case- and spacing-insensitively; only members ever reach the voice)".

- [ ] **Step 5: Run the new tests, then the whole suite**

Run: `uv run --frozen pytest tests/test_expressive_palette.py tests/test_expressive.py -q` → pass.
Run: `uv run --frozen pytest -q --ignore=tests/test_integration_data_plane.py` → every test green. Existing tests that spell `[whispers]`, `[sighs]`, `[laughs]` or `[inhales deeply]` need those tags in the palette; if the bake-off removed one, replace it in the test with another palette tag of the same shape (single-word or multi-word). Do not loosen any assertion.

- [ ] **Step 6: Commit**

```bash
git add src/expressive_palette.py app/expressive.py tests/test_expressive_palette.py tests/test_expressive.py
git commit -F - <<'EOF'
feat(expressive): single-sourced tag palette and tolerant tag matching (#30)

SPOOKY_TAGS is now the flattened, verified palette (src/expressive_palette.py).
encode_tags canonicalises case and spacing before the allowlist test, so a
near-miss such as [Whispers] is performed instead of dropped; only allowlist
members can ever reach the voice (G13 unchanged). Tests cover every tag whole
and split per character, the client-side strip, and parity with the contract.

Refs #28 #30

Co-authored-by: Isaac <no-reply@databricks.com>
EOF
git status --short
```
If ` M uv.lock` appears, `git restore -- uv.lock`.

---

### Task 3: Monster persona, grouped cue rules and worked example (issue #31)

**Files:**
- Modify: `src/agent_prompt.py` (the persona near line 36, `_cue_rules` near line 91)
- Modify: `tests/test_agent_prompt.py`

**Interfaces:**
- Consumes (T2): `from src.expressive_palette import GROUP_HINTS, OTHER, OTHER_HINT, grouped` and `PALETTE`, `flatten` in tests.
- Produces (for T4): `build_instructions(..., expressive_tags=...)` unchanged in signature; its cue section now contains, in order: the header `--- Expressive cues ---`, one line per group `"{GROUP_HINTS[category]}: [tag] [tag] ..."` (or `"Other cues: ..."`), the line `Rules for cues:` and its bullets, then (only when the voice has a tag in each of `volume`, `emotion` and `pacing`) two lines beginning `Example of the delivery` and `Example of a story beat`. `ON_NOTE`, `OFF_NOTE`, `ANNOUNCE_*`, `BRIDGE_ON`, `VOICE_REQUESTS` are **unchanged**: the steady instructions already carry the cue rules, and both notes end "Follow the rules above."

- [ ] **Step 1: Write the failing tests**

In `tests/test_agent_prompt.py`, add `import re` and `from src.expressive_palette import GROUP_HINTS, PALETTE, flatten` at the top, **replace** `test_cue_rules_only_present_with_tags` with the version below, and append the rest:

```python
_FULL = flatten()


def _cues(tags=_FULL):
    return build_instructions("s", _D_NEUTRAL, persona=HALLOWEEN_PERSONA, expressive_tags=tags)


def test_cue_rules_only_present_with_tags():
    with_tags = _cues(("whispers",))
    without = build_instructions("s", _D_NEUTRAL, persona=HALLOWEEN_PERSONA, expressive_tags=())
    assert "[whispers]" in with_tags and "[whispers]" not in without
    assert "Expressive cues" in with_tags and "Expressive cues" not in without   # the cue rules, not just the tags


def test_cue_rules_do_not_cap_a_reply_at_two_cues():
    assert "at most two" not in _cues().lower()


def test_the_palette_is_shown_grouped_and_spelled_exactly_as_the_vocabulary_spells_it():
    out = _cues()
    for category, members in PALETTE.items():
        line = next(l for l in out.splitlines() if l.startswith(GROUP_HINTS[category]))
        assert line == f"{GROUP_HINTS[category]}: " + " ".join(f"[{t}]" for t in members)


def test_only_the_vocabulary_is_listed():
    one, *others = flatten()
    out = _cues((one,))
    assert f"[{one}]" in out
    assert all(f"[{t}]" not in out for t in others)


def test_tags_the_palette_does_not_know_are_listed_as_other_cues():
    assert "Other cues: [zzzz cue]" in _cues(("zzzz cue",))


def test_rules_keep_facts_and_brackets_clean():
    out = _cues()
    assert "Never put a cue inside a number, date, name, or any other fact" in out
    assert "no other bracketed text" in out.lower()
    assert "never two in a row" in out


def test_the_worked_examples_use_only_cues_the_voice_has():
    vocab = set(flatten())
    out = _cues()
    lines = [l for l in out.splitlines() if l.startswith(("Example of the delivery", "Example of a story beat"))]
    assert len(lines) == 2
    used = {c for l in lines for c in re.findall(r"\[([^\]]+)\]", l)}
    assert used and used <= vocab


def test_no_example_when_the_voice_lacks_a_whole_category():
    only_volume = tuple(PALETTE.get("volume", ())) or (flatten()[0],)
    assert "Example of the delivery" not in _cues(only_volume)


def test_standard_and_fallback_prompts_carry_no_cue_section_and_no_brackets():
    for persona in (None, HALLOWEEN_PERSONA):
        out = build_instructions("Support.", _D_NEUTRAL, persona=persona, expressive_tags=())
        assert "Expressive cues" not in out and "[" not in out


def test_the_halloween_persona_is_a_monster_but_keeps_every_safety_line():
    p = HALLOWEEN_PERSONA
    assert "monster" in p.lower()
    assert "Never be threatening, cruel, gory, or genuinely frightening" in p
    assert "exactly as the tools return it" in p
    assert "drop the act" in p
    assert 'say "As you wish…"' in p


def test_the_cue_section_stays_within_a_prompt_budget():
    section = _cues().split("--- Expressive cues ---")[1].split("Governance (non-negotiable)")[0]
    assert len(section) <= 4000      # about 1,000 tokens at the very most; T4 records the real figure


def test_the_steady_instructions_still_reach_a_same_turn_patch_with_the_cue_rules():
    steady = _cues()
    patched = f"{steady}\n\n{ON_NOTE}"          # what VoiceModeController patches into the switching reply
    assert "Expressive cues" in patched and patched.endswith("Follow the rules above.")
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `uv run --frozen pytest tests/test_agent_prompt.py -q`
Expected: several FAIL (the old prompt still says "at most two", has no groups or examples, persona has no "monster").

- [ ] **Step 3: Implement**

In `src/agent_prompt.py` add `from src.expressive_palette import GROUP_HINTS, OTHER, OTHER_HINT, grouped` to the imports, replace `HALLOWEEN_PERSONA`, and replace `_cue_rules` (keep the comment block above the persona):

```python
HALLOWEEN_PERSONA = """
--- Halloween persona ---
You are a Halloween monster who tells it like a campfire story: slow, theatrical, a little mischievous. You
savour every pause and let the tension build before each reveal, with dramatic pauses (an ellipsis works well).
- Never be threatening, cruel, gory, or genuinely frightening. Keep it fun.
- The act colors only your delivery. State every fact, number, date, name, and order detail plainly and
  exactly as the tools return it.
- If the caller sounds uncomfortable, drop the act and offer the normal voice.
- If the caller asks for the normal voice, say "As you wish…" (the system switches the voice back).
""".strip()
```

```python
_CUE_INTRO = (
    "--- Expressive cues ---\n"
    "Your voice performs the cues below when you write them in square brackets. They are how you act: use them "
    "the way a storyteller uses breath, hush, laughter and timing."
)

_CUE_RULES = (
    "Rules for cues:\n"
    "- Write each cue EXACTLY as listed (lowercase, square brackets included) and use no other bracketed text. "
    "Never put words you want spoken inside brackets.\n"
    "- Open your reply with a cue, then start each new beat with a cue: at most one per sentence, never two in a "
    "row, and vary them. A short factual answer needs just one or two.\n"
    "- A cue colors only the next few words, so cue each new beat again; nothing carries over.\n"
    "- Put each cue right before the words it colors.\n"
    "- Never put a cue inside a number, date, name, or any other fact. Say the fact plainly, then cue the next line.\n"
    "- To build suspense, use short sentences, a cue, a pause (…), then the reveal."
)

# The worked examples teach only cues the voice really has: per slot, the first cue from a preference list that the
# voice performs, else the first cue of that group; if any slot is empty there is no example.
_EXAMPLE_SLOTS = (
    ("volume", ("whispers", "whisper", "soft")),
    ("emotion", ("mischievously", "menacing", "sinister", "dismissive")),
    ("pacing", ("building tension", "pause", "slowly")),
)


def _examples(available: frozenset[str], groups: list[tuple[str, tuple[str, ...]]]) -> list[str]:
    by_group = dict(groups)
    picks = []
    for category, preferred in _EXAMPLE_SLOTS:
        tag = next((t for t in preferred if t in available), None) or next(iter(by_group.get(category, ())), None)
        if tag is None:
            return []
        picks.append(tag)
    soft, attitude, tension = picks
    return [
        "Example of the delivery (do not copy the words): "
        f"[{soft}] Your order shipped on Tuesday. [{attitude}] It should reach you by Friday… "
        f"[{tension}] and not a moment sooner.",
        "Example of a story beat (do not copy the words): "
        f"[{tension}] The door creaked open… [{soft}] and nobody was there. [{attitude}] Nobody ever is.",
    ]


def _cue_rules(tags: tuple[str, ...]) -> str:
    """The cue section for a voice that performs `tags`: the palette grouped by purpose, the rules, and worked
    examples built only from cues the voice really has. `tags` come from the active voice profile."""
    groups = grouped(tags)
    lines = [_CUE_INTRO]
    for category, members in groups:
        hint = OTHER_HINT if category == OTHER else GROUP_HINTS[category]
        lines.append(f"{hint}: " + " ".join(f"[{t}]" for t in members))
    lines.append(_CUE_RULES)
    lines.extend(_examples(frozenset(tags), groups))
    return "\n".join(lines)
```

Leave `build_instructions` as is (it already calls `_cue_rules(tags)` only when `tags` is non-empty).

- [ ] **Step 4: Run the prompt tests, then the whole suite**

Run: `uv run --frozen pytest tests/test_agent_prompt.py tests/test_voice_governance.py -q` → pass.
Run: `uv run --frozen pytest -q --ignore=tests/test_integration_data_plane.py` → all green.

- [ ] **Step 5: Record the size and commit**

Measure the characters the cue section adds (`len(build_instructions(..., expressive_tags=flatten())) - len(build_instructions(..., expressive_tags=()))`) and put the figure (and `≈ chars/4` tokens) in the commit body.

```bash
git add src/agent_prompt.py tests/test_agent_prompt.py
git commit -F - <<'EOF'
feat(prompt): monster persona and grouped expressive-cue rules (#31)

Replaces the "at most two cues" cap with a bounded density guide, the palette
grouped by purpose, and two worked examples built only from cues the voice has.
The persona becomes a campfire-storyteller monster and keeps every safety line.
Standard, fallback and OpenAI prompts are unchanged (no tags, no cue section).
Cue section adds <N> characters (~<N/4> tokens) in Halloween mode.

Refs #28 #31

Co-authored-by: Isaac <no-reply@databricks.com>
EOF
git status --short
```
If ` M uv.lock` appears, `git restore -- uv.lock`. (Fill in `<N>` from your measurement.)

---

### Task 4: Live LLM compliance harness and prompt tuning (issue #32)

**Files:**
- Create: `tools/expressive_llm_check.py`, `tests/test_expressive_tools.py`
- Modify: `src/agent_prompt.py` (tuning only: persona wording, `_CUE_RULES`, `_EXAMPLE_SLOTS`, examples), `tests/test_agent_prompt.py` (only to follow a wording change; assert structure, not prose)
- Modify: `docs/discovery/expressive-tags-contract.md` (append `## LLM compliance results`)

**Interfaces:**
- Consumes (T2, T3): `SPOOKY_TAGS`, `canonical_tag`, `strip_tags`, `build_instructions`, `HALLOWEEN_PERSONA`, `route_for`.
- Produces (for T5): `uv run --frozen python tools/expressive_llm_check.py` exits 0 only when every tier model meets the targets; `--dry-run` prints the call count without credentials.

- [ ] **Step 1: Write the failing tests**

`tests/test_expressive_tools.py`:

```python
"""Offline tests for tools/expressive_llm_check.py: the pure metrics and target checks (no network)."""
import pytest

from tools import expressive_llm_check as chk

VOCAB = frozenset({"whispers", "building tension", "sighs"})
ORDER = chk.SCENARIOS[2]            # the "order" scenario, with facts


def test_measure_counts_cues_words_and_distinct_cues():
    m = chk.measure("[whispers] The door opened. [building tension] Nobody was there.", VOCAB)
    assert (m.cues, m.words) == (2, 7)
    assert m.distinct == {"whispers", "building tension"}
    assert m.per_100 == pytest.approx(100 * 2 / 7)


def test_measure_separates_exact_spelling_from_canonicalised_spelling():
    m = chk.measure("[Whispers] a [ building   tension ] b [sighs] c", VOCAB)
    assert (m.cues, m.exact, m.canonical) == (3, 1, 3)


def test_measure_flags_a_cue_that_splits_a_fact():
    m = chk.measure("Order 48[sighs]213 shipped on October 7.", VOCAB, ("48213", "October 7"))
    assert m.cues_inside_facts == 1 and m.facts_verbatim


def test_measure_facts_verbatim_is_false_when_a_fact_is_paraphrased():
    m = chk.measure("It shipped on the seventh.", VOCAB, ("October 7",))
    assert not m.facts_verbatim and m.cues_inside_facts == 0


def test_measure_counts_stage_directions():
    m = chk.measure("[whispers menacingly into the microphone] boo [whispers] hi", VOCAB)
    assert m.directions == 1 and m.canonical == 1


def test_measure_counts_stacked_cues():
    assert chk.measure("[whispers] [sighs] hi", VOCAB).stacked == 1
    assert chk.measure("[whispers] hi [sighs] there", VOCAB).stacked == 0


def test_plain_text_has_no_cues():
    m = chk.measure("Just a plain sentence here.", VOCAB)
    assert (m.cues, m.per_100, m.distinct) == (0, 0.0, frozenset())


def _row(scenario, reply, facts=()):
    return scenario, chk.measure(reply, VOCAB, facts)


def test_evaluate_passes_a_rich_compliant_suite():
    story = chk.SCENARIOS[0]
    rows = [_row(story, "[whispers] One. [building tension] Two. [sighs] Three. [whispers] Four. [sighs] Five."),
            _row(ORDER, "[whispers] Order 48213 shipped on October 7.", ORDER.facts[:2])]
    assert chk.evaluate(rows, chk.Targets(distinct_cues=3)) == []


def test_evaluate_reports_every_kind_of_miss():
    story = chk.SCENARIOS[0]
    rows = [_row(story, "A story with no cues at all, just words going on and on."),
            _row(ORDER, "Order 48[sighs]213 shipped. [explodes] [whispers]", ("48213",))]
    problems = " | ".join(chk.evaluate(rows, chk.Targets(distinct_cues=3)))
    for needle in ("cues/100", "distinct", "inside a fact", "compliance", "stacked"):
        assert needle in problems, problems


def test_scenarios_are_well_formed():
    assert len({s.key for s in chk.SCENARIOS}) == len(chk.SCENARIOS)
    assert {s.kind for s in chk.SCENARIOS} == {"story", "factual", "short"}
    for s in chk.SCENARIOS:
        assert all(f in (s.tool_result or "") for f in s.facts), s.key     # facts come from the tool result


def test_dry_run_reports_calls_and_needs_no_credentials(capsys, monkeypatch):
    for name in ("DATABRICKS_HOST", "DATABRICKS_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    assert chk.main(["--dry-run", "--samples", "2"]) == 0
    assert "calls" in capsys.readouterr().out
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `uv run --frozen pytest tests/test_expressive_tools.py -q`
Expected: FAIL (`ModuleNotFoundError` for `tools.expressive_llm_check`).

- [ ] **Step 3: Write the harness**

`tools/expressive_llm_check.py`:

```python
"""Live check: does the Halloween prompt make the REAL tier models write rich, compliant cues?

Builds the exact Halloween instructions the agent sends (persona + cue rules + governance, via
`build_instructions`), runs scripted caller utterances against each tier's model through the Unity Gateway
Responses API (the endpoint the agent uses; Databricks-served models only), and reports per model: cues per 100 words, distinct cues, allowlist
compliance (before and after case/spacing are forgiven), stage directions, stacked cues, facts kept verbatim, cues
that split a fact, and the prompt's token overhead. Exit code 1 if any model misses a target.

Credentials come from the gitignored .env.local and are never printed.

    uv run --frozen python tools/expressive_llm_check.py --dry-run
    uv run --frozen python tools/expressive_llm_check.py                 # 3 tiers x 7 utterances x 3 samples
    uv run --frozen python tools/expressive_llm_check.py --tiers Standard --samples 1
"""
from __future__ import annotations

import argparse
import os
import re
import statistics
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.expressive import SPOOKY_TAGS, canonical_tag, strip_tags  # noqa: E402
from src.agent_prompt import HALLOWEEN_PERSONA, build_instructions  # noqa: E402
from src.policy.routing import route_for  # noqa: E402

TIERS = ("Standard", "Premium", "VIP")
DEFAULT_MAX_CALLS = 100
MAX_TAG_NAME = 32
DATASET_PROMPT = ("You are the voice assistant for Northwind Outfitters, an outdoor-gear retailer. Answer only "
                  "from your tools. Keep replies to short, friendly spoken sentences.")

_CUE = re.compile(r"\[([^\[\]\n]{0,118})\](?!\()")


@dataclass(frozen=True)
class Scenario:
    key: str
    kind: str                     # "story" | "factual" | "short"
    user: str
    tool_result: str | None = None
    facts: tuple[str, ...] = ()


SCENARIOS: tuple[Scenario, ...] = (
    Scenario("story", "story", "Tell me a scary story."),
    Scenario("lighthouse", "story", "Tell me a short spooky story about a haunted lighthouse."),
    Scenario("order", "factual", "Where is my order?",
             "Order 48213 shipped on Tuesday, October 7 and arrives Friday, October 10. The total was $84.50.",
             ("48213", "October 7", "October 10", "84.50")),
    Scenario("returns", "factual", "What is your return policy?",
             "Returns are accepted within 30 days with a receipt. Refunds take 5 to 7 business days.",
             ("30 days", "5 to 7")),
    Scenario("hours", "factual", "Is the store open on Sundays?",
             "The store is open on Sundays from 10 AM to 4 PM.", ("10 AM", "4 PM")),
    Scenario("unknown", "short", "What is the phone number for the moon base?", "No results."),
    Scenario("greeting", "short", "Hi there!"),
)


@dataclass(frozen=True)
class ReplyMetrics:
    words: int
    cues: int
    distinct: frozenset[str]
    exact: int                    # cues already spelled exactly as the vocabulary spells them
    canonical: int                # cues that match once case and spacing are forgiven (>= exact)
    directions: int               # bracketed stage directions: too long or too wordy to be a cue
    facts_verbatim: bool
    cues_inside_facts: int        # facts that only survive once the cues are removed: a cue split them
    stacked: int                  # a cue immediately followed by another

    @property
    def per_100(self) -> float:
        return 100.0 * self.cues / self.words if self.words else 0.0


def measure(reply: str, vocabulary: frozenset[str], facts: tuple[str, ...] = ()) -> ReplyMetrics:
    raw = _CUE.findall(reply)
    names = [canonical_tag(c) for c in raw]
    plain, low_reply = strip_tags(reply).lower(), reply.lower()
    return ReplyMetrics(
        words=len(strip_tags(reply).split()),
        cues=len(raw),
        distinct=frozenset(n for n in names if n in vocabulary),
        exact=sum(1 for c in raw if c in vocabulary),
        canonical=sum(1 for n in names if n in vocabulary),
        directions=sum(1 for n in names if len(n) > MAX_TAG_NAME or len(n.split()) > 2),
        facts_verbatim=all(f.lower() in plain for f in facts),
        cues_inside_facts=sum(1 for f in facts if f.lower() in plain and f.lower() not in low_reply),
        stacked=len(re.findall(r"\]\s*\[", reply)),
    )


@dataclass(frozen=True)
class Targets:
    story_cues_per_100: float = 5.0     # median over the story replies
    distinct_cues: int = 6              # across the whole suite for one model
    factual_min_cues: int = 1           # every factual reply
    compliance: float = 0.95            # share of cues that are vocabulary members once canonicalised


def evaluate(rows: list[tuple[Scenario, ReplyMetrics]], targets: Targets = Targets()) -> list[str]:
    """Why the model misses the targets; an empty list means it passes."""
    problems: list[str] = []
    story = [m.per_100 for s, m in rows if s.kind == "story"]
    if story and statistics.median(story) < targets.story_cues_per_100:
        problems.append(f"story cues/100 words median {statistics.median(story):.1f} < {targets.story_cues_per_100}")
    distinct = set().union(*(m.distinct for _, m in rows)) if rows else set()
    if len(distinct) < targets.distinct_cues:
        problems.append(f"only {len(distinct)} distinct cues (< {targets.distinct_cues})")
    bare = [s.key for s, m in rows if s.kind == "factual" and m.cues < targets.factual_min_cues]
    if bare:
        problems.append(f"factual replies without a cue: {sorted(set(bare))}")
    total = sum(m.cues for _, m in rows)
    if total and sum(m.canonical for _, m in rows) / total < targets.compliance:
        problems.append(f"compliance {sum(m.canonical for _, m in rows) / total:.0%} < {targets.compliance:.0%}")
    for label, count in (("cues inside a fact", sum(m.cues_inside_facts for _, m in rows)),
                         ("stage directions", sum(m.directions for _, m in rows)),
                         ("stacked cues", sum(m.stacked for _, m in rows)),
                         ("replies missing a fact", sum(1 for _, m in rows if not m.facts_verbatim))):
        if count:
            problems.append(f"{count} {label}")
    return problems


# ------------------------------------------------------------------ live part

def instructions_for(tier: str, *, with_cues: bool = True) -> tuple[str, str]:
    decision = route_for(tier)
    tags = tuple(sorted(SPOOKY_TAGS)) if with_cues else ()
    return decision.model, build_instructions(DATASET_PROMPT, decision.directives, None, persona=HALLOWEEN_PERSONA,
                                              expressive_tags=tags, voice_requests=True)


def ask(client, model: str, instructions: str, scenario: Scenario) -> tuple[str, int]:
    user = scenario.user
    if scenario.tool_result:
        user += f"\n\n(For your reference, the lookup tool returned: {scenario.tool_result})"
    resp = client.responses.create(model=model, instructions=instructions, input=user,
                                   reasoning={"effort": "low"}, store=False)
    return resp.output_text or "", resp.usage.input_tokens


def parse_args(argv):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--tiers", default=",".join(TIERS))
    p.add_argument("--samples", type=int, default=3)
    p.add_argument("--max-calls", type=int, default=DEFAULT_MAX_CALLS)
    p.add_argument("--dry-run", action="store_true", help="print the call count; no credentials, no network")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    tiers = [t.strip() for t in args.tiers.split(",") if t.strip()]
    unknown = [t for t in tiers if t not in TIERS]
    if unknown:
        print(f"unknown tier(s): {unknown}; choose from {list(TIERS)}")
        return 2
    calls = len(tiers) * (len(SCENARIOS) * args.samples + 2)      # +2 per tier: the prompt-overhead probe
    if calls > args.max_calls:
        print(f"{calls} calls planned, over the budget of {args.max_calls}; lower --samples or --tiers")
        return 2
    if args.dry_run:
        print(f"{len(tiers)} tiers x {len(SCENARIOS)} utterances x {args.samples} samples (+ overhead probe) -> {calls} calls")
        return 0

    from dotenv import load_dotenv
    from openai import OpenAI       # only the client library: every request below goes to the Databricks Unity Gateway

    load_dotenv(REPO_ROOT / ".env.local", override=False)
    host, token = os.environ.get("DATABRICKS_HOST", "").strip().rstrip("/"), os.environ.get("DATABRICKS_TOKEN", "").strip()
    if not host or not token:
        print("Missing in .env.local: DATABRICKS_HOST and/or DATABRICKS_TOKEN (names only)")
        return 2
    if not host.startswith("http"):
        host = "https://" + host
    client = OpenAI(base_url=f"{host}/ai-gateway/openai/v1", api_key=token, timeout=120.0)

    vocabulary, failed = frozenset(SPOOKY_TAGS), False
    greeting = SCENARIOS[-1]
    for tier in tiers:
        model, instructions = instructions_for(tier)
        _, plain_instructions = instructions_for(tier, with_cues=False)
        overhead = ask(client, model, instructions, greeting)[1] - ask(client, model, plain_instructions, greeting)[1]
        rows, first_story = [], ""
        for scenario in SCENARIOS:
            for _ in range(args.samples):
                reply, _tokens = ask(client, model, instructions, scenario)
                rows.append((scenario, measure(reply, vocabulary, scenario.facts)))
                if scenario.key == "story" and not first_story:
                    first_story = reply
        problems = evaluate(rows)
        failed |= bool(problems)
        story = [m.per_100 for s, m in rows if s.kind == "story"]
        factual = [m.per_100 for s, m in rows if s.kind == "factual"]
        total = sum(m.cues for _, m in rows) or 1
        print(f"\n### {tier} — `{model}`  (cue section ≈ +{overhead} input tokens)")
        print(f"- story cues/100 words: median {statistics.median(story):.1f}   factual: median {statistics.median(factual):.1f}")
        print(f"- distinct cues: {len(set().union(*(m.distinct for _, m in rows)))}   "
              f"compliance: {sum(m.canonical for _, m in rows) / total:.0%} "
              f"(exact spelling {sum(m.exact for _, m in rows) / total:.0%})")
        print(f"- stage directions: {sum(m.directions for _, m in rows)}   stacked: {sum(m.stacked for _, m in rows)}   "
              f"cues inside facts: {sum(m.cues_inside_facts for _, m in rows)}   "
              f"replies missing a fact: {sum(1 for _, m in rows if not m.facts_verbatim)}")
        print(f"- verdict: {'PASS' if not problems else 'FAIL — ' + '; '.join(problems)}")
        print(f"- sample story: {first_story[:400]!r}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run the offline tests**

Run: `uv run --frozen pytest tests/test_expressive_tools.py -q` → pass.

- [ ] **Step 5: Baseline run against the real models**

Run: `uv run --frozen python tools/expressive_llm_check.py` (about 69 calls). Save the output. If it cannot authenticate (HTTP 401/403 from the gateway), stop and report `BLOCKED` naming only the status code: the Databricks token in `.env.local` needs refreshing by the owner.

- [ ] **Step 6: Tune the prompt until every tier passes**

If a tier FAILs, change only `src/agent_prompt.py` prompt text (persona wording, `_CUE_RULES`, `_CUE_INTRO`, `_EXAMPLE_SLOTS` preferences, the two examples) and re-run the tool. Typical levers: a stronger instruction to open with a cue, a longer story example, a sentence saying a story needs a cue at nearly every beat, moving the rules above the palette. Keep the safety lines and G11 (governance last). After every wording change run `uv run --frozen pytest tests/test_agent_prompt.py tests/test_voice_governance.py -q`; if a test pins prose, loosen it to assert structure (a header, a rule's presence), never to drop a safety or governance assertion. Stop after at most 4 tuning runs; if a target still fails on the weakest tier, report `DONE_WITH_CONCERNS` with the table and your diagnosis instead of lowering a target.

- [ ] **Step 7: Record the results**

Append to `docs/discovery/expressive-tags-contract.md`:

````markdown
## LLM compliance results

_Run <YYYY-MM-DD> with `tools/expressive_llm_check.py`: 7 scripted utterances x 3 samples on each tier's model (Standard / Premium / VIP), `reasoning.effort=low`, the exact Halloween instructions the agent sends._

<for each tier: model name, the metrics lines printed by the tool, and the final verdict; then a short "Before -> after" paragraph naming what the tuning changed>

Targets (initial; changed only with evidence, recorded here): story median >= 5 cues per 100 words; >= 6 distinct cues per model; >= 1 cue in every factual reply; >= 95% of cues are vocabulary members once case/spacing are forgiven; 0 cues inside facts, stage directions, stacked cues, or missing facts.
````

- [ ] **Step 8: Run the whole suite and commit**

Run: `uv run --frozen pytest -q --ignore=tests/test_integration_data_plane.py` → all green.

```bash
git add tools/expressive_llm_check.py tests/test_expressive_tools.py src/agent_prompt.py tests/test_agent_prompt.py docs/discovery/expressive-tags-contract.md
git commit -F - <<'EOF'
feat(expressive): live LLM compliance harness and prompt tuning (#32)

tools/expressive_llm_check.py runs scripted utterances against the Standard,
Premium and VIP models with the exact Halloween instructions and measures cues
per 100 words, distinct cues, allowlist compliance, stage directions, stacked
cues, facts kept verbatim and cues that split a fact. Prompt wording tuned until
every tier meets the targets; results recorded in the contract.

Refs #28 #32

Co-authored-by: Isaac <no-reply@databricks.com>
EOF
git status --short
```
If ` M uv.lock` appears, `git restore -- uv.lock`. (Only `git add` the test and prompt files you actually changed.)

---

### Task 5: Docs, listening recipe, final gate and PR (issue #33)

**Files:**
- Modify: `docs/gotchas.md`, `README.md`, `docs/discovery/expressive-tags-contract.md`

**Interfaces:**
- Consumes (T1-T4): the contract's results and limits, the two tools' flags.

- [ ] **Step 1: Gotchas**

Append a section to `docs/gotchas.md`, in the file's own style (trap → consequence → what to do), covering at least:

```markdown
## Plan 6 — expressive Halloween tags (2026-10-04)

- **The allowlist stays closed on purpose.** ElevenLabs v3 accepts free-form `[descriptive]` tags, but nothing guarantees a given tag is performed instead of read aloud. Only tags that `tools/expressive_bakeoff.py` proved `performed` for the configured voice/model may be in `src/expressive_palette.py`; anything else is dropped from audio and transcript (G13). Re-run the tool when the voice, model or plugin version changes.
- **"Performed" is judged by transcript and duration, not by ear.** <state the tool's limits as found in T1: noise floor, tags that only change timbre, etc.>
- **The ElevenLabs API key may be scoped to speech.** Account and model endpoints (`/v1/user/subscription`, `/v1/models`) return 401 `missing_permissions` while synthesis works, so quota cannot be read and tools must never call them.
- **A `.env.local` makes the live Lakebase test run.** `tests/test_integration_data_plane.py` is gated only on `LAKEBASE_ENDPOINT`, which `app/agent.py` and `app/web_server.py` load from `.env.local` at import; the test then generates two real datasets and never cleans them up. With a `.env.local` present, run `uv run --frozen pytest -q --ignore=tests/test_integration_data_plane.py`.
- **Plain `uv run` re-locks `uv.lock` to the internal proxy, and so does the commit hook.** `--no-sync` does not stop it; `--frozen` does. After each `git commit`, `git restore -- uv.lock` if the hook touched it.
- **The weakest tier model follows a long prompt least reliably.** <state T4's findings: which model, which lever fixed it>
```
Fill every `<...>` from the real T1/T4 results; delete none of the known bullets.

- [ ] **Step 2: README**

In `README.md`, (a) add a `tools/` row to the "What's inside" table (`Manual verification tools: the TTS→STT tag bake-off and the live LLM cue check`), (b) extend the existing Halloween-mode section with the expressive palette (grouped, as in `src/expressive_palette.py`) and a short "Verifying the voice" subsection:

```markdown
### Verifying the voice

```bash
uv run --frozen python tools/expressive_bakeoff.py --from-palette     # every shipped tag is performed, none read aloud
uv run --frozen python tools/expressive_bakeoff.py --stability-check  # 0.0 vs 0.5 on the reference passage
uv run --frozen python tools/expressive_llm_check.py                   # cue richness on the three tier models
```

Then listen once: run the studio locally (`PORT=8080 uv run --frozen python app/web_server.py` and
`uv run --frozen python app/agent.py dev` in a second terminal), open http://localhost:8080, start a call, say
"switch to the spooky voice", then "tell me a scary story". You should hear breaths, whispers and pauses, and
no tag read out loud. The transcript shows no brackets.
```
(Use real nested fences in the README; the plan shows them collapsed.) Keep the README's existing tone and the rule that nothing picks a Databricks profile for you.

- [ ] **Step 3: Re-verify on the final branch**

Run the suite: `uv run --frozen pytest -q --ignore=tests/test_integration_data_plane.py` → all green; record the count.
Re-verify the shipped palette: `uv run --frozen python tools/expressive_bakeoff.py --from-palette` → zero `spoken-aloud`.
Re-run the LLM check: `uv run --frozen python tools/expressive_llm_check.py` → all tiers PASS.
If a result differs from the contract, update the contract (never the other way round) and say why.

- [ ] **Step 4: Identifier and secret sweep on the diff**

```bash
uv run --frozen python - <<'PYEOF'
# Build the "must never be public" needles from this machine's own gitignored .env.local at run time, so no
# identifier is ever written into a tracked file (including this plan). Only file:line positions are printed.
import re, subprocess
from dotenv import dotenv_values

env = {k: (v or "").strip() for k, v in dotenv_values(".env.local").items()}
needles = set()

def add(value, minimum=4):
    if value and len(value) >= minimum:
        needles.add(value.lower())

host = re.sub(r"^https?://", "", env.get("DATABRICKS_HOST", ""))
add(host.split(".")[0])                                            # workspace name
add((re.search(r"projects/([^/]+)", env.get("LAKEBASE_ENDPOINT", "")) or [None, ""])[1])   # Lakebase project
for key in ("DATABRICKS_TRACE_CATALOG", "DATABRICKS_TRACE_SCHEMA", "MLFLOW_TRACING_SQL_WAREHOUSE_ID",
            "UG_HALLOWEEN_VOICE_ID"):
    add(env.get(key, ""))
add((re.search(r"[\w.+-]+@[\w.-]+", env.get("MLFLOW_EXPERIMENT_NAME", "")) or [""])[0])   # the owner's email
add(re.sub(r"^wss?://", "", env.get("LIVEKIT_URL", "")).split(".")[0])                   # LiveKit project
for key, value in env.items():                                     # every credential value
    if re.search(r"KEY|SECRET|TOKEN", key):
        add(value, minimum=8)

diff = subprocess.run(["git", "diff", "origin/main...HEAD"], capture_output=True, text=True).stdout
path, hits = "?", []
for number, line in enumerate(diff.splitlines(), 1):
    if line.startswith("+++ b/"):
        path = line[6:]
    elif line.startswith("+") and any(n in line.lower() for n in needles):
        hits.append(f"{path}: diff line {number}")
print(f"identifier sweep: checked {len(needles)} needles -", "CLEAN" if not hits else f"FOUND at {hits[:20]} (values not shown)")
raise SystemExit(1 if hits else 0)
PYEOF
command -v gitleaks >/dev/null && gitleaks git --log-opts="origin/main..HEAD" --no-banner || echo "gitleaks not installed - skipped"
```
Expected: `identifier sweep: ... - CLEAN` (public documentation links such as `docs.databricks.com` are fine) and gitleaks reports no leaks. Anything found: remove it, amend the offending commit by a fixup commit, and re-run.

Then prove the backend was not touched (the LLM stays the Databricks-served Unity Gateway model):

```bash
git diff --name-only origin/main...HEAD -- app/agent.py app/voice_profiles.py src/policy src/services app.yaml .env.example pyproject.toml uv.lock requirements.txt agent-requirements.in agent-requirements.txt | grep . && echo "BACKEND FILES CHANGED - undo" || echo "backend untouched"
```
Expected: `backend untouched`.

- [ ] **Step 5: Commit the docs**

```bash
git add docs/gotchas.md README.md docs/discovery/expressive-tags-contract.md
git commit -F - <<'EOF'
docs(plan-6): gotchas, verification recipe and results for the expressive voice (#33)

Refs #28 #33

Co-authored-by: Isaac <no-reply@databricks.com>
EOF
git status --short
```
If ` M uv.lock` appears, `git restore -- uv.lock`.

- [ ] **Step 6: Controller only — push and open the draft PR**

The controller prints the Isaac Review tip, confirms `gh api user -q .login` is `datasciencemonkey`, pushes with `git push -u origin feat/halloween-expressive-tags`, and opens a **draft** PR into `main` whose body links the epic and tasks (`Closes #29 #30 #31 #32 #33`, `Refs #28`), summarises what changed, attaches the test and tool evidence, and ends with the line `This pull request and its description were written by Isaac.`

---

## Self-review

- **Spec coverage:** §6.1 palette → T2; §6.2 filter → T2; §6.3 prompt → T3 (+ tuning T4; `ON_NOTE`/`ANNOUNCE_ON` deliberately unchanged, the spec is amended to say so); §6.5 tools → T1, T4; §7 invariants → Global Constraints + per-task tests; §8 testing → T1-T4 tests and the live runs, listening recipe → T5; §10 delivery → the protocol section and T5 step 6.
- **Placeholders:** the only data left to the implementer are values that cannot exist before T1 runs (the palette, measured figures, dates). Each has one named source: the contract's `palette` block, the tool output, the measured character count. A parity test makes the first one mechanical to check.
- **Type consistency:** `PALETTE`, `GROUP_HINTS`, `OTHER`, `OTHER_HINT`, `flatten`, `grouped`, `canonical_tag`, `SPOOKY_TAGS`, `measure`, `evaluate`, `Targets`, `Scenario`, `Trial`, `verdict`, `plan_jobs` are used with the same names and signatures in every task.
- **Backend LLM unchanged:** no task edits the LLM wiring (`app/agent.py`), routing, env/config, dependencies or the gateway clients; T5 step 4 proves it with a diff check. The models stay Databricks-served through Unity Gateway.
