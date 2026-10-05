"""Live check: does the Halloween prompt make the REAL tier models write rich, compliant cues?

Builds the exact Halloween instructions the agent sends (persona + cue rules + governance, via
`build_instructions`), runs scripted caller utterances against each tier's model through the Unity Gateway
Responses API (the endpoint the agent uses; Databricks-served models only), and reports per model: cues per 100 words, distinct cues, allowlist
compliance (before and after case/spacing are forgiven), stage directions, stacked cues, facts kept verbatim, cues
that split a fact, the median reply length, and the prompt's token overhead. A lookup result reaches the model as a
recorded tool call and its output, the way the agent's tools deliver it. Exit code 1 if any model misses a target.

Credentials come from the gitignored .env.local and are never printed. A failed request prints only the exception's
class and HTTP status, never its message or URL.

    uv run --frozen python tools/expressive_llm_check.py --dry-run
    uv run --frozen python tools/expressive_llm_check.py                 # 3 tiers x 7 utterances x 3 samples
    uv run --frozen python tools/expressive_llm_check.py --tiers Standard --samples 1
    uv run --frozen python tools/expressive_llm_check.py --json /tmp/llm_check.json    # keep the raw replies
"""
from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import sys
from dataclasses import asdict, dataclass
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
STORY_MIN_WORDS = 25          # a shorter reply to "tell me a story" is a refusal, which measures nothing
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
    tool: str = "semantic_search"      # the agent tool that returned `tool_result`


