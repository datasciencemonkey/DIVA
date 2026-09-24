# Discovery: tier → model routing (R2)

_Verified 2026-09-24 against `{host}/ai-gateway/openai/v1/responses` on the DEFAULT profile, each with a function-tool request (the agent needs tools)._

## Finding: only some models support Responses-API passthrough WITH function calling

- **Supported (HTTP 200):** `databricks-gpt-5-nano`, `databricks-gpt-5-4-nano`, `databricks-gpt-5-mini`, `databricks-gpt-5-4-mini`, `databricks-gpt-5-5`, `databricks-gpt-5-5-pro`, `databricks-gpt-6-sol`, `databricks-gpt-6-luna`, `databricks-grok-4-6`. (GPT-proper family + Grok.)
- **NOT supported (HTTP 400 `Responses API passthrough is not supported for model …`):** `databricks-claude-*` (incl. `opus-5-5`, `sonnet-4-5`), `databricks-gemini-3-8-flash`, `databricks-gpt-oss-120b`.
- Confirms ReferenceApp §7 (claude-sonnet-4-5 fails) and extends it: **Claude and Gemini cannot be used with `openai.responses.LLM` + tools in v1.**

## Tier mapping (v1) — from the supported set, clean cost/quality gradient

| Tier | Model | Env var |
|---|---|---|
| Standard | `databricks-gpt-5-nano` | `UG_MODEL_STANDARD` |
| Premium | `databricks-gpt-5-5` | `UG_MODEL_PREMIUM` |
| VIP | `databricks-gpt-6-sol` | `UG_MODEL_VIP` |
| Fallback | `databricks-gpt-5-5` | `UG_MODEL_FALLBACK` |

Three visibly-distinct names (`gpt-5-nano` → `gpt-5-5` → `gpt-6-sol`) make the "Choice" pillar legible. All verified Responses+tools-compatible.

## Costs pillar

Cost = per-turn tokens (from the Responses `usage` object; see runtime-contracts R3) × a per-model `$/1M tokens` config table. **Rates are illustrative in the demo** — set from current Foundation Model API pricing before any external showing; do not present as authoritative billing.

## Note for the spec (future)

To route a premium tier to Claude/Gemini, add a **chat/completions tool path** (`openai.LLM` over `/ai-gateway/openai/v1/chat/completions`) for models that support tools there — a Future-extension, since it means two LLM code paths. v1 stays single-path (Responses) on the GPT/Grok set.
