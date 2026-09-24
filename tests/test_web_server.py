import base64
import json
import os


def _load(monkeypatch):
    monkeypatch.setenv("LIVEKIT_URL", "wss://x")
    monkeypatch.setenv("LIVEKIT_API_KEY", "k")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "s")
    import importlib
    import app.web_server as ws
    return importlib.reload(ws)


def _claims(token: str) -> dict:
    payload = token.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    return json.loads(base64.urlsafe_b64decode(payload))


def test_clean_name_strips_non_name_chars(monkeypatch):
    ws = _load(monkeypatch)
    assert ws.clean_name("Sam <script>3") == "Sam script"  # letters/space/-'. kept, digits/<> dropped


def test_token_carries_dataset_and_customer_in_metadata(monkeypatch):
    ws = _load(monkeypatch)
    tok = ws.mint_token(name="Sam", data_generation_id="G1", customer_id="C1")
    claims = _claims(tok["token"])
    assert claims["name"] == "Sam"
    md = json.loads(claims["metadata"])
    assert md == {"data_generation_id": "G1", "customer_id": "C1"}
    assert claims["roomConfig"]["agents"][0]["agentName"]  # agent dispatch embedded
    assert claims["video"]["room"] == tok["roomName"]        # unique room per visit
