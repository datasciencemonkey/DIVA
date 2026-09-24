"""UG Voice Studio frontend server: stdlib only, zero pip deps.

Serves web/public/ (the page + vendored livekit-client) and mints LiveKit
access tokens on GET /api/token with agent dispatch embedded.  The token is
a plain HS256 JWT signed with the LiveKit API secret — no LiveKit SDK needed
on the serving path.

Adapted from ReferenceApp app/web_server.py (same token-minting logic, same
SimpleHTTPRequestHandler pattern). Changes: clean_name (was clean_first_name),
clean_id replaces clean_route, mint_token now carries JSON metadata with
data_generation_id + customer_id, /api/token reads ?dataset=&customer=&name=.
"""
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

ROOT = Path(__file__).parent
PUBLIC = ROOT / "web" / "public"


def _load_env_local() -> None:
    """Load .env.local for local development parity; no-op on Databricks Apps.

    Checks app/.env.local first, then the repo-root .env.local (where agent.py
    reads it via parent.parent), so one repo-root file feeds both entrypoints.
    """
    for cand in (ROOT / ".env.local", ROOT.parent / ".env.local"):
        if not cand.exists():
            continue
        for line in cand.read_text().splitlines():
            m = re.match(r"^\s*(?:export\s+)?([A-Z_][A-Z0-9_]*)\s*=\s*(.*?)\s*$", line)
            if not m:
                continue
            key, val = m.group(1), m.group(2)
            val = re.sub(r"^(['\"])(.*)\1$", r"\2", val)
            os.environ.setdefault(key, val)
        return


_load_env_local()

LIVEKIT_URL = os.environ.get("LIVEKIT_URL", "")
LIVEKIT_API_KEY = os.environ.get("LIVEKIT_API_KEY", "")
LIVEKIT_API_SECRET = os.environ.get("LIVEKIT_API_SECRET", "")
AGENT_NAME = os.environ.get("AGENT_NAME", "ug-agent")
PORT = int(os.environ.get("DATABRICKS_APP_PORT") or os.environ.get("PORT") or 8000)

if not (LIVEKIT_URL and LIVEKIT_API_KEY and LIVEKIT_API_SECRET):
    raise SystemExit("Missing LIVEKIT_URL / LIVEKIT_API_KEY / LIVEKIT_API_SECRET.")


def _b64url(raw: bytes) -> bytes:
    return base64.urlsafe_b64encode(raw).rstrip(b"=")


def clean_name(raw: str) -> str:
    """Sanitize a browser-supplied display name to courtesy-display characters only.

    The name is COURTESY ONLY (never a lookup key or an auth grant). Keep letters
    (any language), spaces, hyphen, apostrophe, and period; drop everything else
    (digits, punctuation, control chars, newlines); collapse whitespace; cap length.
    Dropping non-name characters also neutralizes prompt-injection via the name.
    """
    name = unquote(raw or "").strip()
    name = "".join(ch for ch in name if ch.isalpha() or ch in " -'.")
    name = re.sub(r"\s+", " ", name).strip()
    return name[:40]


def clean_id(raw: str) -> str:
    """Sanitize a dataset or customer id to alphanumeric + hyphen, capped at 64 chars.

    Strips everything outside [A-Za-z0-9-] and truncates to 64 characters.
    Prevents injection of arbitrary strings into the JWT metadata payload.
    """
    cleaned = re.sub(r"[^A-Za-z0-9-]", "", unquote(raw or ""))
    return cleaned[:64]


def mint_token(name: str = "", data_generation_id: str = "", customer_id: str = "") -> dict:
    """Mint a short-TTL LiveKit access token for a fresh room with agent dispatch.

    A unique room name per visit is load-bearing: token-embedded dispatch fires
    only on room creation (same contract as the reference web_server.py).
    The metadata JSON carries data_generation_id + customer_id so the agent can
    derive the governed loyalty tier without relying on anything the caller says.
    """
    now = int(time.time())
    room = f"ug-{format(int(time.time() * 1000), 'x')}-{secrets.token_hex(2)}"
    identity = f"caller-{secrets.token_hex(3)}"
    claims = {
        "exp": now + 900,
        "nbf": now - 10,
        "iss": LIVEKIT_API_KEY,
        "sub": identity,
        "video": {
            "roomJoin": True,
            "room": room,
            "canPublish": True,
            "canSubscribe": True,
            "canPublishData": True,
        },
        "roomConfig": {"agents": [{"agentName": AGENT_NAME}]},
        "metadata": json.dumps({
            "data_generation_id": clean_id(data_generation_id),
            "customer_id": clean_id(customer_id),
        }),
    }
    if name:
        claims["name"] = name  # participant display name; courtesy only, never auth
    header = {"alg": "HS256", "typ": "JWT"}
    signing_input = (
        _b64url(json.dumps(header, separators=(",", ":")).encode())
        + b"."
        + _b64url(json.dumps(claims, separators=(",", ":")).encode())
    )
    sig = hmac.new(LIVEKIT_API_SECRET.encode(), signing_input, hashlib.sha256).digest()
    token = (signing_input + b"." + _b64url(sig)).decode()
    return {
        "serverUrl": LIVEKIT_URL,
        "roomName": room,
        "identity": identity,
        "token": token,
        "name": name,
        "data_generation_id": data_generation_id,
        "customer_id": customer_id,
    }


class Handler(SimpleHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 (stdlib naming convention)
        parsed = urlparse(self.path)
        if parsed.path == "/api/token":
            qs = parse_qs(parsed.query)
            raw_name = (qs.get("name") or [""])[0]
            raw_dataset = (qs.get("dataset") or [""])[0]
            raw_customer = (qs.get("customer") or [""])[0]
            body = json.dumps(
                mint_token(
                    clean_name(raw_name),
                    clean_id(raw_dataset),
                    clean_id(raw_customer),
                )
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        super().do_GET()

    def end_headers(self):
        if self.path.split("?")[0] in ("/", "/index.html"):
            self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, fmt, *args):
        print(f"[web] {self.address_string()} {fmt % args}", flush=True)


def main() -> None:
    handler = partial(Handler, directory=str(PUBLIC))
    server = ThreadingHTTPServer(("0.0.0.0", PORT), handler)
    print(
        f'[web] UG Voice Studio frontend listening on 0.0.0.0:{PORT} (agent: "{AGENT_NAME}")',
        flush=True,
    )
    server.serve_forever()


if __name__ == "__main__":
    main()
