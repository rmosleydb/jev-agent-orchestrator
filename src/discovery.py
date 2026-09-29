"""
Discover the building blocks JEV can compose an agent from, using AI Gateway /
Unity Catalog metadata so JEV understands the *context* of each choice.

  models      : databricks-* llm/v1/chat serving endpoints (FMAPI).
  mcp_servers : UC connections that represent MCP servers (+ their UC comment).
  skills      : UC skill securables (catalog.schema.skill) (+ their comment).

Everything is described by its Unity Catalog metadata (name + comment/description),
which is exactly what gets fed to JEV as the selection context.
"""
from __future__ import annotations

import os
from functools import lru_cache
from typing import Any


def _ws():
    from databricks.sdk import WorkspaceClient
    return WorkspaceClient()


# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #
# Short human descriptions so JEV understands each model's sweet spot. Anything
# not listed still appears with a generic description.
_MODEL_HINTS = {
    "claude-opus": "Anthropic Claude Opus — strongest reasoning, complex coding, long analysis.",
    "claude-sonnet": "Anthropic Claude Sonnet — balanced quality/speed for coding and reasoning.",
    "claude-haiku": "Anthropic Claude Haiku — fast, cheap, good for simple tasks.",
    "claude": "Anthropic Claude — strong reasoning and coding.",
    "gpt-oss": "OpenAI GPT-OSS — general-purpose open model.",
    "gpt-5": "OpenAI GPT-5 — general-purpose, strong all-round quality.",
    "gpt": "OpenAI GPT — general-purpose chat and reasoning.",
    "gemini": "Google Gemini — fast, multimodal, good general assistant.",
    "kimi": "Moonshot Kimi — reasoning model, strong at math/logic.",
    "llama": "Meta Llama — open general-purpose model.",
    "qwen": "Alibaba Qwen — multilingual general model, good at coding.",
    "mistral": "Mistral — efficient general-purpose open model.",
    "mixtral": "Mixtral — efficient MoE open model.",
    "dbrx": "Databricks DBRX — general-purpose open model.",
}


def _describe_model(name: str) -> str:
    n = name.lower()
    for frag, desc in _MODEL_HINTS.items():
        if frag in n:
            return desc
    return "General-purpose chat model."


def list_models() -> list[dict[str, str]]:
    w = _ws()
    out = []
    for e in w.serving_endpoints.list():
        task = str(getattr(e, "task", "") or "")
        name = e.name
        if task == "llm/v1/chat" and name.startswith("databricks-"):
            label = name.replace("databricks-", "")
            out.append({"label": label, "model": name, "description": _describe_model(name)})
    out.sort(key=lambda x: x["label"])
    return out


# --------------------------------------------------------------------------- #
# MCP servers (UC connections)
# --------------------------------------------------------------------------- #
# Connection-name substrings that indicate an MCP server. We also treat any
# connection whose comment mentions "MCP" as an MCP server.
_MCP_NAME_HINTS = ("mcp", "tavily", "you", "glean", "alphavantage", "polygon",
                   "github", "slack", "gmail", "calendar", "drive", "sharepoint",
                   "atlassian", "genie")
_SKIP_CONN = ("internal-", "managed-database-", "system_ai_one_chat")


def _looks_like_mcp(name: str, comment: str) -> bool:
    n = name.lower()
    if any(n.startswith(s) or s in n for s in _SKIP_CONN):
        return False
    if "mcp" in (comment or "").lower():
        return True
    return any(h in n for h in _MCP_NAME_HINTS)


def list_mcp_servers() -> list[dict[str, str]]:
    w = _ws()
    out = []
    for c in w.connections.list():
        name = c.name
        comment = getattr(c, "comment", None) or ""
        if not _looks_like_mcp(name, comment):
            continue
        # Exclude connections that explicitly declare they are NOT MCP servers.
        # Several system_ai_agent_* / google-drive / gmail connections are plain
        # OAuth REST proxies to googleapis.com (options.is_mcp_connection ==
        # "false"). Hitting /api/2.0/mcp/external/<them> forwards to a non-MCP
        # host and fails with "Session terminated" / 404. Don't offer those.
        opts = getattr(c, "options", None) or {}
        try:
            is_mcp = str(opts.get("is_mcp_connection", "")).strip().lower()
        except AttributeError:
            is_mcp = ""
        if is_mcp == "false":
            continue
        desc = comment.strip() or f"MCP server / tool connection '{name}'."
        out.append({
            "label": name,
            "connection": name,
            "description": desc,
            "connection_type": str(getattr(c, "connection_type", "") or ""),
        })
    out.sort(key=lambda x: x["label"])
    return out


# --------------------------------------------------------------------------- #
# Skills (UC securables) — Beta feature. Query catalogs for objects of the
# skill securable type. If none are present, fall back to a small seed set so
# the demo still works end-to-end.
# --------------------------------------------------------------------------- #
_SEED_SKILLS = [
    {"label": "sql-conventions",
     "description": "Apply the team's SQL style: CTEs over subqueries, snake_case, explicit column lists, no SELECT *."},
    {"label": "incident-runbook",
     "description": "Follow the on-call incident runbook: triage severity, check dashboards, page the owner, write a postmortem."},
    {"label": "data-analysis",
     "description": "Analyze a table or dataset: profile columns, summarize distributions, call out anomalies and next steps."},
    {"label": "code-review",
     "description": "Review code for correctness, security, and style; suggest concrete diffs and explain the reasoning."},
    {"label": "financial-analysis",
     "description": "Analyze financial data: compute growth rates, margins, and ratios; explain drivers and risks concisely."},
]


def _skill_catalog_schema() -> tuple[str, str] | None:
    cs = os.environ.get("SKILLS_SCHEMA")  # e.g. "main.skills"
    if cs and "." in cs:
        cat, sch = cs.split(".", 1)
        return cat, sch
    return None


def list_skills() -> list[dict[str, str]]:
    """Best-effort discovery of UC skill securables; seed set as fallback.

    UC skills are a Beta securable. Depending on SDK/workspace enablement, the
    typed client may not expose them yet, so we degrade gracefully to a seed set
    (overridable by setting SKILLS_SCHEMA=catalog.schema of real skills).
    """
    loc = _skill_catalog_schema()
    if loc:
        cat, sch = loc
        try:
            w = _ws()
            # Skills surface can vary by workspace; try functions/tables comments
            # in the configured schema as skill descriptors.
            out = []
            for t in w.tables.list(catalog_name=cat, schema_name=sch):
                out.append({
                    "label": t.name,
                    "description": (getattr(t, "comment", None) or f"Skill '{t.name}'.").strip(),
                    "full_name": f"{cat}.{sch}.{t.name}",
                })
            if out:
                out.sort(key=lambda x: x["label"])
                return out
        except Exception:  # noqa: BLE001
            pass
    return [dict(s) for s in _SEED_SKILLS]
