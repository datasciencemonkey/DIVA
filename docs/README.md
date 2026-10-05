# Docs

Start with the [root README](../README.md). These pages go deeper.

| Read | When you want |
|---|---|
| [architecture.md](architecture.md) | How a call works end to end: every component and arrow, the voice-mode path, and the governance rules (the design spec's eight, plus G9 to G13 for voice mode). |
| [halloween-voice.md](halloween-voice.md) | Halloween mode: AI Decide, the ElevenLabs voice, its setup and settings, and how to verify the voice. |
| [gotchas.md](gotchas.md) | The non-obvious traps and platform constraints hit while building this on Databricks, and the fix for each. Read it before your first deploy. |
| [discovery/](discovery/) | Contracts verified against the live services: Lakebase Search and indexes, model routing, the LiveKit runtime, expressive tags. Dated records, not tutorials. |
| [halloween-ui/](halloween-ui/DESIGN.md) | The design and build plan for the Halloween studio theme. |
| [superpowers/](superpowers/) | The design specs and implementation plans for each wave of the project, in date order. |
| [constitution.md](constitution.md) | How work on this project is run. |

The agent skill that teaches a coding agent to build this kind of app lives in
[`skills/`](../skills/README.md).

## More reading

The root README links the main Databricks post and doc for each product. These are the rest.

- **Agent Bricks and Databricks Apps:** [Databricks Apps is Generally Available](https://www.databricks.com/blog/announcing-general-availability-databricks-apps) ·
  [Production-ready data and AI apps with Databricks Apps and Lakebase](https://www.databricks.com/blog/how-build-production-ready-data-and-ai-apps-databricks-apps-and-lakebase) ·
  Docs: [Deploy a Databricks app](https://docs.databricks.com/aws/en/dev-tools/databricks-apps/deploy) · [Use agents on Databricks](https://docs.databricks.com/aws/en/agents/custom-agents/build-agents)
- **Unity Gateway:** [What's new at Data + AI Summit 2026](https://www.databricks.com/blog/ai-governance-data-ai-summit-2026-whats-new-unity-ai-gateway) ·
  [Service policies, guardrails, observability and cost controls](https://www.databricks.com/blog/whats-new-unity-ai-gateway-service-policies-guardrails-observability-and-cost-controls-ai) ·
  [Governance layer for agentic AI](https://www.databricks.com/blog/ai-gateway-governance-layer-agentic-ai) ·
  Docs: [AI governance with Unity Gateway](https://docs.databricks.com/aws/en/ai-gateway/)
- **Lakebase:** [Build apps with Lakebase and Databricks Apps](https://www.databricks.com/blog/how-use-lakebase-transactional-data-layer-databricks-apps)
- **MLflow traces:** Docs: [Tracing overview](https://docs.databricks.com/aws/en/mlflow3/genai/tracing/overview)
- **AI Decide:** Docs: [REST API](https://docs.databricks.com/api/ai-functions/v1/ai-decide)
