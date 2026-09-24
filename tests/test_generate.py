import json

import pytest

import src.generate as gen
from src.generate import build_rows, GenSpec

_LLM = {
    "documents": [
        {"title": "Returns policy", "chunk_text": "Items can be returned within 30 days."},
        {"title": "Shipping", "chunk_text": "Standard shipping is 3-5 days."},
    ],
    "customers": [
        {"display_name": "Ada", "loyalty_tier": "Standard"},
        {"display_name": "Grace", "loyalty_tier": "Premium"},
        {"display_name": "Kat", "loyalty_tier": "VIP"},
    ],
    "records": [
        {"customer_index": 2, "kind": "order", "fields": {"item": "widget"}, "status": "shipped"},
    ],
}


def test_build_rows_tags_everything_with_one_generation_id():
    spec = GenSpec(company="Acme", role="support", system_prompt="help", data_generation_id="G1")
    rows = build_rows(spec, _LLM)
    ids = {r["data_generation_id"] for r in rows.documents + rows.customers + rows.records}
    assert ids == {"G1"}


def test_build_rows_covers_all_three_tiers():
    spec = GenSpec("Acme", "support", "help", "G1")
    tiers = {c["loyalty_tier"] for c in build_rows(spec, _LLM).customers}
    assert tiers == {"Standard", "Premium", "VIP"}


def test_build_rows_marks_documents_synthetic():
    spec = GenSpec("Acme", "support", "help", "G1")
    docs = build_rows(spec, _LLM).documents
    assert all(json.loads(d["metadata"])["synthetic"] is True for d in docs)


def test_build_rows_rejects_missing_tier_coverage():
    bad = {**_LLM, "customers": [{"display_name": "X", "loyalty_tier": "Standard"}]}
    with pytest.raises(ValueError):
        build_rows(GenSpec("Acme", "support", "help", "G1"), bad)


# --- generate_dataset progress hook (spec §"Backend change") -----------------

# A minimal-but-valid LLM draft: 5 documents, all three tiers, a couple records.
_GEN_LLM = {
    "documents": [
        {"title": "Returns", "chunk_text": "Return items within 30 days."},
        {"title": "Shipping", "chunk_text": "Standard shipping is 3-5 days."},
        {"title": "Warranty", "chunk_text": "One-year limited warranty."},
        {"title": "Support", "chunk_text": "Chat support is available 24/7."},
        {"title": "Refunds", "chunk_text": "Refunds post within 5 business days."},
    ],
    "customers": [
        {"display_name": "Ada", "loyalty_tier": "Standard"},
        {"display_name": "Grace", "loyalty_tier": "Premium"},
        {"display_name": "Kat", "loyalty_tier": "VIP"},
    ],
    "records": [
        {"customer_index": 2, "kind": "order", "fields": {"item": "widget"}, "status": "shipped"},
        {"customer_index": 0, "kind": "ticket", "fields": {"issue": "late"}, "status": "open"},
    ],
}


class _FakeCursor:
    """Minimal async cursor: every statement here is INSERT/UPDATE (no result set)."""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, sql, params=None):
        return None

    @property
    def description(self):
        return None  # no result set -> _run_query returns []

    async def fetchall(self):
        return []


class _FakeConn:
    def __init__(self, cursor):
        self._cursor = cursor

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def cursor(self):
        return self._cursor


class _FakePool:
    def __init__(self):
        self._cursor = _FakeCursor()

    def connection(self):
        return _FakeConn(self._cursor)


def _patch_llm_and_embed(monkeypatch, llm=_GEN_LLM):
    monkeypatch.setattr(gen, "complete_json", lambda system, user, model: llm)
    monkeypatch.setattr(gen, "embed_texts", lambda texts: [[0.1, 0.2] for _ in texts])


async def test_generate_dataset_emits_progress_stages_in_order(monkeypatch):
    _patch_llm_and_embed(monkeypatch)
    events: list[tuple[str, dict]] = []
    gid = await gen.generate_dataset(
        "Acme", "support", "help", model="m", pool=_FakePool(),
        progress=lambda stage, detail: events.append((stage, detail)))

    assert [s for s, _ in events] == ["drafting", "drafted", "embedding", "saving", "ready"]
    stages = dict(events)
    assert stages["drafting"] == {}
    assert stages["drafted"] == {"documents": 5, "customers": 3, "records": 2}
    assert stages["embedding"] == {"count": 5}
    assert stages["saving"] == {}
    assert stages["ready"] == {"data_generation_id": gid, "doc_count": 5, "customer_count": 3}


async def test_generate_dataset_emits_failed_before_reraising(monkeypatch):
    def boom(system, user, model):
        raise RuntimeError("llm unavailable")

    monkeypatch.setattr(gen, "complete_json", boom)
    events: list[tuple[str, dict]] = []
    with pytest.raises(RuntimeError):
        await gen.generate_dataset(
            "Acme", "support", "help", model="m", pool=_FakePool(),
            progress=lambda stage, detail: events.append((stage, detail)))

    assert events[0][0] == "drafting"
    assert events[-1] == ("failed", {"error": "llm unavailable"})


async def test_generate_dataset_survives_progress_callback_exceptions(monkeypatch):
    _patch_llm_and_embed(monkeypatch)

    def bad(stage, detail):
        raise ValueError("callback boom")

    gid = await gen.generate_dataset(
        "Acme", "support", "help", model="m", pool=_FakePool(), progress=bad)
    assert isinstance(gid, str) and gid  # generation still completed


async def test_generate_dataset_progress_is_optional(monkeypatch):
    _patch_llm_and_embed(monkeypatch)
    gid = await gen.generate_dataset("Acme", "support", "help", model="m", pool=_FakePool())
    assert isinstance(gid, str) and gid
