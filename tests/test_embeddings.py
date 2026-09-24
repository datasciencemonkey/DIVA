import src.services.embeddings as emb


def test_embed_texts_calls_gateway_and_returns_vectors(monkeypatch):
    captured = {}

    def fake_post(path, body):
        captured["path"], captured["body"] = path, body
        return {"data": [{"embedding": [0.1, 0.2, 0.3]} for _ in body["input"]]}

    monkeypatch.setattr(emb, "_post", fake_post)
    out = emb.embed_texts(["a", "b"])
    assert out == [[0.1, 0.2, 0.3], [0.1, 0.2, 0.3]]
    assert captured["path"].endswith("/embeddings")
    assert captured["body"]["input"] == ["a", "b"]


def test_to_pgvector_formats_bracketed_literal():
    assert emb.to_pgvector([0.5, -1.0, 2.0]) == "[0.5,-1.0,2.0]"


def test_embed_texts_empty_returns_empty(monkeypatch):
    monkeypatch.setattr(emb, "_post", lambda p, b: {"data": []})
    assert emb.embed_texts([]) == []
