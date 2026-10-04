"""Thin Unity Gateway (OpenAI-compatible) client — auth + base_url + POST.

Used off the hot path (generation, embeddings). Auth via WorkspaceClient so it
works with DATABRICKS_CONFIG_PROFILE locally and Apps-injected creds in prod.
"""
from __future__ import annotations

import requests

_BASE = "/ai-gateway/openai/v1"
_w = None


def _client():
    global _w
    if _w is None:
        from databricks.sdk import WorkspaceClient

        _w = WorkspaceClient()
    return _w


def post(path: str, body: dict, timeout: float = 60.0) -> dict:
    w = _client()
    url = f"{w.config.host.rstrip('/')}{_BASE}{path}"
    headers = w.config.authenticate()
    headers["Content-Type"] = "application/json"
    resp = requests.post(url, headers=headers, json=body, timeout=timeout)
    resp.raise_for_status()
    return resp.json()
