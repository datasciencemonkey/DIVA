# Agent worker, token tier, and Lakebase tools

## Token / web tier (stdlib, no LiveKit SDK)

A tiny HTTP server serves the browser page and mints LiveKit access tokens. Mint an HS256 JWT yourself (no SDK needed on the serving path) and embed **agent dispatch** so the worker is pulled into the room. Use a **fresh room per visit** — token-embedded dispatch fires only on room creation.

```python
# GET /api/token  -> sign an HS256 JWT for a brand-new room, with agent dispatch
claims = {
    "exp": now + 900, "nbf": now - 10, "iss": LIVEKIT_API_KEY,
    "sub": identity, "name": display_name,               # display name is courtesy only
    "video": {"roomJoin": True, "room": f"call-{uuid}", "canPublish": True, "canSubscribe": True},
    "roomConfig": {"agents": [{"agentName": AGENT_NAME}]},  # MUST match the worker's agent_name
    "metadata": json.dumps({"customer_id": ...}),         # governed identity the agent reads
}
# header {"alg":"HS256","typ":"JWT"} ; sign signing_input with LIVEKIT_API_SECRET (HMAC-SHA256)
```

Bind to `int(os.environ.get("DATABRICKS_APP_PORT") or 8000)`. Keep this tier dependency-light so it starts instantly.

## Agent worker (LiveKit `AgentServer`)

Run it as `python app/agent.py start` (production) — `dev` for local hot-reload, `console` for a terminal mic test.

**Entrypoint ordering matters.** Connect and wait for the participant BEFORE building the `AgentSession`, so you can read the caller's identity from the token and choose the model/prompt per call:

```python
from livekit import agents
from livekit.agents import Agent, AgentServer, AgentSession
from livekit.plugins import deepgram, openai

server = AgentServer()

@server.rtc_session(agent_name="my-agent")   # matches roomConfig.agents in the token
async def entrypoint(ctx: agents.JobContext):
    pool = await create_pool_soft()           # Lakebase; None if unavailable (degrade, don't crash)
    await ctx.connect()
    await ctx.wait_for_participant()           # identity known AFTER this
    meta = read_token_metadata(ctx)            # customer id, etc.

    session = AgentSession(
        stt=deepgram.STT(model="nova-3"),
        llm=openai.responses.LLM(              # Databricks Foundation Model over the AI Gateway
            model=os.environ["LLM_MODEL"],
            api_key=os.environ["DATABRICKS_TOKEN"],
            base_url=f'{os.environ["DATABRICKS_HOST"]}/ai-gateway/openai/v1',
            use_websocket=False, store=False,
        ),
        tts=deepgram.TTS(model="aura-2-andromeda-en"),
        tools=build_tools(pool, session_ctx),
        max_tool_steps=5,
    )
    await session.start(room=ctx.room, agent=Agent(instructions=system_prompt))
    await session.generate_reply(instructions="Greet the caller and ask how you can help.")

if __name__ == "__main__":
    agents.cli.run_app(server)
```

**Why `openai.responses.LLM` (Responses API), not chat completions:** reasoning/served models on the gateway reject function tools on `/chat/completions`; the Responses passthrough supports tool-calling. Every model you route to must be Responses + tools capable. `use_websocket=False`, `store=False`.

VAD/turn-detection: `livekit-plugins-silero` is auto-provisioned; its model is fetched by `python app/agent.py download-files` (run in `start_app.py` before `start`).

## Lakebase-backed tools

Handlers are LiveKit-free (unit-testable); a `build_tools(pool, ctx)` factory wraps them as `function_tool`s. Pass the Lakebase pool in; **fail soft when `pool is None`** so a Lakebase outage degrades instead of crashing the call.

```python
# tools.py  — NOTE: intentionally NO `from __future__ import annotations` (see gotcha)
from dataclasses import dataclass

@dataclass
class SessionContext:
    customer_id: str | None = None

async def _do_lookup(pool, customer_id, query):
    if pool is None or not customer_id:      # degraded OR unidentified -> abstain, don't guess
        return {"records": []}
    return {"records": await run_query(pool, ..., customer_id)}

def build_tools(pool, ctx: SessionContext) -> list:
    from livekit.agents import RunContext, function_tool

    async def record_lookup(context: RunContext, query: str = "") -> dict:
        """Look up the caller's own records. Returns only what exists."""
        return await _do_lookup(pool, ctx.customer_id, query)

    return [function_tool(record_lookup, name="record_lookup",
                          description="Look up the caller's records; if empty, say you couldn't find it.")]
```

### GOTCHA: never `from __future__ import annotations` in a tool module

LiveKit resolves the `context: RunContext` type hint via `typing.get_type_hints()` at session start. With `from __future__ import annotations`, the hint is a **string** that can't resolve the `build_tools`-local `RunContext` → **`NameError` on every LLM turn**. Use eager (real) annotations so the class object binds. The module can still avoid importing LiveKit at top level (import inside `build_tools`).

## Lakebase connection pool

Mint the Postgres credential with the SDK (no static DB password); the app's identity (PAT user) must have access to the Lakebase instance.

```python
from databricks.sdk import WorkspaceClient
from psycopg_pool import AsyncConnectionPool

w = WorkspaceClient()                                   # PAT auth (see deployment.md)
ep = os.environ["LAKEBASE_ENDPOINT"]
host = w.postgres.get_endpoint(ep).as_dict()["status"]["hosts"]["host"]
token = w.postgres.generate_database_credential(ep).token   # short-lived (~1h)
user = w.current_user.me().user_name
conninfo = f"host={host} user={user} dbname={db} password={token} sslmode=require"
pool = AsyncConnectionPool(conninfo, min_size=1, max_size=4, open=False)
await pool.open(timeout=15)
```

Notes:

- **Use `psycopg[binary]`, not `psycopg[c]`** — the Apps env has no C toolchain; the binary wheel just works.
- **Open explicitly:** construct with `open=False`, then `await pool.open(timeout=15)`, so the timeout actually applies (auto-open ignores it and can hang mid-call).
- **Fail soft:** wrap `create_pool()` so any failure returns `None`; tools check `if pool is None`. Never raise into the session.
- If host resolution is flaky, pin `hostaddr` while keeping `host` for TLS SNI.

**Credential expiry (~1h).** `generate_database_credential(...).token` is short-lived. If you create the pool **per call** in the entrypoint (as above), a single call under ~1h is fine — no refresh needed. Only if you **share one pool across the worker's lifetime** do you need a refresh timer, because connections opened with an expired token start failing auth around the 1h mark:

```python
class PoolHolder:
    def __init__(self): self.pool = None
    async def refresh(self):
        fresh = await create_pool()                 # mints a new credential inside
        old, self.pool = self.pool, fresh
        if old is not None:
            asyncio.create_task(_close_after(old, delay=30))   # let in-flight queries drain

async def refresh_loop(holder):                     # start once at worker init
    while True:
        await asyncio.sleep(45 * 60)                # < 1h TTL
        try: await holder.refresh()
        except Exception: pass                      # keep the old pool on failure
```

Tools then read `holder.pool` instead of a fixed `pool`.
