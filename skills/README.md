# Voice-agent skills (ships with this repo)

An [Agent Skills](https://agentskills.io) bundle living at `skills/` in **DIVA** — the reusable, non-obvious techniques for building real-time voice agents on Databricks, distilled so others don't have to re-learn them the hard way. Kept separate from the app code (`app/`, `src/`) but versioned and shipped with the project.

Point Cursor, Claude Code, or any Agent Skills loader at this folder, then ask it to **deploy the app**, **add Lakebase tools**, **wire Unity AI Gateway**, **trace calls into MLflow**, or **debug a call that connects with no agent**. Concrete prompts live in the root [README § Agent skill](../README.md#agent-skill).

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

One skill, `building-voice-agents-on-databricks`, covering the full path: a **LiveKit agent worker + browser token server in one Databricks App**, LLM on the **Databricks Foundation Model API / AI Gateway**, **Lakebase**-backed tools, and **MLflow/OpenTelemetry** tracing into Unity Catalog. It leads with the traps that cost real time (the Python 3.11-vs-3.12 dependency mismatch, `databricks sync` vs `import-dir`, service-principal grants, the LiveKit `RunContext` annotation `NameError`, and telling a transient platform failure from a real one).

## What you can ask a coding agent to do

Once the skill is loaded, these are in scope (the agent should open `SKILL.md`, then the matching `references/` file):

- Deploy DIVA or a fork as a **single-container Databricks App** (3.11 worker venv, secrets, SP grants, `databricks sync --full`).
- Scaffold **LiveKit `AgentServer` + stdlib token mint** so a browser call dispatches the worker.
- Point the conversational LLM at **Unity AI Gateway** (`openai.responses.LLM` + tools).
- Add **Lakebase** `function_tool`s without the `RunContext` `NameError`.
- Export LiveKit OTel spans to **MLflow traces in Unity Catalog**.
- Diagnose **"call connects, no agent joins"**, failed Apps deploys, and missing traces.

## Install / use

- **This repo:** leave `skills/` at the root; Cursor and Claude Code will pick it up when the workspace is open.
- **Cursor (personal):** copy `skills/building-voice-agents-on-databricks` into `~/.cursor/skills/`.
- **Claude Code (personal):** copy the same folder into `~/.claude/skills/`.
- **Any agent runtime:** point your skill loader at `skills/`.

The skill's `description` triggers it when you're building/deploying/debugging a voice agent on Databricks; the agent then reads `SKILL.md` and drills into `references/` as needed.

## Provenance

Distilled from a working deployment (`ug-voice-studio-v2`) using the `superpowers:writing-skills` discipline — patterns are taken from code that actually shipped and ran, not from intentions.
