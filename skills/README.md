# Voice-agent skills (ships with this repo)

This folder is an [Agent Skills](https://agentskills.io) bundle for **DIVA**. It holds the non-obvious stuff we
learned getting real-time voice agents running on Databricks, written down so you don't have to learn it the hard
way. It sits apart from the app code (`app/`, `src/`) but ships and versions with the project.

Point Cursor, Claude Code, or any Agent Skills loader at this folder. Then ask it to **deploy the app**, **add
Lakebase tools**, **wire Unity AI Gateway**, **trace calls into MLflow**, or **debug a call that connects with no
agent**. Example prompts are in the root [README § Agent skill](../README.md#agent-skill).

## What's inside

```
skills/
  building-voice-agents-on-databricks/
    SKILL.md                      # spine: architecture, workflow, gotchas, quick reference
    references/
      deployment.md               # single-container Databricks App deploy (+ the py3.11 dep trap)
      agent-and-tools.md          # worker entrypoint, FM/AI-Gateway LLM, Lakebase tools (+ RunContext gotcha)
      observability.md            # LiveKit OTel spans -> MLflow / Unity Catalog
    templates/
      start_app.py                # single-container launcher (adapt paths)
      app.yaml                    # Databricks App spec (secrets via valueFrom)
      agent-requirements.in       # worker deps to compile for Python 3.11
```

## Scope

There's one skill, `building-voice-agents-on-databricks`, and it covers the whole path: a **LiveKit agent worker
and a browser token server in one Databricks App**, the LLM on the **Databricks Foundation Model API / AI Gateway**,
**Lakebase**-backed tools, and **MLflow/OpenTelemetry** tracing into Unity Catalog.

It opens with the traps that ate the most time: the Python 3.11 vs 3.12 dependency mismatch, `databricks sync` vs
`import-dir`, service-principal grants, LiveKit's `RunContext` annotation `NameError`, and how to tell a flaky
platform blip from a real bug.

## What you can ask a coding agent to do

Once the skill is loaded, the agent should open `SKILL.md` and then the matching `references/` file. It can:

- Deploy DIVA or a fork as a **single-container Databricks App** (3.11 worker venv, secrets, SP grants,
  `databricks sync --full`).
- Scaffold a **LiveKit `AgentServer` + stdlib token mint** so a browser call dispatches the worker.
- Point the conversational LLM at **Unity AI Gateway** (`openai.responses.LLM` + tools).
- Add **Lakebase** `function_tool`s without hitting the `RunContext` `NameError`.
- Export LiveKit OTel spans to **MLflow traces in Unity Catalog**.
- Diagnose **"call connects, no agent joins"**, failed Apps deploys, and missing traces.

## Install / use

- **This repo:** leave `skills/` at the root. Cursor and Claude Code pick it up when the workspace is open.
- **Cursor (personal):** copy `skills/building-voice-agents-on-databricks` into `~/.cursor/skills/`.
- **Claude Code (personal):** copy the same folder into `~/.claude/skills/`.
- **Any agent runtime:** point your skill loader at `skills/`.

The skill's `description` fires when you're building, deploying or debugging a voice agent on Databricks. From
there the agent reads `SKILL.md` and digs into `references/` as it needs to.

## Background reading

- [Unity Gateway is Generally Available](https://www.databricks.com/blog/unity-ai-gateway-generally-available) and the [Unity Gateway docs](https://docs.databricks.com/aws/en/unity-gateway/)
- [Announcing General Availability of Databricks Apps](https://www.databricks.com/blog/announcing-general-availability-databricks-apps) and the [Databricks Apps docs](https://docs.databricks.com/aws/en/dev-tools/databricks-apps/)
- [Lakebase Search: state-of-the-art full text and vector search for Postgres](https://www.databricks.com/blog/lakebase-search-state-art-full-text-and-vector-search-postgres)
- The full reading list is in the root [README § Further reading](../README.md#further-reading).

## Where this came from

We pulled these patterns out of a working deployment (`ug-voice-studio-v2`) using the `superpowers:writing-skills`
discipline. Everything here comes from code that shipped and ran, not from a plan.
