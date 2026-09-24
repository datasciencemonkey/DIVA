import json

import pytest

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
