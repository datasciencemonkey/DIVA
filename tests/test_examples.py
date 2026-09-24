"""GET /api/examples derives grounded read-aloud prompts from a world's real
generated data (document titles + record kinds). No fabrication; fail-soft."""
import app.web_server as ws


def test_examples_from_derives_grounded_questions():
    qs = ws._examples_from(["Refund Policy", "Baggage Allowance", "Loyalty Program"], ["order"])
    joined = " ".join(qs).lower()
    assert "refund policy" in joined          # a real document title -> a question
    assert "baggage allowance" in joined
    assert "status of my order" in joined     # a real record kind -> a lookup prompt
    assert all(isinstance(q, str) and q.strip() for q in qs)
    assert len(qs) <= 6


def test_examples_from_handles_empty():
    assert ws._examples_from([], []) == []
    assert ws._examples_from(None, None) == []


def test_examples_payload_fail_soft(monkeypatch):
    # No dataset id -> empty, and never touches the DB.
    assert ws._examples_payload("") == {"questions": []}

    # Any pool/query error -> empty (never 500 the UI).
    async def boom():
        raise RuntimeError("lakebase down")
    monkeypatch.setattr(ws, "create_pool", boom)
    assert ws._examples_payload("G1") == {"questions": []}
