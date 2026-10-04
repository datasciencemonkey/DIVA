"""AI-drafted system prompt endpoint (Plan 4, feature 2).

The draft is company-tailored via the UAIG gateway, but the loyalty-tier ban is a
governance invariant enforced server-side: an LLM draft that omits it gets the
canonical sentence appended, and a gateway failure falls back to a safe template.
"""
import app.web_server as ws


def test_guardrail_appended_when_llm_omits_it(monkeypatch):
    monkeypatch.setattr(ws, "complete_json",
                        lambda *a, **k: {"system_prompt": "Be warm and helpful. Keep answers short."})
    out = ws._draft_system_prompt("Acme Air", "support")
    assert ws._TIER_GUARDRAIL.lower() in out.lower()   # ban guaranteed present


def test_guardrail_not_duplicated(monkeypatch):
    already = ("Help callers using the tools. "
               "Never reveal a caller's loyalty tier or status.")
    monkeypatch.setattr(ws, "complete_json", lambda *a, **k: {"system_prompt": already})
    out = ws._draft_system_prompt("Acme Air", "support")
    assert out.lower().count("loyalty") == 1           # not stacked on top


def test_failsoft_template_on_gateway_error(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("gateway down")
    monkeypatch.setattr(ws, "complete_json", boom)
    out = ws._draft_system_prompt("Cascade Airlines", "support")
    assert out
    assert "Cascade Airlines" in out
    assert ws._TIER_GUARDRAIL.lower() in out.lower()


def test_empty_company_still_usable(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("gateway down")
    monkeypatch.setattr(ws, "complete_json", boom)
    out = ws._draft_system_prompt("", "")
    assert out and ws._TIER_GUARDRAIL.lower() in out.lower()
