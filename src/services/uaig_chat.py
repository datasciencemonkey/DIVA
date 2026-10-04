"""UAIG chat completion returning parsed JSON — for the generator (off hot path)."""
from __future__ import annotations

import json

from src.services.gateway import post as _post


def complete_json(system: str, user: str, model: str) -> dict:
    # No `temperature`: reasoning models (e.g. gpt-5-5) reject a non-default value,
    # and generation doesn't need it. json_object keeps the output parseable.
    resp = _post("/chat/completions", {
        "model": model,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "response_format": {"type": "json_object"},
    })
    return json.loads(resp["choices"][0]["message"]["content"])
