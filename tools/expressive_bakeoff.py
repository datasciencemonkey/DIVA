"""Bake-off: which ElevenLabs audio tags does the Halloween voice PERFORM, and which does it READ ALOUD?

For each candidate tag it synthesizes a short carrier sentence with the tag in front and without it, through the
same plugin, model, voice and voice settings the Halloween profile uses (app/voice_profiles.py), transcribes the
audio with Deepgram, and classifies the tag:

  spoken-aloud       the tag's own words are heard in the audio (G13: never allowed on the air)
  performed          not read aloud, and the audio differs from the plain carrier beyond run-to-run noise
  no-audible-effect  not read aloud, but indistinguishable from the plain carrier (safe, but useless)

Judged by transcript and duration, not by ear: "performed" means the voice did something other than read the tag,
not that it did the intended thing. A human listen is the last check (docs/halloween-voice.md, "Verify the voice").

Credentials come from the gitignored .env.local and are never printed. The ElevenLabs key may be scoped to speech
only, so this tool never calls an account or model endpoint.

    uv run --frozen python tools/expressive_bakeoff.py --dry-run
    uv run --frozen python tools/expressive_bakeoff.py --only "whispers,building tension" --carriers 1
    uv run --frozen python tools/expressive_bakeoff.py --json /tmp/bakeoff.json
    uv run --frozen python tools/expressive_bakeoff.py --from-palette      # re-verify the shipped palette
    uv run --frozen python tools/expressive_bakeoff.py --stability-check   # 0.0 vs 0.5 on the reference passage

Exit codes: 0 done. 1 a request failed; or --from-palette found a shipped tag read aloud; or --stability-check heard a
cue read aloud. (In a plain candidate run a spoken-aloud candidate is an expected, excluded result and still exits 0.)
2 bad arguments, missing .env.local values, or over the synthesis budget.
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


def exit_code(counts: dict[str, int], failures: list[str], from_palette: bool) -> int:
    """1 when a request failed or a row errored, or, in --from-palette mode, when a shipped tag was read aloud (G13).

    In a plain candidate run a spoken-aloud candidate is an expected, excluded result, so it still exits 0 there.
    """
    if from_palette and counts["spoken-aloud"]:
        return 1
    return 1 if (failures or counts["error"]) else 0


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
    return exit_code(counts, failures, args.from_palette)


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
