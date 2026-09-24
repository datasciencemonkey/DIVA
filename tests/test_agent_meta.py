from types import SimpleNamespace

from app.agent import _read_meta


def test_read_meta_parses_dataset_and_customer_from_participant():
    room = SimpleNamespace(remote_participants={
        "p": SimpleNamespace(name="Sam", metadata='{"data_generation_id": "G1", "customer_id": "C1"}')})
    name, gid, cid = _read_meta(SimpleNamespace(room=room))
    assert (name, gid, cid) == ("Sam", "G1", "C1")


def test_read_meta_safe_when_absent():
    room = SimpleNamespace(remote_participants={})
    assert _read_meta(SimpleNamespace(room=room)) == ("", "", None)
