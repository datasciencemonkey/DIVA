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

Notes on the compile:

- **`-o` on an existing file keeps its pins as preferences.** A recompile moves only what your `.in` edits force (numpy stays put), and re-running the same command is idempotent. Add `--upgrade` to take newer versions on purpose.
- **A `livekit-agents` bump moves its companions.** 1.8.x hard-pins `livekit==1.1.18`, needs `livekit-api>=1.2.0`, and requires `openai<3` (replacing an `openai` 3.x from an earlier resolve). Bump `livekit`, `livekit-api` and every `livekit-plugins-*` in the `.in` together, or the resolve fails.
- **Apps run Linux; compiling on a Mac is fine** when the same compile with `--python-platform linux`, run on a copy of the output so the same pins are preferred, produces identical pins.
- **Verify before you deploy:** the pins are 3.11-valid and the stack really installs and imports there.

```bash
grep -iE '^(numpy|livekit-plugins-[a-z]+)==' agent-requirements.txt      # numpy 2.4.x, every plugin you use pinned
uv venv /tmp/v311 --python 3.11
uv pip install --python /tmp/v311/bin/python -r agent-requirements.txt    # what start_app.py does at boot
/tmp/v311/bin/python -c "import livekit.agents, livekit.plugins.elevenlabs"
```

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

### Adding another secret later (example: an ElevenLabs key)

A new `valueFrom:` line in `app.yaml` is not enough on its own. The key needs a secret in the scope **and** an app resource that points at it, and both are created outside the repo by whoever owns the key. Attach the resource **before** you deploy the `app.yaml` that references it: `valueFrom` only resolves once the resource exists.

```bash
# 1. the secret (value read from the gitignored .env.local, never echoed)
val=$(grep '^ELEVEN_API_KEY=' .env.local | cut -d= -f2-)
databricks secrets put-secret "$SCOPE" elevenlabs_api_key --string-value "$val" --profile "$PROFILE"

# 2. the app resource. Send the FULL resources list so the existing ones are not dropped:
#    start from `databricks apps get my-voice-agent -o json` and append
#    {"name":"elevenlabs-api-key","secret":{"scope":"my-voice-agent","key":"elevenlabs_api_key","permission":"READ"}}
databricks apps update my-voice-agent --json @resources.json --profile "$PROFILE"
```

```yaml
# app.yaml -- the name after valueFrom is the RESOURCE name, not the scope key
- name: ELEVEN_API_KEY
  valueFrom: elevenlabs-api-key
```

The app's service principal already has READ on the scope, so a new key in the same scope needs no new ACL. Keep non-secret knobs (model, fallback voice, thresholds) as plain `value:` entries. For an optional value only the owner can choose (such as an ElevenLabs voice id), leave it out as a commented-out entry rather than `value: ""`: each entry needs one of `value` / `valueFrom`, and the docs say nothing about empty strings.

## Auth note: PAT vs service principal

`start_app.py` pops `DATABRICKS_CLIENT_ID`/`DATABRICKS_CLIENT_SECRET` so the injected **PAT** (`DATABRICKS_TOKEN`) is the SDK's single auth method — the app then acts as your user everywhere (Lakebase, AI Gateway, LLM), matching local dev and avoiding SP-permission gaps. Apps inject SP OAuth too, and the SDK errors on ambiguous auth if both are present. (A fully SP-based setup is possible but requires granting the SP Lakebase + gateway access.)

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
| resolver conflict after bumping `livekit-agents` | its companions are pinned to it (`livekit`, `livekit-api`, `openai<3`) | bump them and every `livekit-plugins-*` together in the `.in` |
| vendor-direct TTS (e.g. ElevenLabs) never speaks; the call carries on in the fallback voice | key or voice id missing, the app resource not attached, or outbound to the vendor (`api.elevenlabs.io`) blocked | confirm the `ELEVEN_API_KEY` resource is attached and the voice id is set; verify egress; check `apps logs` for the degrade/warning line |

More dated findings, with source anchors, are kept in the Voice Studio repo's `docs/gotchas.md` (sections "Worktrees & deploy" and "ElevenLabs / expressive-TTS").