SCENARIOS: tuple[Scenario, ...] = (
    Scenario("story", "story", "Tell me a scary story."),
    Scenario("lighthouse", "story", "Tell me a short spooky story about a haunted lighthouse."),
    Scenario("order", "factual", "Where is my order, and what was the total?",
             "Order 48213 shipped on Tuesday, October 7 and arrives Friday, October 10. The total was $84.50.",
             ("48213", "October 7", "October 10", "84.50"), tool="record_lookup"),
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
    story_min_words: int = 0            # a story reply shorter than this is a refusal, not a story (0: not checked)


def evaluate(rows: list[tuple[Scenario, ReplyMetrics]], targets: Targets = Targets()) -> list[str]:
    """Why the model misses the targets; an empty list means it passes."""
    problems: list[str] = []
    story = [m.per_100 for s, m in rows if s.kind == "story"]
    if story and statistics.median(story) < targets.story_cues_per_100:
        problems.append(f"story cues/100 words median {statistics.median(story):.1f} < {targets.story_cues_per_100}")
    short_stories = sum(1 for s, m in rows if s.kind == "story" and m.words < targets.story_min_words)
    if short_stories:
        problems.append(f"{short_stories} story replies under {targets.story_min_words} words (a refusal is not a story)")
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


def conversation(scenario: Scenario) -> str | list[dict]:
    """What the model is shown. A scenario with a lookup result gets a recorded tool call and its output, the way the
    agent's tools answer: a result pasted into the caller's own message is read as the caller's claim, not as a tool
    result, and the governance says to answer only from the tools."""
    if not scenario.tool_result:
        return scenario.user
    return [
        {"role": "user", "content": scenario.user},
        {"type": "function_call", "call_id": "call_lookup", "name": scenario.tool,
         "arguments": json.dumps({"query": scenario.user})},
        {"type": "function_call_output", "call_id": "call_lookup", "output": scenario.tool_result},
    ]


def ask(client, model: str, instructions: str, scenario: Scenario) -> tuple[str, int]:
    resp = client.responses.create(model=model, instructions=instructions, input=conversation(scenario),
                                   reasoning={"effort": "low"}, store=False)
    return resp.output_text or "", resp.usage.input_tokens


def median_words(rows: list[tuple[Scenario, ReplyMetrics]], kind: str) -> float:
    """Median reply length in words over the replies of one scenario kind (0 when there are none)."""
    return statistics.median([m.words for s, m in rows if s.kind == kind] or [0])


def failure_summary(exc: Exception) -> str:
    """All that is safe to print about a failed request: the class and the HTTP status, never the message or URL."""
    status = getattr(exc, "status_code", None)
    return type(exc).__name__ + (f" (HTTP {status})" if status else "")


def parse_args(argv):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--tiers", default=",".join(TIERS))
    p.add_argument("--samples", type=int, default=3)
    p.add_argument("--max-calls", type=int, default=DEFAULT_MAX_CALLS)
    p.add_argument("--dry-run", action="store_true", help="print the call count; no credentials, no network")
    p.add_argument("--json", help="write every reply and its metrics to this path (keep it outside the repo)")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    tiers = [t.strip() for t in args.tiers.split(",") if t.strip()]
    unknown = [t for t in tiers if t not in TIERS]
    if unknown:
        print(f"unknown tier(s): {unknown}; choose from {list(TIERS)}")
        return 2
    if args.samples < 1:
        print("--samples must be at least 1")
        return 2
    calls = len(tiers) * (len(SCENARIOS) * args.samples + 2)      # +2 per tier: the prompt-overhead probe
    if calls > args.max_calls:
        print(f"{calls} calls planned, over the budget of {args.max_calls}; lower --samples or --tiers")
        return 2
    if args.dry_run:
        print(f"{len(tiers)} tiers x {len(SCENARIOS)} utterances x {args.samples} samples (+ overhead probe) -> {calls} calls")
        return 0

    from dotenv import load_dotenv
    from openai import OpenAI, OpenAIError       # only the client library: every request below goes to the Databricks Unity Gateway

    load_dotenv(REPO_ROOT / ".env.local", override=False)
    host, token = os.environ.get("DATABRICKS_HOST", "").strip().rstrip("/"), os.environ.get("DATABRICKS_TOKEN", "").strip()
    if not host or not token:
        print("Missing in .env.local: DATABRICKS_HOST and/or DATABRICKS_TOKEN (names only)")
        return 2
    if not host.startswith("http"):
        host = "https://" + host
    plans = {tier: (*instructions_for(tier), instructions_for(tier, with_cues=False)[1]) for tier in tiers}
    client = OpenAI(base_url=f"{host}/ai-gateway/openai/v1", api_key=token, timeout=120.0)

    vocabulary, failed, saved = frozenset(SPOOKY_TAGS), False, {}
    greeting = SCENARIOS[-1]
    try:
        for tier, (model, instructions, plain_instructions) in plans.items():
            overhead = ask(client, model, instructions, greeting)[1] - ask(client, model, plain_instructions, greeting)[1]
            rows, first_story, replies = [], "", []
            for scenario in SCENARIOS:
                for _ in range(args.samples):
                    reply, _tokens = ask(client, model, instructions, scenario)
                    rows.append((scenario, measure(reply, vocabulary, scenario.facts)))
                    replies.append((scenario.key, reply, rows[-1][1]))
                    if scenario.key == "story" and not first_story:
                        first_story = reply
            problems = evaluate(rows, Targets(story_min_words=STORY_MIN_WORDS))
            failed |= bool(problems)
            story = [m.per_100 for s, m in rows if s.kind == "story"]
            factual = [m.per_100 for s, m in rows if s.kind == "factual"]
            total = sum(m.cues for _, m in rows) or 1
            print(f"\n### {tier} — `{model}`  (cue section ≈ +{overhead} input tokens)")
            print(f"- story cues/100 words: median {statistics.median(story):.1f}   factual: median {statistics.median(factual):.1f}")
            print(f"- median reply length (words): story {median_words(rows, 'story'):.0f}   "
                  f"factual {median_words(rows, 'factual'):.0f}   short {median_words(rows, 'short'):.0f}")
            print(f"- distinct cues: {len(set().union(*(m.distinct for _, m in rows)))}   "
                  f"compliance: {sum(m.canonical for _, m in rows) / total:.0%} "
                  f"(exact spelling {sum(m.exact for _, m in rows) / total:.0%})")
            print(f"- stage directions: {sum(m.directions for _, m in rows)}   stacked: {sum(m.stacked for _, m in rows)}   "
                  f"cues inside facts: {sum(m.cues_inside_facts for _, m in rows)}   "
                  f"replies missing a fact: {sum(1 for _, m in rows if not m.facts_verbatim)}")
            print(f"- verdict: {'PASS' if not problems else 'FAIL — ' + '; '.join(problems)}")
            print(f"- sample story: {first_story[:400]!r}")
            saved[tier] = {"model": model, "overhead_input_tokens": overhead, "problems": problems,
                           "replies": [{"scenario": key, "reply": text,
                                        "metrics": {**asdict(m), "distinct": sorted(m.distinct), "per_100": round(m.per_100, 2)}}
                                       for key, text, m in replies]}
    except OpenAIError as exc:
        print(f"\nstopped: a gateway request failed: {failure_summary(exc)}")
        return 3
    finally:
        if args.json and saved:
            Path(args.json).write_text(json.dumps(saved, indent=2, ensure_ascii=False), encoding="utf-8")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
