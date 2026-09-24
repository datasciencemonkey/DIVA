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
import asyncio
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from src.generate import generate_dataset
from src.services.db import SCHEMA, _run_query, create_pool

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


# --- In-app dataset generation (spec Plan 4) --------------------------------
#
# Generation runs in the web tier on a background daemon thread; the HTTP handler
# thread never blocks on it. Progress is narrated into an in-memory job store
# (dict + lock), which the SPA polls via GET /api/generate/status.

GEN_MODEL = os.getenv("UG_GEN_MODEL", "system.ai.gpt-5-4")

_JOBS: dict[str, dict] = {}
_JOBS_LOCK = threading.Lock()


def _new_job(job_id: str) -> None:
    """Seed a job as 'queued' (called synchronously before the worker is spawned)."""
    with _JOBS_LOCK:
        _JOBS[job_id] = {"stage": "queued", "detail": {}, "done": False, "error": None}


def _store_progress(job_id: str, stage: str, detail: dict) -> None:
    """Record a (stage, detail) narration tick from generate_dataset's callback."""
    with _JOBS_LOCK:
        job = _JOBS.setdefault(job_id, {"done": False, "error": None})
        job["stage"] = stage
        job["detail"] = detail
        if stage == "failed":
            job["done"] = True
            job["error"] = detail.get("error")


def _store_ready(job_id: str, data_generation_id: str, customers: list[dict]) -> None:
    """Attach the ready payload (customers for the tier picker) and mark done."""
    with _JOBS_LOCK:
        job = _JOBS.setdefault(job_id, {"detail": {}})
        job["stage"] = "ready"
        job["data_generation_id"] = data_generation_id
        job["customers"] = customers
        job["done"] = True
        job["error"] = None


def _store_error(job_id: str, message: str) -> None:
    """Mark a job failed with a message (defensive net around the worker)."""
    with _JOBS_LOCK:
        job = _JOBS.setdefault(job_id, {"detail": {}})
        job["stage"] = "failed"
        job["error"] = message
        job["done"] = True


def _status_payload(job_id: str) -> dict | None:
    """Return a copy of the job entry (so callers can't mutate the store), or None."""
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        return dict(job) if job is not None else None


async def _generate_job_async(job_id: str, company: str, role: str, system_prompt: str) -> None:
    pool = None
    try:
        pool = await create_pool()
        gid = await generate_dataset(
            company, role, system_prompt, model=GEN_MODEL, pool=pool,
            progress=lambda stage, detail: _store_progress(job_id, stage, detail))
        # On ready, fetch the generated customers so the SPA can offer tier picks.
        customers = await _run_query(
            pool,
            f"SELECT customer_id, display_name, loyalty_tier FROM {SCHEMA}.customers "
            "WHERE data_generation_id=%(g)s ORDER BY loyalty_tier",
            {"g": gid})
        _store_ready(job_id, gid, customers)
    except Exception as exc:  # noqa: BLE001 — surface any failure into the job store
        _store_error(job_id, str(exc))
    finally:
        if pool is not None:
            await pool.close()


def _run_generate_job(job_id: str, company: str, role: str, system_prompt: str) -> None:
    """Daemon-thread entrypoint: run the async generation job on a fresh event loop."""
    asyncio.run(_generate_job_async(job_id, company, role, system_prompt))


async def _fetch_datasets() -> list[dict]:
    pool = await create_pool()
    try:
        rows = await _run_query(
            pool,
            f"SELECT data_generation_id, company_name, status, doc_count, customer_count, "
            f"created_at FROM {SCHEMA}.datasets WHERE status='ready' ORDER BY created_at DESC")
        out = []
        for r in rows:
            created = r.get("created_at")
            out.append({
                "data_generation_id": r.get("data_generation_id"),
                "company_name": r.get("company_name"),
                "status": r.get("status"),
                "doc_count": r.get("doc_count"),
                "customer_count": r.get("customer_count"),
                "created_at": created.isoformat() if hasattr(created, "isoformat") else created,
            })
        return out
    finally:
        await pool.close()


def _datasets_payload() -> dict:
    """List ready datasets, newest first. Fail-soft: any error -> empty list (never 500)."""
    try:
        return {"datasets": asyncio.run(_fetch_datasets())}
    except Exception:  # noqa: BLE001 — degraded mode: never break the picker UI
        return {"datasets": []}


class Handler(SimpleHTTPRequestHandler):
    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):  # noqa: N802 (stdlib naming convention)
        parsed = urlparse(self.path)
        if parsed.path == "/api/generate":
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length > 0 else b""
            try:
                data = json.loads(raw or b"{}")
            except (ValueError, TypeError):
                data = {}
            company = str(data.get("company", "")).strip()
            role = str(data.get("role", "")).strip()
            system_prompt = str(data.get("system_prompt", "")).strip()
            job_id = secrets.token_hex(8)
            _new_job(job_id)
            threading.Thread(
                target=_run_generate_job,
                args=(job_id, company, role, system_prompt),
                daemon=True,
            ).start()
            self._send_json(202, {"job_id": job_id})
            return
        self.send_error(404, "Not Found")

    def do_GET(self):  # noqa: N802 (stdlib naming convention)
        parsed = urlparse(self.path)
        if parsed.path == "/api/generate/status":
            qs = parse_qs(parsed.query)
            job_id = (qs.get("job_id") or [""])[0]
            payload = _status_payload(job_id)
            if payload is None:
                self._send_json(404, {"error": "unknown job"})
            else:
                self._send_json(200, payload)
            return
        if parsed.path == "/api/datasets":
            self._send_json(200, _datasets_payload())
            return
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
