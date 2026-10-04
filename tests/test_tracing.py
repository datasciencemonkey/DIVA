from types import SimpleNamespace
from app.tracing import fill_ug_metadata


def test_fill_sets_ug_attrs_without_leaking_pii():
    enr = {}
    bind = SimpleNamespace(data_generation_id="G1", company="Acme", tier="VIP",
                           model="databricks-gpt-6-sol", customer_id="C1", courtesy_name="Grace")
    sess = SimpleNamespace(last_retrieval={"kind": "semantic", "hits": 3})
    fill_ug_metadata(enr, bind, sess)
    assert enr["ug.data_generation_id"] == "G1"
    assert enr["ug.company"] == "Acme"
    assert enr["ug.loyalty_tier"] == "VIP"
    assert enr["ug.routed_model"] == "databricks-gpt-6-sol"
    # courtesy name is PII — must NOT be emitted to the trace
    assert "Grace" not in str(enr)


def test_fill_is_fail_soft_on_bad_input():
    fill_ug_metadata({}, None, None)  # must not raise
