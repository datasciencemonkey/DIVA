# Deploying the voice agent to Databricks Apps

Single Databricks App, one container, two processes (web tier + worker) via `start_app.py`.
All commands take `--profile <PROFILE>` — never auto-select a profile.

## Prerequisites

- Databricks CLI ≥ 1.0 (`databricks --version`), authenticated (OAuth for `apps logs`).
- A LiveKit Cloud project (URL + API key/secret) and your STT/TTS provider key.
- A **PAT** for the app to authenticate as (the app runs as this user; see auth note below).
- Repo layout: `app/web_server.py`, `app/agent.py`, plus `start_app.py`, `app.yaml`, `requirements.txt`, `agent-requirements.txt` at the root.

## Two requirements files (this is the crux)

| File | Installed by | Holds |
|---|---|---|
| `requirements.txt` (root) | the platform, into the shared env, before your command runs | ONLY what the **web tier** imports (keep it tiny, loosely pinned) |
| `agent-requirements.txt` | `start_app.py`, into `/tmp/agent-venv` at boot | the full **LiveKit worker** stack |

The heavy LiveKit stack must NOT go in the platform env (it collides with preinstalled packages). Keep the root `requirements.txt` minimal, e.g.:

```
requests
psycopg[binary]
psycopg-pool
databricks-sdk==0.133.0
```

### Resolve `agent-requirements.txt` for the Apps Python (3.11), not 3.12

**The single most expensive trap.** Databricks Apps run **Python 3.11**. If your `pyproject.toml` is `requires-python>=3.12`, a `uv export` of the lock pins `numpy==2.5.x` (which requires py≥3.12). The `/tmp/agent-venv` install then fails, the worker never starts, and calls connect with **no agent joining**. Compile the direct deps FOR 3.11 instead:

```bash
uv pip compile agent-requirements.in --python-version 3.11 -o agent-requirements.txt
# verify: grep -i '^numpy==' agent-requirements.txt   # should be 2.4.x, not 2.5.x
```

(`agent-requirements.in` = your direct worker deps — see `templates/agent-requirements.in`.)

## Secrets

```bash
SCOPE=my-voice-agent
databricks secrets create-scope "$SCOPE" --profile "$PROFILE"
# push each value WITHOUT echoing it (read from your gitignored .env.local):
val=$(grep '^LIVEKIT_URL=' .env.local | cut -d= -f2-)
databricks secrets put-secret "$SCOPE" livekit_url --string-value "$val" --profile "$PROFILE"
# ...repeat for livekit_api_key, livekit_api_secret, deepgram_api_key, databricks_token
```

## Create the app + attach secret resources

`app.yaml`'s `valueFrom: livekit-url` references an app **resource** named `livekit-url` that maps to a scope/key. Attach them (compute must be `ACTIVE`/`STOPPED` to update):

```bash
databricks apps create my-voice-agent --description "..." --profile "$PROFILE"
# resources.json: [{"name":"livekit-url","secret":{"scope":"my-voice-agent","key":"livekit_url","permission":"READ"}}, ...]
databricks apps update my-voice-agent --json @resources.json --profile "$PROFILE"
```

Grant the app's service principal **READ on the scope** (the platform reads secrets as the SP to inject them):

```bash
SP=$(databricks apps get my-voice-agent -o json --profile "$PROFILE" | python3 -c "import sys,json;print(json.load(sys.stdin)['service_principal_client_id'])")
databricks secrets put-acl my-voice-agent "$SP" READ --profile "$PROFILE"
```

## Auth note: PAT vs service principal

`start_app.py` pops `DATABRICKS_CLIENT_ID`/`DATABRICKS_CLIENT_SECRET` so the injected **PAT** (`DATABRICKS_TOKEN`) is the SDK's single auth method — the app then acts as your user everywhere (Lakebase, Unity Gateway, LLM), matching local dev and avoiding SP-permission gaps. Apps inject SP OAuth too, and the SDK errors on ambiguous auth if both are present. (A fully SP-based setup is possible but requires granting the SP Lakebase + gateway access.)

## Upload + deploy

**Use `databricks sync`, not `workspace import-dir`.** Sync (no `--watch`) is one-shot and is the path Apps deploy expects.

```bash
# grant the SP access to the source folder FIRST, or deploy dies at "Preparing source code"
OID=$(databricks workspace get-status "$WSP" -o json --profile "$PROFILE" | python3 -c "import sys,json;print(json.load(sys.stdin)['object_id'])")
databricks permissions update directories "$OID" \
  --json "{\"access_control_list\":[{\"service_principal_name\":\"$SP\",\"permission_level\":\"CAN_MANAGE\"}]}" --profile "$PROFILE"

# launch the app compute FIRST
databricks apps start my-voice-agent --profile "$PROFILE"

# upload (stage a clean dir with just app/, src/, *.py, *.yaml, *requirements*; no .venv/tests/docs)
databricks sync ./stage "$WSP" --full --profile "$PROFILE"

# deploy
databricks apps deploy my-voice-agent --source-code-path "$WSP" --profile "$PROFILE"
```

`WSP` = `/Workspace/Users/<you>/databricks_apps/my-voice-agent`.

## Verify

```bash
databricks apps get my-voice-agent -o json --profile "$PROFILE"   # app_status RUNNING, deployment SUCCEEDED
databricks apps logs my-voice-agent --tail-lines 100 --profile "$PROFILE"
```

Look for, in order: `[web] ... listening on 0.0.0.0:8000` (web tier up), then ~30–60s later `installing agent deps` → `download-files` → **`registered worker` … agent_name … url: wss://…livekit.cloud`**. Only after `registered worker` will a call get an agent. If you also see `received job request`, a call was dispatched successfully.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| deploy fails at "Preparing source code" | app SP lacks read on source folder | grant SP `CAN_MANAGE` on the workspace source dir |
| deploy `failed unexpectedly` ~7s, no logs, even for a trivial hello-world | platform-side build/launch issue | retry later; confirm with a bare probe app; don't keep editing the app |
| `agent-boot` step failed: numpy `requires Python>=3.12` | `agent-requirements.txt` resolved for 3.12 | recompile with `--python-version 3.11` |
| web tier crashes on import | a module-level dep missing from root `requirements.txt` | add it (only the web tier's imports) |
| SDK "more than one authorization method configured" | PAT + SP creds both in env | ensure the launcher pops `DATABRICKS_CLIENT_ID/SECRET` |
| worker keeps restarting | outbound to LiveKit Cloud / STT provider blocked | verify Apps egress reaches `wss://…livekit.cloud` and the provider |
