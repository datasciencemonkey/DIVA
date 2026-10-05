"""Live check: does the Databricks `ai_decide` function answer, the way the agent calls it?

Sends four scripted caller turns through the repo's own `DecideClient`, which makes the exact request the agent makes:
POST {host}/api/2.0/ai-functions/ai-decide, as the official API reference documents it
(https://docs.databricks.com/api/ai-functions/v1/ai-decide). Prints one line per case: the utterance, the current mode,
the label, the confidence, the verdict source and the latency in ms. Then it makes one raw request to the same endpoint
and prints the HTTP status, the top-level keys of the reply, `response.answers.voice_mode` and `metadata`, so a change in
the shape of the reply shows up here first. The raw request is for the eyes only: the exit code looks at the four cases.

A case passes when the function answered it (verdict source "model"; any label counts, `none` included). A timeout, an
error or a reply the parser cannot use fails it: in a call the agent would fail closed and stay in the current voice.
The function must be enabled on the workspace (admin -> Previews). The timeout is the agent's own (UG_DECIDE_TIMEOUT_S,
default 3.0 s).

Credentials come from the gitignored .env.local and are never printed, and neither is the host. A failed request prints
only the exception's class and HTTP status, never its message or URL.

    uv run --frozen python tools/ai_decide_check.py --dry-run
    uv run --frozen python tools/ai_decide_check.py
    UG_DECIDE_TIMEOUT_S=10 uv run --frozen python tools/ai_decide_check.py     # a one-off look with a longer timeout

Exit codes: 0 every case was answered by the function. 1 at least one case was not (timeout, error or unusable reply).
2 missing .env.local values (DATABRICKS_HOST / DATABRICKS_TOKEN).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import NamedTuple

import httpx

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.policy.voice_mode import IntentVerdict  # noqa: E402
# The tool checks what the agent calls, so it reads the module's own endpoint path and host handling.
from src.services.ai_decide import (  # noqa: E402
    _AI_DECIDE_PATH, DecideClient, _base_url, _dig, build_ai_decide_body,
)

ENV_FILE = REPO_ROOT / ".env.local"
RAW_TIMEOUT_S = 10.0     # the raw request is a diagnostic: give a slow function time to show its reply


class Case(NamedTuple):
    utterance: str
    agent_said: str      # the agent's last line: context only
    mode: str            # the voice the call is in now


CASES: tuple[Case, ...] = (
    Case("switch to the spooky voice", "How can I help you today?", "standard"),
    Case("okay that is enough, back to the normal voice please", "Mwahaha, what else would you like to know?", "halloween"),
    Case("what time do you open on Halloween?", "How can I help you today?", "standard"),   # a topic, not a voice request
    Case("where is my order?", "How can I help you today?", "standard"),
)
CALLS = len(CASES) + 1   # the four cases, then one raw request


def failures(rows: Sequence[tuple[Case, IntentVerdict]]) -> list[str]:
    """Why the check fails; an empty list means the function answered every case.

    "Answered" is the verdict's source. "model" is a reply the parser accepted, whatever its label. "timeout" and
    "error" are the client failing closed. A check that ran no case proves nothing, so it fails too."""
    if not rows:
        return ["no case was run"]
    return [f"{case.utterance!r} [{case.mode}]: {verdict.source}" for case, verdict in rows if verdict.source != "model"]


def case_line(case: Case, verdict: IntentVerdict, ms: float) -> str:
    return (f"- {case.utterance[:52]!r} [{case.mode}] -> label={verdict.intent} conf={verdict.confidence:.2f} "
            f"source={verdict.source} {ms:.0f} ms")


def failure_summary(exc: Exception) -> str:
    """All that is safe to print about a failed request: the class and the HTTP status, never the message or URL."""
    status = getattr(getattr(exc, "response", None), "status_code", None) or getattr(exc, "status_code", None)
    return type(exc).__name__ + (f" (HTTP {status})" if status else "")


def print_plan() -> None:
    first = CASES[0]
    print(f"endpoint: POST {_AI_DECIDE_PATH}")
    print(f"request body for the first case ({first.utterance!r}):")
    print(json.dumps(build_ai_decide_body(*first), indent=2, ensure_ascii=False))
    print(f"{CALLS} calls: {len(CASES)} cases through DecideClient + 1 raw request")


async def raw_request(host: str, token: str, case: Case) -> None:
    """One request to the same endpoint, read directly: what the function itself says."""
    try:
        async with httpx.AsyncClient(timeout=RAW_TIMEOUT_S) as http:
            reply = await http.post(_base_url(host) + _AI_DECIDE_PATH, json=build_ai_decide_body(*case),
                                    headers={"Authorization": f"Bearer {token}"})
    except Exception as exc:  # noqa: BLE001 - say what failed, never what it carried
        print(f"raw request failed: {failure_summary(exc)}")
        return
    print(f"raw request: HTTP {reply.status_code}")
    if not 200 <= reply.status_code < 300:
        return
    try:
        payload = reply.json()
    except ValueError:
        print("raw request: the reply body is not JSON")
        return
    if not isinstance(payload, dict):
        print(f"raw request: the reply is a {type(payload).__name__}, not an object")
        return
    print("raw reply top-level keys:", sorted(payload))
    print("raw response.answers.voice_mode:", _dig(payload, "response", "answers", "voice_mode"))
    print("raw metadata:", payload.get("metadata"))


async def run(host: str, token: str) -> int:
    client = DecideClient(host, token)
    print(f"{client.label}: POST {_AI_DECIDE_PATH}, {len(CASES)} cases, timeout {client.timeout_s} s")
    rows: list[tuple[Case, IntentVerdict]] = []
    try:
        for case in CASES:
            verdict, ms = await client.classify(*case)
            rows.append((case, verdict))
            print(case_line(case, verdict, ms))
    finally:
        await client.aclose()
    await raw_request(host, token, CASES[0])
    problems = failures(rows)
    print(f"verdict: {'PASS' if not problems else 'FAIL: ' + '; '.join(problems)}")
    return 1 if problems else 0


def parse_args(argv):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--dry-run", action="store_true",
                   help="print the endpoint path, the first request body and the call count; no credentials, no network")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    if args.dry_run:
        print_plan()
        return 0

    from dotenv import load_dotenv

    load_dotenv(ENV_FILE, override=False)
    values = {name: os.environ.get(name, "").strip() for name in ("DATABRICKS_HOST", "DATABRICKS_TOKEN")}
    missing = [name for name, value in values.items() if not value]
    if missing:
        print(f"Missing in .env.local: {', '.join(missing)} (names only)")
        return 2
    return asyncio.run(run(values["DATABRICKS_HOST"], values["DATABRICKS_TOKEN"]))


if __name__ == "__main__":
    raise SystemExit(main())
