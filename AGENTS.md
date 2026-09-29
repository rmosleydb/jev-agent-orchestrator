# AGENTS.md

Instructions for a coding agent working in this repo. Humans: see `README.md`.

## What this app is

A **Databricks App** (FastAPI) that composes a **purpose-built agent per prompt**
using **JEV (TypeSafe System One)** as the decision engine, then runs that agent
with the **OpenAI Agents SDK** on Databricks (`databricks-openai` +
`openai-agents`).

JEV is **not** an LLM. Given a `state` and typed `questions`, it returns typed,
calibrated decisions and evaluates all questions **in parallel/isolation** in one
call. We use two primitives:

- **`choice`** — pick exactly one option (used to pick the model).
- **`noul`** — P(statement is true), 0–1 (used per MCP server and per skill;
  everything ≥ threshold is selected).

Pipeline per prompt:

1. **Discover** building blocks + their Unity Catalog metadata (`discovery.py`):
   models (FMAPI `databricks-*` `llm/v1/chat`), MCP servers (UC connections),
   skills (UC skill securables, with a seed-set fallback).
2. **Compose** with one JEV call (`jev.py::compose`): 1 `choice` for the model,
   1 `noul` per MCP server, 1 `noul` per skill → an agent **spec**.
3. **Build + run** an agent from the spec (`agent.py`) using the OpenAI Agents SDK:
   `Agent(model=<chosen endpoint>, mcp_servers=[...])` + selected skill bodies
   injected into the system prompt, driven by `Runner.run`. Inference goes through
   `AsyncDatabricksOpenAI` with `set_default_openai_api("chat_completions")` (the
   Responses API is not supported for Claude/Opus/Gemini on Databricks).

So: **JEV = composer/router, Databricks FMAPI = the model that answers,
MCP = tools, skills = injected instructions.**

## Repo layout

- `src/jev.py` — JEV client + `compose()` (the choice/noul question builder).
- `src/discovery.py` — model/MCP/skill discovery from AI Gateway + Unity Catalog.
- `src/agent.py` — builds & runs the OpenAI Agents SDK agent from a spec
  (best-effort MCP; SDK compatibility patches + JEV-ranked model fallback).
- `src/app.py` — FastAPI routes (`/`, `/orchestrator/catalog|compose|run`, `/healthz`).
- `src/ui.py` — inline single-page UI (`INDEX_HTML`) with noul bars.
- `src/app.yaml`, `src/requirements.txt` — Databricks Apps launch config + deps.

Everything deployable lives under `src/`. Keep it a valid Databricks Apps bundle
(`app.yaml` + `requirements.txt` + `app.py` at the root of `src/`).

## Hard rules

- **Never hardcode `JEV_API_KEY`** (or any token) in code, YAML, README, or
  commits. It is injected at runtime via `app.yaml` `valueFrom` from a Databricks
  secret. `.gitignore` blocks `.env`, `secrets*.json`, keys, etc.
- Don't move files out of `src/` — the deploy step points at `src/`.
- **Skills are context, not code.** A "skill" (`SKILL.md`) is instructions; JEV
  pre-selects them and their bodies are injected into the system prompt. Do not
  try to "execute" a skill. (A future `load_skill` tool for progressive
  disclosure is fine, but keep injection as the default.)
- **MCP tool loading is best-effort and must never crash a run.** If a server's
  tools can't be loaded, catch it, report it in `mcp_report`, and continue.
- Model inference goes through `databricks-openai` `AsyncDatabricksOpenAI` + the
  `openai-agents` SDK, pinned to `chat_completions`. Some FMAPI models are
  reasoning models and/or misbehave with tools; `agent.py` applies two surgical
  SDK compatibility patches (reasoning-block coercion; MCP tool-schema `$schema`
  stripping) and, on a classified incompatibility, transparently falls back down
  **JEV's own ranked candidate list** (`ORCH_TOOL_SAFE_MODEL` as last resort).
  Keep any fallback transparent — report it in `mcp_report` / `run_model`.
- JEV composition is **one call** — keep it that way. Adding candidates is cheap
  (parallel fan-out); don't split into per-item round trips.

## Deploy (Databricks Apps)

Prereqs: Databricks CLI authenticated to the target workspace, and a JEV key.

1. **Create the JEV secret** (once per workspace):
   ```bash
   databricks secrets create-scope jev
   databricks secrets put-secret jev api_key   # paste the JEV key
   ```
   `app.yaml` references it as `valueFrom: jev-api-key`; ensure the app has an
   app resource named `jev-api-key` mapped to scope `jev`, key `api_key`.

2. **Sync `src/` and create + deploy:**
   ```bash
   databricks sync ./src /Workspace/Users/<you>/jev-agent-orchestrator
   databricks apps create jev-agent-orchestrator
   databricks apps deploy jev-agent-orchestrator \
     --source-code-path /Workspace/Users/<you>/jev-agent-orchestrator
   ```

3. **Grant the app's service principal:** `CAN QUERY` on the FMAPI serving
   endpoints it may pick; `EXECUTE` + `USE CATALOG`/`USE SCHEMA` on any MCP
   Services and skill securables it may compose from.

4. **Verify:** open the app URL, or hit `GET /healthz` (`jev_key_present`) and
   `GET /orchestrator/catalog` (should list models/mcp_servers/skills). Empty
   lists usually mean the SP lacks access. A 502 from `compose`/`run` points at
   the JEV secret/key or JEV availability.

## Redeploy after edits

Re-run `databricks sync ./src ...` then `databricks apps deploy ...` at the same
workspace path. No code lives outside `src/`.

## Local run

```bash
cd src && pip install -r requirements.txt
export JEV_API_KEY=...            # JEV key
export DATABRICKS_HOST=...        # FMAPI + discovery
export DATABRICKS_TOKEN=...       # or CLI profile / OAuth
uvicorn app:app --reload --port 8080
```

## Config (env vars)

`JEV_API_KEY` (required), `JEV_API_URL` (default
`https://api.typesafe.ai/v1/systemone`), `JEV_MODEL` (default `jev-latest`),
`JEV_TIMEOUT` (default `45`), `JEV_SELECT_THRESHOLD` (default `0.6`),
`SKILLS_SCHEMA` (`catalog.schema` of real UC skills; seed set if unset),
`JEV_LOG_PROMPT` (`1` to log the composed spec).
