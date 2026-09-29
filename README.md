# JEV Dynamic Agent Orchestrator

A [Databricks App](https://docs.databricks.com/aws/en/dev-tools/databricks-apps/)
that turns **one prompt into a purpose-built agent**, on the fly, using
[JEV (TypeSafe System One)](https://typesafe.ai) as the decision engine.

A prompt comes in. JEV composes an agent for it:

- **picks the model** — one JEV `choice` over your Foundation Model API endpoints
- **picks the MCP servers** — one JEV `noul` (0–1 relevance) per candidate; everything above a threshold is selected
- **picks the skills** — one JEV `noul` per candidate; everything above a threshold is selected

…all in a **single JEV round trip**, using **Unity Catalog metadata** (names +
comments/descriptions) as the selection context. Then an agent is composed from
that spec — chosen model + selected MCP tools + selected skill instructions —
using the **OpenAI Agents SDK** on Databricks, and invoked.

```
                          ┌──────────────────────────────────────────┐
   prompt ───────────────►│  Discovery (AI Gateway + Unity Catalog)    │
                          │   models · MCP servers · skills (+metadata)│
                          └───────────────┬────────────────────────────┘
                                          │  descriptions
                                          ▼
                          ┌──────────────────────────────────────────┐
                          │  JEV — one parallel call                   │
                          │   choice: which model                      │
                          │   noul  : which MCP servers (≥ threshold)  │
                          │   noul  : which skills      (≥ threshold)  │
                          └───────────────┬────────────────────────────┘
                                          │  agent spec
                                          ▼
                          ┌──────────────────────────────────────────┐
                          │  OpenAI Agents SDK agent (Databricks)      │
                          │   model = chosen serving endpoint          │
                          │   tools = selected MCP servers' tools      │
                          │   prompt = base + selected skill bodies    │
                          └───────────────┬────────────────────────────┘
                                          ▼
                                     agent reply
```

## Why JEV for composition

JEV is **not an LLM** — it returns *typed, calibrated* decisions for a given
`state` and set of `questions`, and evaluates all questions **in parallel and in
isolation**. That makes it a natural fit for agent composition:

- **`choice`** selects exactly one model.
- **`noul`** gives a per-item probability that a tool/skill is relevant, so
  "anything with meaningful signal gets selected" is just a threshold — no
  brittle single-pick, no wasteful LLM discovery loop.

Because it's one call, adding more candidate tools/skills is essentially free
(speculative fan-out), and every decision comes back calibrated with a
confidence you can show and threshold on.

## What gets discovered (and how JEV understands it)

| Building block | Source | Context fed to JEV |
| --- | --- | --- |
| Models | `serving_endpoints.list()` → `databricks-*` `llm/v1/chat` | endpoint name + a short capability hint |
| MCP servers | Unity Catalog **connections** that look like MCP servers | connection name + **UC `comment`** |
| Skills | UC skill securables (`catalog.schema.skill`), Beta | skill name + **UC `comment`/description** (seed set as fallback) |

The whole point: **JEV chooses using your Unity Catalog metadata**, so its
selections reflect what each tool/skill actually is in *your* enterprise.

## Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| `GET`  | `/` | Single-page UI (compose + run, with noul bars) |
| `GET`  | `/orchestrator/catalog` | Discovered `{models, mcp_servers, skills}` |
| `POST` | `/orchestrator/compose` | JEV spec for a prompt (decision only, no run) |
| `POST` | `/orchestrator/run` | Compose **and** build+run the agent (OpenAI Agents SDK) |
| `GET`  | `/healthz` | Liveness + whether the JEV key is present |

`compose` / `run` request body:

```json
{ "messages": [{"role":"user","content":"..."}], "threshold": 0.4, "system_prompt": "" }
```

`run` response: `{ "spec": {…JEV composition…}, "run": { "reply", "model", "tools_used", "mcp_report", "skill_names" } }`.

## Configuration (env vars)

| Var | Default | Purpose |
| --- | --- | --- |
| `JEV_API_KEY` | — (**required**) | JEV bearer key. Injected via `app.yaml` `valueFrom` from a Databricks secret. **Never hardcode.** |
| `JEV_API_URL` | `https://api.typesafe.ai/v1/systemone` | JEV endpoint |
| `JEV_MODEL` | `jev-latest` | JEV model tag |
| `JEV_TIMEOUT` | `45` | JEV HTTP timeout (s) |
| `JEV_SELECT_THRESHOLD` | `0.4` | Default noul threshold for MCP/skill selection |
| `SKILLS_SCHEMA` | — | `catalog.schema` of real UC skills; falls back to a seed set if unset |
| `JEV_LOG_PROMPT` | `0` | `1` logs the composed spec to stdout |
| `ORCH_TOOL_SAFE_MODEL` | `databricks-claude-haiku-4-5` | Final backstop model if the JEV-ranked fallback chain is exhausted |
| `ORCH_RUN_TIMEOUT` | `120` | Overall agent-run ceiling (s) |
| `MCP_MAX_SERVERS` | `25` | Cap on MCP servers connected per run |
| `MCP_PREFER` | `tavily_mcp` | MCP server to prioritize when capping |

## Deploy (Databricks Apps)

```bash
# 1. Create the JEV secret (once per workspace)
databricks secrets create-scope jev
databricks secrets put-secret jev api_key      # paste the JEV key

# 2. Sync src/ and deploy
databricks sync ./src /Workspace/Users/<you>/jev-agent-orchestrator
databricks apps create jev-agent-orchestrator
databricks apps deploy jev-agent-orchestrator \
  --source-code-path /Workspace/Users/<you>/jev-agent-orchestrator
```

Then grant the app's **service principal** `CAN QUERY` on the FMAPI serving
endpoints and `EXECUTE` / `USE` on any MCP Services + skills you want it to
compose from. Open the app URL and try a prompt.

## Local run

```bash
cd src && pip install -r requirements.txt
export JEV_API_KEY=...            # JEV key
export DATABRICKS_HOST=...        # for FMAPI + discovery from your machine
export DATABRICKS_TOKEN=...       # or a CLI profile / OAuth
uvicorn app:app --reload --port 8080
```

## Notes & limits

- **Skills are injected as instructions.** A skill is context, not code; JEV
  pre-selects the relevant ones and their bodies are added to the system prompt.
  (A progressive-disclosure `load_skill` tool is a natural future toggle.)
- **MCP tool loading is best-effort.** External MCP servers need governed
  access (grants/OAuth) via Unity Gateway; if a server's tools can't be loaded,
  the agent still runs with whatever loaded and reports what was skipped.
- **UC Skills are Beta;** discovery falls back to a seed set unless
  `SKILLS_SCHEMA` points at real skill securables.
- **Model-vs-runtime compatibility.** Databricks model serving only supports the
  `chat_completions` API for Claude/Opus/Gemini (not the Responses API), and some
  JEV-picked models misbehave on it (Gemini's tool-calling; reasoning-model output
  shape). The runtime applies two surgical compatibility patches (reasoning-block
  coercion; MCP tool-schema `$schema` stripping) and, if a model still can't drive
  the runtime, transparently **falls back down JEV's own ranked candidate list**
  (then `ORCH_TOOL_SAFE_MODEL`). The fallback is reported in the run's `mcp_report`
  and surfaced in the UI as `JEV model → run_model`.

## License

MIT — see [LICENSE](LICENSE).
