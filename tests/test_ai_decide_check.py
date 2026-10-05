"""Offline tests for tools/ai_decide_check.py: the pass/fail decision, --dry-run, and the exit codes with the network
replaced by an `httpx.MockTransport` (no credentials, no live call)."""
import json

import dotenv
import httpx
import pytest

from src.policy.voice_mode import IntentVerdict
from src.services.ai_decide import build_ai_decide_body
from tools import ai_decide_check as chk

HOST, TOKEN = "https://ws.example.com", "dapi-secret-token"
PATH = "/api/2.0/ai-functions/ai-decide"
GOOD = {"response": {"answers": {"voice_mode": {"type": "choice", "choice": "enter", "confidence": 0.9,
                                                 "probabilities": {"enter": 0.9, "exit": 0.0, "none": 0.1}}}},
        "metadata": {"version": "1.0"}}


def _rows(*sources):
    """One (case, verdict) row per source, in the order of the check's own cases."""
    return [(case, IntentVerdict("none" if source != "model" else "enter", 0.9 if source == "model" else 0.0, source))
            for case, source in zip(chk.CASES, sources)]


def _no_network(**kwargs):
    raise AssertionError("no client may be made here")


def _offline(monkeypatch, tmp_path, respond):
    """Credentials in the environment, no .env.local on disk, and every request answered by `respond(n, request)`,
    where n counts the requests made so far (the four cases come first, the raw request last)."""
    monkeypatch.setattr(chk, "ENV_FILE", tmp_path / ".env.local")
    monkeypatch.setenv("DATABRICKS_HOST", HOST)
    monkeypatch.setenv("DATABRICKS_TOKEN", TOKEN)
    monkeypatch.delenv("UG_DECIDE_TIMEOUT_S", raising=False)
    seen, real = [], httpx.AsyncClient

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return respond(len(seen), request)

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: real(transport=httpx.MockTransport(handler), **kwargs))
    return seen


# ---------------------------------------------------------------- the pass/fail decision

def test_the_check_passes_when_the_function_answered_every_case_whatever_the_label():
    assert chk.failures(_rows("model", "model", "model", "model")) == []


def test_the_check_names_each_case_the_function_did_not_answer():
    problems = chk.failures(_rows("model", "timeout", "error", "model"))
    assert len(problems) == 2
    assert chk.CASES[1].utterance in problems[0] and problems[0].endswith(": timeout")
    assert chk.CASES[2].utterance in problems[1] and problems[1].endswith(": error")


@pytest.mark.parametrize("source", ["timeout", "error", "rule", "skipped", "disabled"])
def test_only_a_model_verdict_counts_as_answered(source):
    assert chk.failures(_rows("model", "model", "model", source)) != []


def test_a_check_that_ran_no_case_does_not_pass():
    assert chk.failures([]) != []


def test_the_cases_are_well_formed():
    assert len(chk.CASES) == 4 and chk.CALLS == 5
    assert len({case.utterance for case in chk.CASES}) == len(chk.CASES)
    assert {case.mode for case in chk.CASES} == {"standard", "halloween"}


def test_failure_summary_names_the_class_and_status_but_never_the_message():
    class Rejected(Exception):
        status_code = 403

    request = httpx.Request("POST", HOST + PATH)
    status_error = httpx.HTTPStatusError("MESSAGE-MUST-NOT-BE-PRINTED", request=request,
                                         response=httpx.Response(503, request=request))
    assert chk.failure_summary(Rejected("MESSAGE-MUST-NOT-BE-PRINTED")) == "Rejected (HTTP 403)"
    assert chk.failure_summary(status_error) == "HTTPStatusError (HTTP 503)"
    assert chk.failure_summary(httpx.ConnectError("MESSAGE-MUST-NOT-BE-PRINTED")) == "ConnectError"


# ---------------------------------------------------------------- --dry-run

def test_dry_run_needs_no_credentials_and_prints_the_path_the_first_body_and_the_call_count(capsys, monkeypatch):
    for name in ("DATABRICKS_HOST", "DATABRICKS_TOKEN"):
        monkeypatch.delenv(name, raising=False)

    def boom(*args, **kwargs):
        raise AssertionError("--dry-run must not read .env.local")

    monkeypatch.setattr(dotenv, "load_dotenv", boom)
    monkeypatch.setattr(httpx, "AsyncClient", _no_network)
    assert chk.main(["--dry-run"]) == 0
    out = capsys.readouterr().out
    assert PATH in out
    assert json.loads(out[out.index("{"):out.rindex("}") + 1]) == build_ai_decide_body(*chk.CASES[0])
    assert f"{chk.CALLS} calls" in out


