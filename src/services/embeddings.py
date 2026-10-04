"""FMAPI embeddings over UAIG (bring-your-own embeddings for lakebase_ann).

EMBED_MODEL / EMBED_DIM verified in docs/discovery/embeddings-and-index-contract.md
(databricks-gte-large-en, dim 1024).
"""
from __future__ import annotations

import os

from src.services.gateway import post as _post

EMBED_MODEL = os.getenv("UG_EMBED_MODEL", "databricks-gte-large-en")
EMBED_DIM = int(os.getenv("UG_EMBED_DIM", "1024"))


def embed_texts(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    resp = _post("/embeddings", {"model": EMBED_MODEL, "input": texts})
    return [d["embedding"] for d in resp["data"]]


def to_pgvector(vec: list[float]) -> str:
    return "[" + ",".join(str(x) for x in vec) + "]"
