"""
JEV Dynamic Agent Orchestrator — standalone FastAPI app.

A prompt comes in. JEV composes an agent (model + MCP servers + skills) from
Unity Catalog metadata in one call; then an OpenAI Agents SDK agent is built from
that spec, loads the selected MCP tools on-behalf-of the end user, and is invoked.

Routes:
  GET  /            -> UI
  GET  /catalog     -> discovered {models, mcp_servers, skills}
  POST /compose     -> JEV spec for a prompt (decision only)
  POST /run         -> compose + build agent + execute MCP tools + run
  GET  /healthz
"""
from __future__ import annotations

import os
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse

import jev
import discovery
import agent as agent_mod
from ui import INDEX_HTML

app = FastAPI(title="JEV Dynamic Agent Orchestrator")

_CATALOG_CACHE: dict[str, Any] = {}


def _conversation_text(messages: list[dict[str, Any]]) -> str:
    lines = []
    for m in messages:
        role = m.get("role", "user")
        c = m.get("content", "")
        if isinstance(c, list):
            c = " ".join(p.get("text", "") for p in c if isinstance(p, dict))
        if c:
            lines.append(f"{role.upper()}: {c}")
    return "\n".join(lines).strip()


def _catalog(refresh: bool = False) -> dict[str, Any]:
    if _CATALOG_CACHE and not refresh:
        return _CATALOG_CACHE
    cat = {
        "models": discovery.list_models(),
        "mcp_servers": discovery.list_mcp_servers(),
        "skills": discovery.list_skills(),
    }
    _CATALOG_CACHE.clear()
    _CATALOG_CACHE.update(cat)
    return cat


@app.get("/", response_class=HTMLResponse)
def index():
    return HTMLResponse(INDEX_HTML)


@app.get("/healthz")
def healthz():
    return {"ok": True, "jev_key_present": bool(jev.JEV_API_KEY)}


@app.get("/catalog")
def catalog(refresh: bool = False):
    try:
        return _catalog(refresh=refresh)
    except Exception as ex:  # noqa: BLE001
        return JSONResponse(
            {"models": [], "mcp_servers": [], "skills": [], "error": str(ex)[:300]},
            status_code=200,
        )


@app.post("/compose")
async def compose(request: Request):
    body = await request.json()
    messages = body.get("messages", [])
    if not messages:
        return JSONResponse({"error": "no messages"}, status_code=400)
    threshold = body.get("threshold")
    try:
        cat = _catalog()
    except Exception as ex:  # noqa: BLE001
        return JSONResponse({"error": f"catalog discovery failed: {str(ex)[:200]}"},
                            status_code=502)
    state = _conversation_text(messages)
    try:
        spec = jev.compose(
            state,
            models=cat["models"],
            mcp_servers=cat["mcp_servers"],
            skills=cat["skills"],
            threshold=threshold,
        )
    except Exception as ex:  # noqa: BLE001
        return JSONResponse({"error": f"JEV compose failed: {str(ex)[:200]}"},
                            status_code=502)
    return spec


@app.post("/run")
async def run(request: Request):
    body = await request.json()
    messages = body.get("messages", [])
    if not messages:
        return JSONResponse({"error": "no messages"}, status_code=400)
    threshold = body.get("threshold")
    base_prompt = body.get("system_prompt", "")
    # On-behalf-of: forward the end user's OAuth token so MCP tools execute with
    # the user's UC permissions (the app SP cannot traverse the MCP gateway).
    # In a real browser session the forwarded token carries the app's consented
    # scopes (catalog.connections, mcp.external). For CLI/API testing we also
    # accept a raw bearer via Authorization when explicitly allowed.
    user_token = request.headers.get("x-forwarded-access-token")
    if os.environ.get("ORCH_ALLOW_RAW_BEARER", "0") == "1":
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            user_token = auth.split(" ", 1)[1].strip()
    try:
        cat = _catalog()
    except Exception as ex:  # noqa: BLE001
        return JSONResponse({"error": f"catalog discovery failed: {str(ex)[:200]}"},
                            status_code=502)
    state = _conversation_text(messages)

    try:
        spec = jev.compose(
            state,
            models=cat["models"],
            mcp_servers=cat["mcp_servers"],
            skills=cat["skills"],
            threshold=threshold,
        )
    except Exception as ex:  # noqa: BLE001
        return JSONResponse({"error": f"JEV compose failed: {str(ex)[:200]}"},
                            status_code=502)

    if os.environ.get("JEV_LOG_PROMPT", "0") == "1":
        m = (spec.get("model") or {}).get("model")
        sk = [s["label"] for s in spec["skills"] if s["selected"]]
        mc = [s["label"] for s in spec["mcp_servers"] if s["selected"]]
        print(f"[orchestrator] model={m} skills={sk} mcp={mc}", flush=True)

    try:
        run_out = agent_mod.build_and_run(spec, messages, base_prompt=base_prompt,
                                          user_token=user_token)
    except Exception as ex:  # noqa: BLE001
        return JSONResponse(
            {"spec": spec, "run": {"error": f"agent run failed: {str(ex)[:200]}"}},
            status_code=200,
        )

    return {"spec": spec, "run": run_out}