def test_dry_run_prints_neither_the_host_nor_the_token(capsys, monkeypatch):
    monkeypatch.setenv("DATABRICKS_HOST", HOST)
    monkeypatch.setenv("DATABRICKS_TOKEN", TOKEN)
    assert chk.main(["--dry-run"]) == 0
    out = capsys.readouterr().out
    assert TOKEN not in out and "ws.example.com" not in out


# ---------------------------------------------------------------- exit codes

def test_missing_credentials_exit_2_and_name_only_what_is_missing(capsys, monkeypatch, tmp_path):
    monkeypatch.setattr(chk, "ENV_FILE", tmp_path / ".env.local")       # nothing to read
    monkeypatch.setattr(httpx, "AsyncClient", _no_network)
    monkeypatch.setenv("DATABRICKS_HOST", HOST)
    monkeypatch.delenv("DATABRICKS_TOKEN", raising=False)
    assert chk.main([]) == 2
    out = capsys.readouterr().out
    assert "DATABRICKS_TOKEN" in out and "DATABRICKS_HOST" not in out and "ws.example.com" not in out

    monkeypatch.delenv("DATABRICKS_HOST")
    monkeypatch.setenv("DATABRICKS_TOKEN", TOKEN)
    assert chk.main([]) == 2
    out = capsys.readouterr().out
    assert "DATABRICKS_HOST" in out and "DATABRICKS_TOKEN" not in out and TOKEN not in out


def test_every_case_answered_exits_0_and_shows_the_raw_reply(capsys, monkeypatch, tmp_path):
    seen = _offline(monkeypatch, tmp_path, lambda n, request: httpx.Response(200, json=GOOD))
    assert chk.main([]) == 0
    out = capsys.readouterr().out
    assert len(seen) == chk.CALLS and {r.url.path for r in seen} == {PATH}
    assert all(r.headers["authorization"] == f"Bearer {TOKEN}" for r in seen)
    bodies = [json.loads(r.content) for r in seen]
    assert bodies[:-1] == [build_ai_decide_body(*case) for case in chk.CASES]        # the four cases, in order
    assert bodies[-1] == build_ai_decide_body(*chk.CASES[0])                         # then the raw request
    assert out.count("source=model") == len(chk.CASES)
    assert "label=enter conf=0.90" in out and " ms" in out
    assert "raw request: HTTP 200" in out and "['metadata', 'response']" in out
    assert "raw metadata: {'version': '1.0'}" in out
    assert "verdict: PASS" in out
    assert TOKEN not in out and "ws.example.com" not in out


def test_a_case_the_function_did_not_answer_exits_1(capsys, monkeypatch, tmp_path):
    def respond(n, request):
        if n == 2:
            raise httpx.ReadTimeout("slow", request=request)
        if n == 3:
            return httpx.Response(200, json={"response": None, "metadata": {}})
        return httpx.Response(200, json=GOOD)

    _offline(monkeypatch, tmp_path, respond)
    assert chk.main([]) == 1
    out = capsys.readouterr().out
    assert out.count("source=model") == 2 and "source=timeout" in out and "source=error" in out
    assert "verdict: FAIL" in out
    assert TOKEN not in out


def test_an_http_error_fails_every_case_and_the_error_body_is_not_read(capsys, monkeypatch, tmp_path):
    body = {"error_code": "PERMISSION_DENIED", "message": "MESSAGE-MUST-NOT-BE-PRINTED"}
    _offline(monkeypatch, tmp_path, lambda n, request: httpx.Response(403, json=body))
    assert chk.main([]) == 1
    out = capsys.readouterr().out
    assert out.count("source=error") == len(chk.CASES)
    assert "raw request: HTTP 403" in out and "top-level keys" not in out
    assert "MESSAGE-MUST-NOT-BE-PRINTED" not in out and TOKEN not in out


def test_a_failing_raw_request_is_reported_by_class_and_leaves_the_exit_code_alone(capsys, monkeypatch, tmp_path):
    def respond(n, request):
        if n == chk.CALLS:      # the raw request comes after the four cases
            raise httpx.ConnectError(f"MESSAGE-MUST-NOT-BE-PRINTED {request.url}", request=request)
        return httpx.Response(200, json=GOOD)

    _offline(monkeypatch, tmp_path, respond)
    assert chk.main([]) == 0
    out = capsys.readouterr().out
    assert "raw request failed: ConnectError" in out
    assert "MESSAGE-MUST-NOT-BE-PRINTED" not in out and "ws.example.com" not in out
