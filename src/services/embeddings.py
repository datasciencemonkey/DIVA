"""FMAPI embeddings over Unity Gateway (bring-your-own embeddings for lakebase_ann).

EMBED_MODEL verified in docs/discovery/embeddings-and-index-contract.md (gte-large-en, dim 1024; the schema
hard-codes vector(1024)). The default is the Unity Gateway name that .env.example and app.yaml use.
"""
from __future__ import annotations

import os

from src.services.gateway import post as _post

EMBED_MODEL = os.getenv("UG_EMBED_MODEL", "system.ai.gte-large-en")


def embed_texts(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    resp = _post("/embeddings", {"model": EMBED_MODEL, "input": texts})
    return [d["embedding"] for d in resp["data"]]


def to_pgvector(vec: list[float]) -> str:
    return "[" + ",".join(str(x) for x in vec) + "]"
