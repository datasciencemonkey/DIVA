# Unity Gateway Voice Studio

A governed voice-agent studio presenting **Unity AI Gateway** — Choice / Control / Context / Costs —
through a LiveKit customer-support agent with loyalty→model routing, generic Lakebase-scoped tools,
and generated per-company datasets.

- **Spec:** `docs/superpowers/specs/2026-09-24-unity-gateway-voice-studio-design.md`
- **Plan (wave 1):** `docs/superpowers/plans/2026-09-24-ug-voice-studio-plan-1-foundations.md`
- **Conventions:** run everything with `uv`; the Databricks profile is user-chosen (`--profile <name>`), never auto-selected.

## Build target

- **Profile:** `DEFAULT` (`.databrickscfg`) — chosen by the user, never auto-selected.
- **Lakebase:** your Lakebase endpoint (`LAKEBASE_ENDPOINT` in `.env.local`; see `.env.example`),
  database `databricks_postgres`.
- **Lakebase-capable:** yes — user-provided live instance (endpoint reachability confirmed in Task 5 discovery).
