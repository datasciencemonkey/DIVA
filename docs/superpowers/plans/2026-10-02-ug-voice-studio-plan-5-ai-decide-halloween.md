# UG Voice Studio — Plan 5: Expressive Mode + AI Decide Halloween Voice — Implementation Plan

> **Update 2026-10-05 (#45):** the `uaig_chat` fallback engine this plan builds was removed later. AI Decide now uses only the Databricks `ai_decide` REST API. The plan is implemented and runs on a Databricks App; Tasks 1 and 13 needed the ElevenLabs key and `ai_decide` Previews, which the owner supplied. Task 1's results went into `docs/gotchas.md`, the Plan 6 expressive-tags contract and `tools/ai_decide_check.py`, not into the two contract files it names. It is kept as the record of how it was built.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. This project is governed by `docs/constitution.md` — read it first.

**Goal:** Add a governed, auto-decided "Halloween mode" to the voice studio: Databricks `ai_decide` (fallback: a UAIG-served nano model) decides per turn whether the caller wants a spooky voice, a deterministic policy applies at most one mode transition, and in Halloween mode the Databricks LLM emits expressive tags (`[whispers]`…) that **ElevenLabs** performs — all outside the conversational LLM, same for every tier, and traced live on the Control pillar.

**Architecture:** Option B from the spec. The decision is a per-turn call made at the turn boundary behind a one-method `DecideClient` interface (engine `ai_decide` REST **or** `uaig_chat`), feeding a pure `decide_mode` policy. A `VoiceModeController` applies same-turn or announced switches, a mode-aware `tts_node` routes the active `VoiceProfile`, and a custom `tts_text_transforms` pipeline protects expressive tags through the stock filters. **Expressive audio is hand-rolled through the ElevenLabs provider plugin (BYO `ELEVEN_API_KEY`), NOT LiveKit's managed Expressive mode** (which requires `inference.TTS`/LiveKit Inference and excludes ElevenLabs) — this keeps the LLM + decision on Databricks and the voice vendor-direct. Pure modules carry no LiveKit import; LiveKit lives only in `studio_agent.py` + `agent.py`.

**Tech Stack:** Python 3.12 local / **3.11 for the Apps deploy**, `uv`; `livekit-agents==1.8.3` (bumped from 1.5.6; forces `livekit`==1.1.18, `livekit-api`==1.2.1, `openai`<3) + plugins openai/deepgram/silero/**elevenlabs** all `==1.8.3`; `openai.responses.LLM` over UAIG; `ai_decide` REST (`/api/2.0/ai-functions/ai-decide`) / UAIG Chat Completions; `httpx` (async, already pinned); OpenTelemetry → UC/MLflow.

**Spec:** `docs/superpowers/specs/2026-10-02-ug-voice-studio-plan-5-ai-decide-halloween-design.md` (the plan argues from the spec; executors read both — long verbatim bodies live in the spec sections cited per task).

**Gotchas:** `docs/gotchas.md` — read it; every trap below has an entry there.

**Branch / worktree:** `plan-5-halloween-mode` (isolated worktree off `plan-4-frontend` @ `da942cb`). Baseline: `uv run pytest` → 81 passed, 1 skipped (82 collected).

## Global Constraints

- Run everything with **`uv`**; Databricks profile is **user-chosen** (`--profile <name>`), never auto-selected. Commit as `datasciencemonkey <datasciencemonkey@gmail.com>`, ending messages with `Co-authored-by: Isaac <no-reply@databricks.com>`. **Checkpoint (commit) each task when it builds + verifies locally** (constitution #6).
- **No LiveKit Inference.** Expressive tags are hand-rolled via the ElevenLabs provider plugin (BYO key → `api.elevenlabs.io`). Never switch to `inference.TTS` to "simplify" — it breaks the Databricks boundary and drops ElevenLabs (gotchas: LiveKit §1). At 1.8.3 the framework ships a native `expressive` subsystem that must be kept OFF (`expressive=False`); it doesn't cover ElevenLabs, so we hand-roll (unchanged decision).
- **Decision is outside the conversational LLM**, sees only `{current_mode, agent_said(≤200), caller_said(≤300)}` — never tier, name, customer id, dataset prompt, or retrieved docs. It fails closed (timeout/error → mode unchanged) and never raises inside the turn hook.
- **`ai_decide` question types are `noul`/`choice`/`score` — never `bool`.** REST failures = non-2xx / null-or-missing `response` / unknown label / confidence∉[0,1] (there is NO `error_message` at REST). (gotchas: Databricks)
- **`uaig_chat` engine stays on a GPT-family model** (`databricks-gpt-5-4-nano`) — Claude rejects `json_object`. Its client is a dedicated `httpx.AsyncClient`; never call the sync `gateway.post()`/`uaig_chat.complete_json` from the async hook (gotchas: Repo).
- **Thresholds (pin, revisit after Task 1):** `ENTER_THRESHOLD=0.7`, `EXIT_THRESHOLD=0.5`, `MAX_ENTRIES_PER_CALL=3`. Exit is never capped; an explicit exit rule always wins.
- **Governance invariants G9–G13 (spec §9) enforced in code**, not just the prompt: mode never changes tier/model/directives (`bind` is a frozen dataclass); governance block stays last; tags are never read aloud and never shown; an exit is always honored.
- **Env/secrets (spec §7.12):** `UG_AI_DECIDE=1` kill switch; `UG_DECIDE_ENGINE=ai_decide`; `UG_DECIDE_MODEL=databricks-gpt-5-4-nano`; `UG_DECIDE_TIMEOUT_S=3.0`; `UG_DECIDE_CUE_WAIT_S=0.8`; `UG_HALLOWEEN_TTS=elevenlabs`; `UG_HALLOWEEN_TTS_MODEL=eleven_v3_conversational`; `UG_HALLOWEEN_VOICE_ID` (required for ElevenLabs); `UG_HALLOWEEN_STABILITY=0.5`; `UG_HALLOWEEN_FALLBACK_VOICE=aura-2-zeus-en`; secret `ELEVEN_API_KEY` (`valueFrom: elevenlabs-api-key`, scope `ug-voice-studio`).
- **No new argument to `build_instructions` changes today's output** — the existing byte-identical tests must stay green.
- **Deploy compile is Python 3.11** (the Apps runtime), not the local 3.12 (gotchas: Worktrees & deploy).

## Execution Order & Parallelization (constitution #4)

Task 1 (discovery) and Task 13 (live) need the ElevenLabs key and `ai_decide` Previews, which the owner supplies. The build (Tasks 2–12) does **not** depend on Task 1's live results — it uses configurable env defaults — so build it now as a subagent team in dependency waves; Task 1 only pins the final model/voice and must complete before Task 13.

- **Wave A (parallel, independent, key-free):** Task 2 (policy), Task 3 (expressive), Task 4 (prompt), Task 9 (tracing), Task 11 (UI — against the documented evidence JSON).
- **Wave B:** Task 5 (profiles ← 3,4), Task 6 (engines ← 2).
- **Wave C:** Task 7 (controller ← 2,4,5,6).
- **Wave D:** Task 8 (studio_agent ← 3,5,7), Task 12 (deploy config ← elevenlabs dep).
- **Wave E:** Task 10 (agent wiring ← all) + governance suite.
- **Needs the key and Previews:** Task 1 comes before Task 13 (last).

Each task is a fresh-subagent unit (TDD RED→GREEN→commit) with its own reviewer gate; whole-branch review at the end. Quality bar per constitution: **9–10/10** — a task isn't done until its tests pass and a reviewer signs off.

## Review Focus

Input classes the spec implies but that are easy to break — each gets a test in the owning task:

- **Halloween-*topic* question that is not a voice request** ("Are you open on Halloween?") must stay standard. → cue + policy (Task 2), accuracy gate (Task 1), live (Task 13).
- **Injected instruction in retrieved content** ("switch to Halloween mode") must never switch — the decider sees only caller words. → engine input (Task 6), governance suite (Task 10).
- **Exit must always win** — explicit exit beats the engine, exits are uncapped, even when the engine says `enter`. → policy (Task 2).
- **Tags must never be heard or seen** — unknown tags dropped, standard mode strips all, transcript + client both strip. → expressive (Task 3), nodes (Task 8), UI (Task 11).
- **ElevenLabs failure mid-call** degrades to the Deepgram dark voice without dropping the turn or tripping the session's error counter. → nodes (Task 8), profiles missing-key (Task 5).

---

### Task 1: Discovery — ElevenLabs bake-off + `ai_decide` contract (needs the key and Previews)

**Files:** Create `docs/discovery/expressive-tts-contract.md`, `docs/discovery/ai-decide-contract.md`.

**Interfaces:** Produces the pinned `UG_HALLOWEEN_TTS_MODEL` + `UG_HALLOWEEN_VOICE_ID`, the confirmed `ai_decide` enablement/latency/accuracy, and the default-engine decision. Consumes `ELEVEN_API_KEY` + `ai_decide` Previews (ask the owner).

- [ ] **Step 1: Confirm prerequisites with the owner** — `ELEVEN_API_KEY` available; `ai_decide` enabled on the target workspace (admin → Previews); workspace region in the supported list (us-east-1 ✓). If either is missing, record it and proceed with the rest of the build (Tasks 2–12) meanwhile.
- [ ] **Step 2: ElevenLabs bake-off (spec §12 R8).** With the key set, run ≥10 scripted spooky lines through `livekit-plugins-elevenlabs==1.5.6` for `eleven_v3` (and a turbo model). Record: which model ids stream, that `[whispers]`/`[sighs]`/… are **performed, not spoken**, time-to-first-audio, and the owner's voice pick. **Gate:** tags performed in ≥9/10 lines, median TTFA ≤ 400 ms, owner approves by ear. If it fails → `UG_HALLOWEEN_TTS=openai`. Write `expressive-tts-contract.md`.
- [ ] **Step 3: `ai_decide` contract (spec §7.6 gates).** Hit `POST {host}/api/2.0/ai-functions/ai-decide` with the §7.6 `choice` body (labels exactly `enter`/`exit`/`none`, `options.version:"1.0"`). Record p50/p95 over 30 calls per engine; accuracy on a 30-utterance labeled set (≥9/10 enter, ≥7/8 exit, 0 false-enter on the 12 none incl. the Halloween-topic questions). Repeat for the `uaig_chat` engine (live-test the `/ai-gateway/openai/v1/chat/completions` path — gotchas: Databricks). Pin the default per the gate. Write `ai-decide-contract.md`.
- [ ] **Step 4: Commit** (`docs: ai-decide + expressive-tts discovery contracts`).

---

### Task 2: Decision policy (pure, deterministic) — `src/policy/voice_mode.py`

**Files:** Create `src/policy/__init__.py` (if absent), `src/policy/voice_mode.py`; Test `tests/test_voice_mode_policy.py`.

**Interfaces (spec §7.2/§7.5):**
- Produces: `STANDARD`, `HALLOWEEN` consts; `ENTER_THRESHOLD=0.7`, `EXIT_THRESHOLD=0.5`, `MAX_ENTRIES_PER_CALL=3`; `@dataclass(frozen=True) IntentVerdict(intent, confidence, source, probabilities=None)`; `@dataclass(frozen=True) ModeDecision(mode_before, mode_after, reason)` with `.changed`; `has_cue(text)->bool`; `explicit_command(text)->IntentVerdict|None`; `resolve_verdict(model, rule)->IntentVerdict`; `decide_mode(current, verdict, *, entries_so_far=0)->ModeDecision`.
- Consumes: nothing (no LiveKit, no I/O).

- [ ] **Step 1: Write the failing tests** — `tests/test_voice_mode_policy.py`:

```python
from src.policy.voice_mode import (
    STANDARD, HALLOWEEN, IntentVerdict, has_cue, explicit_command,
    resolve_verdict, decide_mode,
)

def _v(intent, conf, source="model"): return IntentVerdict(intent, conf, source)

def test_has_cue_true_on_voice_and_halloween_wording():
    assert has_cue("can you do a spooky halloween voice")
    assert has_cue("make this creepy for me")
    assert has_cue("go back to your normal voice")

def test_has_cue_false_on_halloween_topic_not_about_voice():
    assert not has_cue("are you open on halloween")
    assert not has_cue("where is my order")

def test_explicit_command_detects_enter_exit_and_negation():
    assert explicit_command("do a spooky voice").intent == "enter"
    assert explicit_command("stop the spooky voice, go normal").intent == "exit"
    assert explicit_command("please don't make it spooky") is None  # negation ≠ enter

def test_resolve_verdict_exit_rule_always_wins():
    model = _v("enter", 0.99); rule = IntentVerdict("exit", 1.0, "rule")
    assert resolve_verdict(model, rule).intent == "exit"

def test_resolve_verdict_prefers_model_then_rule_then_none():
    assert resolve_verdict(_v("enter", 0.8), None).source == "model"
    miss = IntentVerdict("none", 0.0, "timeout")
    assert resolve_verdict(miss, IntentVerdict("enter", 1.0, "rule")).source == "rule"
    assert resolve_verdict(miss, None).intent == "none"

def test_decide_mode_table():
    assert decide_mode(STANDARD, _v("enter", 0.71)).mode_after == HALLOWEEN
    assert decide_mode(STANDARD, _v("enter", 0.69)).reason == "below_threshold"
    assert decide_mode(STANDARD, _v("enter", 0.9), entries_so_far=3).reason == "entry_cap"
    assert decide_mode(HALLOWEEN, _v("exit", 0.51)).mode_after == STANDARD
    assert decide_mode(HALLOWEEN, _v("exit", 0.49)).reason == "below_threshold"
    assert decide_mode(HALLOWEEN, _v("enter", 0.99)).reason == "already_in_mode"
    assert decide_mode(STANDARD, _v("none", 0.0)).reason == "no_intent"
    assert decide_mode(HALLOWEEN, _v("exit", 0.9), entries_so_far=99).mode_after == STANDARD  # exit uncapped
```

- [ ] **Step 2: Run → FAIL** (`ModuleNotFoundError`). `uv run pytest tests/test_voice_mode_policy.py -q`.
- [ ] **Step 3: Implement `src/policy/voice_mode.py`** per spec §7.2/§7.5. `has_cue`: word-boundary match on a spooky/voice lexicon (spooky, scary, creepy, haunt, ghost, halloween, spine…) **and** voice/normal-voice wording; no match on `"open on halloween"`-type topic phrases. `explicit_command`: regex for clear enter/exit, returning `None` on negation ("don't/stop … spooky"). `resolve_verdict`: exit-rule > model(source=="model") > rule > none. `decide_mode`: the §7.5 table exactly (enter needs `confidence>=ENTER_THRESHOLD` and `entries_so_far<MAX_ENTRIES_PER_CALL`; exit needs `>=EXIT_THRESHOLD`, never capped; `already_in_mode`/`no_intent` otherwise). `ModeDecision.changed = mode_before != mode_after`.
- [ ] **Step 4: Run → PASS.** Commit (`feat(policy): deterministic voice-mode decision policy`).

---

### Task 3: Expressive tokens pipeline — `app/expressive.py`

**Files:** Create `app/expressive.py`; Test `tests/test_expressive.py`.

**Interfaces (spec §7.2/§7.8):**
- Produces: `SPOOKY_TAGS: tuple[str,...]`; `encode_tags(vocabulary: Callable[[],frozenset[str]], on_tag=None)` (stage-1 streaming transform → private-use placeholders); `async decode_tags(text)` (stage-4 → back to `[tag]`); `strip_tags(text)->str`; `async strip_tags_stream(text)`.
- Consumes: LiveKit `filter_markdown` only via **lazy import** (keeps the module importable without LiveKit for unit tests).

- [ ] **Step 1: Write the failing tests** — `tests/test_expressive.py` (async where needed):

```python
import pytest
from app.expressive import SPOOKY_TAGS, encode_tags, decode_tags, strip_tags

VOCAB = lambda: frozenset(SPOOKY_TAGS)
EMPTY = lambda: frozenset()

async def _run(stage, chunks):
    async def gen():
        for c in chunks: yield c
    return "".join([x async for x in stage(gen())])

@pytest.mark.asyncio
async def test_allowed_tag_becomes_placeholder_then_decodes_back():
    enc = encode_tags(VOCAB)
    mid = await _run(enc, ["[whispers] boo"])
    assert "[whispers]" not in mid and "boo" in mid           # opaque placeholder in the middle
    out = await _run(decode_tags, [mid])
    assert out == "[whispers] boo"                            # restored for the TTS

@pytest.mark.asyncio
async def test_unknown_tag_dropped_and_empty_vocab_strips_all():
    assert "boo" in await _run(encode_tags(VOCAB), ["[explosion] boo"]) and "[explosion]" not in await _run(encode_tags(VOCAB), ["[explosion] boo"])
    assert await _run(encode_tags(EMPTY), ["[whispers] boo"]) == (await _run(encode_tags(EMPTY), ["[whispers] boo"]))  # no tags survive
    assert "[whispers]" not in await _run(encode_tags(EMPTY), ["[whispers] boo"])

@pytest.mark.asyncio
async def test_tag_split_across_chunks_and_markdown_link_untouched():
    assert "[whispers]" in await _run(decode_tags, [await _run(encode_tags(VOCAB), ["[whi", "spers] hi"])])
    assert "[text](http://x)" in await _run(encode_tags(VOCAB), ["see [text](http://x)"])  # link is not a tag

def test_strip_tags_removes_all_brackets():
    assert strip_tags("[whispers] your order [sighs] shipped") == "your order shipped"

@pytest.mark.asyncio
async def test_placeholders_survive_filter_emoji():  # regression (gotchas: LiveKit §filter_markdown)
    from livekit.agents.voice.transcription.filters import filter_emoji  # noqa
    mid = await _run(encode_tags(VOCAB), ["[whispers] boo"])
    passed = await _run(lambda g: filter_emoji(g), [mid]) if False else mid  # see Step 3 for the real chain assembly
    assert "" <= [c for c in mid if c >= ""][0] <= ""
```

- [ ] **Step 2: Run → FAIL.** Add the "stock `filter_markdown` holds `[whispers]` until stream end, our encode streams it early" regression test from spec §10. `uv run pytest tests/test_expressive.py -q`.
- [ ] **Step 3: Implement `app/expressive.py`** per spec §7.8: `SPOOKY_TAGS = ("whispers","sighs","laughs","mischievously","nervously","exhales","inhales deeply")`. `encode_tags` buffers at most one partial `[...]` (≤34 chars) with one-char look-ahead to distinguish a tag from a markdown link (`](`); allowed tags → opaque private-use placeholders (U+E000+); unknown tags dropped; empty vocab drops all. `decode_tags` maps placeholders back to `[tag]`. `strip_tags`/`strip_tags_stream` remove every `[...]`. (Private-use chars survive `filter_markdown`/`filter_emoji` — verified; see gotchas.)
- [ ] **Step 4: Run → PASS.** Commit (`feat(expressive): streaming tag encode/decode + strip`).

---

### Task 4: Persona + prompt extension — `src/agent_prompt.py`

**Files:** Modify `src/agent_prompt.py`; Test `tests/test_agent_prompt.py` (extend).

**Interfaces (spec §7.9):**
- Produces: `HALLOWEEN_PERSONA`, `VOICE_REQUESTS`, `ON_NOTE`/`OFF_NOTE`, `ANNOUNCE_ON`/`ANNOUNCE_OFF`; extended `build_instructions(system_prompt, directives, courtesy_name=None, *, persona=None, expressive_tags=(), voice_requests=False)`.
- Consumes: nothing.

- [ ] **Step 1: Write the failing tests** — extend `tests/test_agent_prompt.py`:

```python
from src.agent_prompt import build_instructions, HALLOWEEN_PERSONA

_D = {"recognition_tone": "neutral", "be_proactive": False, "thoroughness": "concise", "offer_human_escalation": False}

def test_output_unchanged_when_no_new_args():
    # byte-identical to today's 3-arg behavior (pins the existing contract)
    assert build_instructions("Support.", _D) == build_instructions("Support.", _D, None)

def test_persona_sits_before_governance_block():
    out = build_instructions("Support.", _D, persona=HALLOWEEN_PERSONA)
    assert out.index("Halloween") < out.index("Governance (non-negotiable)")

def test_cue_rules_only_present_with_tags():
    with_tags = build_instructions("s", _D, persona=HALLOWEEN_PERSONA, expressive_tags=("whispers",))
    without = build_instructions("s", _D, persona=HALLOWEEN_PERSONA, expressive_tags=())
    assert "[whispers]" in with_tags and "[whispers]" not in without

def test_voice_request_line_only_when_asked_and_no_tier_words():
    vr = build_instructions("s", _D, voice_requests=True).lower()
    assert "one moment" in vr and "never refuse" in vr
    assert all(w not in vr for w in ("vip", "premium", "tier", "loyalty"))
```

- [ ] **Step 2: Run → FAIL.** `uv run pytest tests/test_agent_prompt.py -q`.
- [ ] **Step 3: Implement** — add the constants (spec §7.9: persona = playful/eerie host, never threatening, every fact stated exactly, drop the act if the caller is uncomfortable, "As you wish…" on exit). Extend `build_instructions` to insert `persona` + (only if `expressive_tags`) the cue rules **between** `system_prompt` and `_GOVERNANCE`, and append the `VOICE_REQUESTS` line when `voice_requests=True`. Keep `_GOVERNANCE` last and the no-arg path byte-identical.
- [ ] **Step 4: Run → PASS** (incl. the pre-existing prompt tests). Commit (`feat(prompt): halloween persona + expressive cue rules`).

---

### Task 5: Voice profiles + router — `app/voice_profiles.py`

**Files:** Create `app/voice_profiles.py`; Test `tests/test_voice_profiles.py`.

**Interfaces (spec §7.2/§7.7):**
- Produces: `@dataclass(frozen=True) VoiceProfile(key, label, vendor, tags: frozenset[str], persona: str|None)`; `resolve_profiles(env) -> dict[str,VoiceProfile]` (standard / halloween / halloween_fallback, honoring `UG_HALLOWEEN_TTS` + missing-key fallback); `build_tts(profile)` (lazy plugin import; ElevenLabs/OpenAI/Deepgram).
- Consumes: Task 3 `SPOOKY_TAGS`, Task 4 `HALLOWEEN_PERSONA`; plugins via lazy import.

- [ ] **Step 1: Write the failing tests** (fake plugin modules; no real vendor calls) — `tests/test_voice_profiles.py`:

```python
from app.voice_profiles import resolve_profiles

def test_elevenlabs_default_when_key_present():
    p = resolve_profiles({"UG_HALLOWEEN_TTS": "elevenlabs", "ELEVEN_API_KEY": "k", "UG_HALLOWEEN_VOICE_ID": "v"})
    assert p["halloween"].vendor == "elevenlabs" and p["halloween"].tags  # tags only for elevenlabs
    assert p["standard"].tags == frozenset() and p["standard"].persona is None

def test_missing_elevenlabs_key_falls_back_to_deepgram_no_tags():
    p = resolve_profiles({"UG_HALLOWEEN_TTS": "elevenlabs"})  # no ELEVEN_API_KEY
    assert p["halloween"].vendor == "deepgram" and p["halloween"].tags == frozenset()
    assert p["halloween"].persona is not None  # still spooky wording

def test_openai_vendor_has_persona_but_no_inline_tags():
    p = resolve_profiles({"UG_HALLOWEEN_TTS": "openai", "OPENAI_API_KEY": "k"})
    assert p["halloween"].vendor == "openai" and p["halloween"].tags == frozenset()
```

- [ ] **Step 2: Run → FAIL.** `uv run pytest tests/test_voice_profiles.py -q`.
- [ ] **Step 3: Implement** per spec §7.7 (reproduce the profile table). `build_tts` lazily imports the plugin and constructs it with the ElevenLabs params confirmed by the Task-1/research findings (`model=UG_HALLOWEEN_TTS_MODEL`, `voice_id`, `voice_settings` with `UG_HALLOWEEN_STABILITY`); OpenAI → `openai.TTS(model="gpt-4o-mini-tts", instructions=<spooky style>)`; fallback → Deepgram `UG_HALLOWEEN_FALLBACK_VOICE`. The profile-owned TTS uses tight `APIConnectOptions(max_retry=1, timeout=5.0)` and sits outside the session's error count.
- [ ] **Step 4: Run → PASS.** Commit (`feat(voice): voice profiles + vendor router with fallback`).

---

### Task 6: Decision engines — `src/services/ai_decide.py`

**Files:** Create `src/services/ai_decide.py`; Test `tests/test_ai_decide.py`.

**Interfaces (spec §7.2/§7.6):**
- Produces: `DecideClient(host, token, *, engine=None, model=None, timeout_s=None, http=None)` with `engine`, `label`, `async classify(utterance, last_agent_line, current_mode) -> tuple[IntentVerdict, float]` (never raises), `async aclose()`; pure helpers `build_ai_decide_body(...)`, `build_uaig_chat_body(...)`, `parse_ai_decide(resp)`, `parse_uaig_chat(resp)`.
- Consumes: Task 2 `IntentVerdict`; its **own `httpx.AsyncClient`** (NOT `gateway.post`).

- [ ] **Step 1: Write the failing tests** — `tests/test_ai_decide.py` (inject a fake async http):

```python
import pytest
from src.services.ai_decide import DecideClient, build_ai_decide_body, parse_ai_decide

def test_state_carries_only_three_truncated_fields():
    body = build_ai_decide_body("x"*500, "y"*500, "standard")
    st = body["state"]
    assert set(st) == {"current_mode", "agent_said", "caller_said"}
    assert len(st["caller_said"]) <= 300 and len(st["agent_said"]) <= 200

def test_ai_decide_body_shape():
    q = build_ai_decide_body("hi", "", "standard")["questions"]["voice_mode"]
    assert q["type"] == "choice" and set(q["criteria"]) == {"enter", "exit", "none"}
    assert build_ai_decide_body("hi","", "standard")["options"]["version"] == "1.0"

def test_parse_ai_decide_valid_and_failure_modes():
    ok = {"answers": {"voice_mode": {"type":"choice","choice":"enter","probabilities":{"enter":0.9,"exit":0.0,"none":0.1},"confidence":0.9}}}
    v = parse_ai_decide(ok); assert v.intent == "enter" and v.confidence == 0.9
    assert parse_ai_decide({"response": None}).source == "error"          # null response
    assert parse_ai_decide({"answers":{"voice_mode":{"choice":"ZZZ"}}}).intent == "none"   # unknown label
    assert parse_ai_decide({"answers":{"voice_mode":{"choice":"enter","confidence":2}}}).source == "error"  # conf out of range

@pytest.mark.asyncio
async def test_classify_never_raises_on_timeout_or_garbage():
    class Boom:
        async def post(self, *a, **k): raise TimeoutError()
    c = DecideClient("http://h", "t", engine="ai_decide", http=Boom())
    v, ms = await c.classify("spooky please", "", "standard")
    assert v.source in ("timeout", "error") and v.intent == "none"
```

- [ ] **Step 2: Run → FAIL.** `uv run pytest tests/test_ai_decide.py -q`.
- [ ] **Step 3: Implement** per spec §7.6. `ai_decide` engine → `POST {host}/api/2.0/ai-functions/ai-decide` (bearer `DATABRICKS_TOKEN`), body from `build_ai_decide_body`. `uaig_chat` engine → `POST {host}/ai-gateway/openai/v1/chat/completions`, `response_format={"type":"json_object"}`, GPT-family `model`, `reasoning_effort` only if `UG_DECIDE_REASONING_EFFORT` set. **Failure handling (fix):** treat non-2xx / null-or-missing `response` / unknown label / confidence∉[0,1] as `none` with `source` in `{timeout,error}`; there is NO `error_message` field at REST. No retries on the hot path; timeout `UG_DECIDE_TIMEOUT_S`. Own `httpx.AsyncClient`; `classify` wraps everything in try/except and returns `(IntentVerdict, latency_ms)`.
- [ ] **Step 4: Run → PASS.** Commit (`feat(decide): ai_decide + uaig_chat engines behind DecideClient`).

---

### Task 7: Voice-mode controller — `app/voice_mode.py`

**Files:** Create `app/voice_mode.py`; Test `tests/test_voice_mode_controller.py`.

**Interfaces (spec §7.2/§7.3/§7.4):**
- Produces: `VoiceModeState{mode, entries, transitions, degraded, last_agent_line, tags_spoken, turn_seq, last}`; `VoiceModeController(classifier, profiles, instructions_for, *, evidence_sink=None, tracer=None, on_cue=None, enabled=True, cue_wait_s=0.8)` with `profile`, `vocabulary()`, `instructions()`, `prefetch(transcript)`, `note_agent_line(text)`, `count_tag(name)`, `async on_turn(agent, turn_ctx, new_message)` (never raises), `mark_degraded(reason)`.
- Consumes: Tasks 2,4,5,6; the 1.8.3 `update_instructions` helper via lazy import.

- [ ] **Step 1: Write the failing tests** — `tests/test_voice_mode_controller.py` (fake classifier/profiles; a **real** `livekit.agents.llm.ChatContext` for the patch assertion). Cover: prefetch reuse; non-cue turn skips the wait; a same-turn enter patches a real `ChatContext` and calls `update_instructions`; an announced enter calls `generate_reply` once with `ANNOUNCE_ON`; a late answer after `turn_seq` advanced is dropped (`path=late_dropped`); an exit rule beats the engine; exceptions in the hook are swallowed (turn continues); evidence payload contains no transcript/PII; `mark_degraded` sets only `degraded`; `enabled=False` makes zero calls.
- [ ] **Step 2: Run → FAIL.** `uv run pytest tests/test_voice_mode_controller.py -q`.
- [ ] **Step 3: Implement** per spec §7.3 sequencing + §7.4 single-writer state. `prefetch` starts the background `classify` on the final transcript and accumulates multi-segment turns. `on_turn`: wait ≤ `cue_wait_s` only when `has_cue`; `resolve_verdict`→`decide_mode`; if changed → `_transition` (same-turn: patch `turn_ctx` via the 1.8.3 helper + `agent.update_instructions`; else announced via `session.generate_reply(instructions=ANNOUNCE_*)` guarded by `turn_seq`). Publish evidence (§7.2 payload) + `ug.ai_decide` span. **Never raise** (gotchas: LiveKit — a hook exception drops the turn).
- [ ] **Step 4: Run → PASS.** Commit (`feat(voice): VoiceModeController — decide, switch, degrade, evidence`).

---

### Task 8: Studio agent nodes — `app/studio_agent.py`

**Files:** Create `app/studio_agent.py`; Test `tests/test_studio_agent.py`.

**Interfaces (spec §7.1/§7.7):** Produces `StudioAgent(Agent)` with `on_user_turn_completed` → `controller.on_turn`, a profile-routed `tts_node` (standard → `Agent.default.tts_node`; else stream through the profile-owned TTS; vendor error → `controller.mark_degraded` + fallback, **no exception, outside the session error count** — gotchas: LiveKit C10), and a tag-stripping `transcription_node`. Consumes Tasks 3,5,7.

- [ ] **Step 1: Write the failing tests** — `tests/test_studio_agent.py` (fake controller + fake profile TTS): standard profile delegates to the default node; Halloween streams through the profile TTS; a raised vendor error → `mark_degraded` called, no exception propagates, fallback audio continues; `transcription_node` output has no `[tags]`; `on_user_turn_completed` calls `controller.on_turn`.
- [ ] **Step 2: Run → FAIL.** `uv run pytest tests/test_studio_agent.py -q`.
- [ ] **Step 3: Implement** per spec §7.7 (copy the 1.8.3 default `tts_node` for the profile branch, with `APIConnectOptions(max_retry=1, timeout=5.0)`; wrap in try/except → `mark_degraded`). `transcription_node` runs `strip_tags_stream`.
- [ ] **Step 4: Run → PASS.** Commit (`feat(agent): StudioAgent nodes — tts routing + tag-stripped transcript`).

---

### Task 9: Tracing extension — `app/tracing.py`

**Files:** Modify `app/tracing.py`; Test `tests/test_tracing.py` (extend).

**Interfaces (spec §7.11):** Produces `ug.voice_mode*` root attrs + a `ug.ai_decide` span type. Consumes nothing.

- [ ] **Step 1: Write the failing tests** — assert `_SPAN_TYPES["ug.ai_decide"] == "CHAIN"` (**fix #3** — the exporter JSON-encodes the value, so it must go through the map, not a raw string), and that root enrichment sets `ug.voice_mode`, `ug.voice_mode_transitions`, `ug.decide_engine`, `ug.expressive_tags`, `ug.voice_degraded`.
- [ ] **Step 2: Run → FAIL.** `uv run pytest tests/test_tracing.py -q`.
- [ ] **Step 3: Implement** — add `"ug.ai_decide": "CHAIN"` to `_SPAN_TYPES` (app/tracing.py:25) and extend `fill_ug_metadata` with the `ug.voice_*` attrs (fail-soft, PII-free — no utterance text; LiveKit's own user-turn spans hold the transcript).
- [ ] **Step 4: Run → PASS.** Commit (`feat(tracing): ug.voice_* attrs + ai_decide CHAIN span type`).

---

### Task 10: Agent wiring + governance suite — `app/agent.py`

**Files:** Modify `app/agent.py`; Test `tests/test_voice_governance.py`.

**Interfaces:** Consumes every prior task. Builds `StudioAgent` instead of `Agent`; wires `resolve_profiles`, `DecideClient`, `VoiceModeController`, the 4-stage `tts_text_transforms` (`[encode_tags(controller.vocabulary, controller.count_tag), "filter_markdown", "filter_emoji", decode_tags]`), the `user_input_transcribed`→`prefetch` + `conversation_item_added`→`note_agent_line` hooks, and the `on_cue` prewarm.

- [ ] **Step 1: Write the failing governance tests** — `tests/test_voice_governance.py` (G9–G13 structural, spec §9): `bind` is unchanged across transitions (frozen dataclass, controller never touches it); a spy confirms the engine input is only `{current_mode, agent_said, caller_said}` (G10 — no tier/name/docs); the persona can't drop the governance block (G11, via Task 4); an explicit exit beats the engine and is uncapped (G12, via Task 2); tags never reach transcript/audio in standard mode (G13, via Tasks 3/8).
- [ ] **Step 2: Run → FAIL.** `uv run pytest tests/test_voice_governance.py -q`.
- [ ] **Step 3: Implement the wiring** in `app/agent.py:130-182` (session build + agent start). Set `tts_text_transforms` on the session; construct the controller with `instructions_for = lambda profile: build_instructions(bind.system_prompt, bind.directives, bind.courtesy_name, persona=profile.persona, expressive_tags=tuple(sorted(profile.tags)), voice_requests=UG_AI_DECIDE)`; pass `StudioAgent(instructions=controller.instructions(), controller=controller, fallback_profile=profiles["halloween_fallback"], build_tts_fn=build_tts)` (NOT `profiles=` — the ctor has no such kwarg → TypeError); register the event hooks + evidence. `UG_AI_DECIDE=0` → controller `enabled=False`, standard only.
- [ ] **Step 4: Run → PASS** and the whole offline suite green (`uv run pytest -q`). Commit (`feat(agent): wire AI Decide + Halloween voice into the session`).

---

### Task 11: Studio UI — state → view (constitution #5) — `app/web/public/*`

**Files:** Modify `app/web/public/studio.js`, `index.html`, `studio.css`; Test: manual + the devloop (web-devloop-tester).

**Interfaces:** Consumes the `voice_mode` evidence payload (spec §7.2). The front-end **must visibly change** when the mode changes.

- [ ] **Step 1: Add the evidence branch** — in `handleEvidence` (studio.js:944-953) add `if (obj.voice_mode) applyVoiceMode(obj.voice_mode);` (**fix #5** — the router dispatches on fragment keys, not a type field).
- [ ] **Step 2: `applyVoiceMode(v)`** — a Control-pillar row "Voice mode · AI Decide": mode chip, voice label, "decided by `<label>` · `<confidence>` · `<latency>` ms · `<path>`", a 3-bar probability readout when `probabilities` present, "Fallback voice" when degraded; set `document.body.dataset.voiceMode = v.mode` to drive a restrained Halloween accent (CSS only, respects `prefers-reduced-motion`).
- [ ] **Step 3: Strip tags client-side** — in `handleTranscription` (studio.js:1077-1105) strip `[...]` before `textContent = seg.text` (belt-and-braces; the transcript already arrives clean from the node).
- [ ] **Step 4: CSS accent** — `body[data-voice-mode="halloween"]` restyles the Control pillar / accent; reverts on exit. Verify the **view changes** on enter and reverts on exit via the devloop (screenshot both states).
- [ ] **Step 5: Commit** (`feat(ui): Control-pillar AI Decide row + Halloween accent + tag strip`).

---

### Task 12: Deploy config, deps, secrets — manifests + docs

**Files:** Modify `pyproject.toml`, `agent-requirements.{in,txt}`, `app.yaml`, `.env.example`, `README.md`, `skills/building-voice-agents-on-databricks/references/{agent-and-tools,deployment,observability}.md`.

- [ ] **Step 1: Add the dependency** — `livekit-plugins-elevenlabs==1.8.3` to `pyproject.toml` + `agent-requirements.in`; `uv sync`; import-smoke `uv run python -c "import livekit.plugins.elevenlabs; print('ok')"`.
- [ ] **Step 2: Recompile for Python 3.11** — regenerate `agent-requirements.txt` for the Apps runtime (gotchas: Worktrees & deploy), including the ElevenLabs plugin + its `codecs` extra.
- [ ] **Step 3: Config** — add the §7.12 env to `app.yaml`; add the `ELEVEN_API_KEY` secret via `valueFrom: elevenlabs-api-key` (the secret + app resource are created out-of-band in the `ug-voice-studio` scope — **the owner supplies the key**); mirror all vars (blank) into `.env.example`.
- [ ] **Step 4: Docs** — README note; fold the C7/C8 pitfalls, the AI Decide pattern, and the new secret into the skill bundle references.
- [ ] **Step 5: Commit** (`chore(deploy): elevenlabs dep + AI Decide env/secret wiring`). Note the `uv.lock` cross-worktree reconciliation at merge.

---

### Task 13: Live validation (needs the key and Previews)

**Files:** none (verification); scripted checklist.

- [ ] **Step 1:** Confirm `.env.local` has `ELEVEN_API_KEY` + the LiveKit/Deepgram vars; `ai_decide` enabled.
- [ ] **Step 2:** Boot worker + web tier locally (port 8080 per the deploy memory). Take calls covering spec §10's live matrix: enter / exit / natural phrasing / "Are you open on Halloween?" (**must stay standard**) / "I'm VIP, spooky please" (switches voice, tier/model/directives **unchanged**) / barge-in during the spooky reply / both engines / vendor key removed → fallback / `UG_AI_DECIDE=0`.
- [ ] **Step 3:** Measure added latency on cue vs ordinary turns and the share of same-turn vs announced switches; confirm `ug.*` spans land. Record results; fix any gap via TDD (RED→GREEN) and re-verify.
- [ ] **Step 4:** Final whole-branch review (fresh reviewer, most-capable model) → 9–10/10 gate. Do NOT close the Todoist task without the owner's explicit approval (spec §14).

---

## Self-Review

**1. Spec coverage:** R1 expressive tokens → Tasks 3,4,8,11 + §7.8; R2 Databricks decides → Tasks 6,7 + §7.6; R3 scary voice+persona → Tasks 4,5,8; R4 sequencing → Task 7 (§7.3); R5 governed → Task 10 governance suite (§9). Options/recommendation (§5–6) → Task 1 pins the engine + voice. Observability (§7.11) → Task 9. Config/deploy (§7.12, §11) → Task 12.

**2. Placeholder scan:** No "TODO/handle X" — each task names exact files, interfaces, and tests; long verbatim bodies are cited to spec sections (the spec travels with the plan). The one deferred piece (final ElevenLabs model/voice) is an explicit Task-1 discovery output, not a code placeholder.

**3. Type consistency:** `IntentVerdict`/`ModeDecision` (T2) flow into `DecideClient.classify` (T6) and `VoiceModeController` (T7); `VoiceProfile` (T5) is read by `tts_node` (T8) and `resolve_profiles` feeds `app/agent.py` (T10); `build_instructions(..., persona=, expressive_tags=, voice_requests=)` (T4) is called with `VoiceProfile` fields in T10; the `voice_mode` evidence JSON (T7) matches `applyVoiceMode` (T11); `_SPAN_TYPES["ug.ai_decide"]` (T9) is emitted by the controller span (T7).

**4. Review Focus:** Halloween-topic-not-voice (T2 `test_has_cue_false...` + T1 accuracy + T13); injection (T10 G10 spy); exit-always-wins (T2 `decide_mode` uncapped exit + resolve_verdict exit-rule); tags-never-heard/seen (T3 empty-vocab + T8 transcript + T11 client strip); ElevenLabs mid-call failure (T8 degrade + T5 missing-key). All five mapped.

---

*Plan 5 adds governed, auto-decided Halloween voice with Databricks-performed inference and vendor-direct ElevenLabs expression. Tasks 2–12 need no keys; Tasks 1 + 13 need the ElevenLabs key and `ai_decide` Previews. Governed by `docs/constitution.md`; traps tracked in `docs/gotchas.md`.*
