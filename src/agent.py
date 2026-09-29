"""
Compose and run an agent from a JEV spec — OpenAI Agents SDK edition.

Given the JEV composition (chosen model + selected MCP servers + selected
skills), build and run an agent using Databricks' officially-supported modern
stack:

  * model  -> databricks_openai.AsyncDatabricksOpenAI (Responses/chat completions)
  * agent  -> agents.Agent + agents.Runner       (the OpenAI Agents SDK)
  * mcp    -> databricks_openai.agents.McpServer  (first-class MCP servers;
              no manual StructuredTool wrapping, no asyncio.run() hacks)
  * skills -> skill descriptions injected into the agent instructions

MCP loading is best-effort and health-checked per server (mirrors the
`connect_healthy_mcp_servers` pattern from databricks/app-templates): a server
that connects but can't list tools (auth, preview limits) is dropped and
reported, so the agent still runs with whatever succeeded.

The public entry point `build_and_run(spec, messages, base_prompt, user_token)`
is a synchronous wrapper (called from the FastAPI route) that drives the async
Agents SDK inside a dedicated worker thread with its own event loop.
"""
from __future__ import annotations

import asyncio
import os
import threading
from typing import Any

WORKSPACE_HOST = os.environ.get("DATABRICKS_HOST", "").rstrip("/")

_SDK_PATCHED = False


# --------------------------------------------------------------------------- #
# OpenAI Agents SDK compatibility patches (applied once, at first run)
# --------------------------------------------------------------------------- #
def _strip_schema_meta(obj: Any) -> Any:
    """Recursively drop JSON-Schema meta keys that some model function-calling
    APIs reject. Gemini's function_declarations API 400s on a `$schema` key
    ("Unknown name \"$schema\""); `$id`/`$defs`/`definitions` are the same class
    of meta key. Returns a cleaned COPY (never mutates the SDK's dict).
    """
    BAD = ("$schema", "$id", "$comment")
    if isinstance(obj, dict):
        return {k: _strip_schema_meta(v) for k, v in obj.items() if k not in BAD}
    if isinstance(obj, list):
        return [_strip_schema_meta(v) for v in obj]
    return obj


def _patch_agents_sdk() -> None:
    """Two surgical fixes for known Databricks-model x Agents-SDK mismatches.
    Idempotent; version-pinned deps keep the patch points stable.

    (A) chat_completions converter: when a model (e.g. Claude Opus reasoning)
        returns `message.content` as a LIST of blocks instead of a str, the SDK
        does ResponseOutputText(text=<list>) and blows up with a pydantic
        ValidationError. We coerce list-content to joined text first.

    (B) MCP tool schemas: the SDK passes `tool.inputSchema` straight through as
        params_json_schema, carrying a `$schema` key that Gemini rejects. We
        strip meta keys from the schema before the FunctionTool is built.
    """
    global _SDK_PATCHED
    if _SDK_PATCHED:
        return

    # ---- (A) list-content -> text in the chat->responses converter ----
    try:
        from agents.models import chatcmpl_converter as _cc

        _orig_msg = _cc.Converter.message_to_output_items.__func__

        def _patched_msg(cls, message, *args, **kwargs):
            content = getattr(message, "content", None)
            if isinstance(content, list):
                parts = []
                for b in content:
                    if isinstance(b, dict):
                        t = b.get("text")
                        if t is None and b.get("type") in ("text", "output_text"):
                            t = b.get("content")
                        if isinstance(t, str) and t:
                            parts.append(t)
                    elif isinstance(b, str) and b:
                        parts.append(b)
                try:
                    message.content = "\n".join(parts)
                except Exception:  # noqa: BLE001 (frozen model? fall back to setattr copy)
                    object.__setattr__(message, "content", "\n".join(parts))
            return _orig_msg(cls, message, *args, **kwargs)

        _cc.Converter.message_to_output_items = classmethod(_patched_msg)
    except Exception as ex:  # noqa: BLE001
        print(f"[orch] SDK patch (A) skipped: {ex}", flush=True)

    # ---- (B) strip `$schema` etc. from MCP tool schemas ----
    try:
        from agents.mcp import util as _mcpu

        _orig_to_fn = _mcpu.MCPUtil.to_function_tool.__func__

        def _patched_to_fn(cls, tool, server, convert_schemas_to_strict, *a, **kw):
            try:
                if getattr(tool, "inputSchema", None):
                    tool.inputSchema = _strip_schema_meta(tool.inputSchema)
            except Exception:  # noqa: BLE001
                pass
            return _orig_to_fn(cls, tool, server, convert_schemas_to_strict, *a, **kw)

        _mcpu.MCPUtil.to_function_tool = classmethod(_patched_to_fn)
    except Exception as ex:  # noqa: BLE001
        print(f"[orch] SDK patch (B) skipped: {ex}", flush=True)

    _SDK_PATCHED = True
    print("[orch] Agents SDK compatibility patches applied", flush=True)


# --------------------------------------------------------------------------- #
# Host / URL helpers
# --------------------------------------------------------------------------- #
def _host() -> str:
    global WORKSPACE_HOST
    h = WORKSPACE_HOST
    if not h:
        try:
            from databricks.sdk import WorkspaceClient
            h = (WorkspaceClient().config.host or "").rstrip("/")
        except Exception:  # noqa: BLE001
            h = ""
    h = h.rstrip("/")
    # Apps often expose DATABRICKS_HOST as a bare hostname (no scheme); the
    # httpx-based MCP client rejects a schemeless URL (UnsupportedProtocol).
    if h and not h.startswith(("http://", "https://")):
        h = "https://" + h
    WORKSPACE_HOST = h
    return h


def _mcp_gateway_url(connection: str) -> str:
    """Databricks-managed MCP URL for an EXTERNAL UC connection."""
    return f"{_host()}/api/2.0/mcp/external/{connection}"


def _system_prompt(base: str, skills: list[dict[str, Any]]) -> str:
    parts = [base.strip() or "You are a helpful enterprise assistant."]
    selected = [s for s in skills if s.get("selected")]
    if selected:
        parts.append(
            "\nYou have been assigned the following governed skills. Follow each "
            "one faithfully when relevant:"
        )
        for s in selected:
            parts.append(f"\n### Skill: {s['label']}\n{s.get('description') or ''}".rstrip())
    return "\n".join(parts)


# --------------------------------------------------------------------------- #
# WorkspaceClient (on-behalf-of the end user when a forwarded token is present)
# --------------------------------------------------------------------------- #
def _mcp_workspace_client(user_token: str | None = None):
    """Build a WorkspaceClient for MCP gateway calls.

    Prefer the *user's* forwarded OAuth token (on-behalf-of). The app SP's own
    token carries only limited scopes and cannot traverse the MCP gateway,
    whereas the end user has full permissions on the UC connection. Falls back
    to the app SP default when no user token is available.

    This mirrors app-templates' get_user_workspace_client():
    WorkspaceClient(token=..., auth_type="pat").
    """
    from databricks.sdk import WorkspaceClient
    if user_token:
        return WorkspaceClient(host=_host(), token=user_token, auth_type="pat")
    return WorkspaceClient()


# --------------------------------------------------------------------------- #
# Error chain unwrapping (keeps the UI error modal informative)
# --------------------------------------------------------------------------- #
def _unwrap(e, depth=0, acc=None):
    acc = acc if acc is not None else []
    acc.append(("  " * depth) + f"{type(e).__name__}: {e}")
    for se in (getattr(e, "exceptions", None) or []):
        _unwrap(se, depth + 1, acc)
    if getattr(e, "__cause__", None) is not None and e.__cause__ is not e:
        _unwrap(e.__cause__, depth + 1, acc)
    return acc


# --------------------------------------------------------------------------- #
# Async core: build MCP servers, health-check them, run the agent.
# --------------------------------------------------------------------------- #
async def _connect_healthy_mcp_servers(stack, mcp_servers, user_token, report):
    """Enter each McpServer's async context and force a tools/list to prove it
    is healthy. Healthy servers are kept; failures are dropped + reported.

    Returns the list of connected, healthy McpServer objects.
    """
    from databricks_openai.agents import McpServer

    selected = [m for m in mcp_servers if m.get("selected")]
    if not selected:
        return []

    # Server cap + preference ordering (parity with the previous loader).
    max_servers = int(os.environ.get("MCP_MAX_SERVERS", "25"))
    _pref = [s.strip() for s in os.environ.get("MCP_PREFER", "tavily_mcp").split(",") if s.strip()]

    def _prio(m):
        c = (m.get("connection") or m["label"])
        return _pref.index(c) if c in _pref else len(_pref)

    selected = sorted(selected, key=_prio)
    attempt = selected[:max_servers]
    for m in selected[max_servers:]:
        report.append({"label": m["label"], "connection": m.get("connection") or m["label"],
                       "loaded": False, "tools": [], "error": "skipped (server cap)"})

    healthy = []
    for m in attempt:
        conn = m.get("connection") or m["label"]
        entry = {"label": m["label"], "connection": conn, "loaded": False,
                 "tools": [], "error": None}
        try:
            wc = _mcp_workspace_client(user_token)
            server = McpServer(
                url=_mcp_gateway_url(conn),
                name=m["label"],
                workspace_client=wc,
            )
            connected = await stack.enter_async_context(server)
            # Force the connectivity + authorization check NOW so a bad server
            # cannot crash Runner.run() later (tools are listed lazily there).
            listed = await connected.list_tools()
            names = [getattr(t, "name", str(t)) for t in (listed or [])]
            entry["tools"] = names
            entry["loaded"] = True
            healthy.append(connected)
            print(f"[orch-mcp] {conn} LOADED tools={names}", flush=True)
        except Exception as ex:  # noqa: BLE001
            chain = _unwrap(ex)[:8]
            entry["error"] = str(ex)[:200]
            entry["error_chain"] = chain
            print(f"[orch-mcp] {conn} FAILED chain={chain}", flush=True)
        report.append(entry)
    return healthy


async def _run_agent_async(endpoint: str, system_prompt: str,
                           messages: list[dict[str, Any]],
                           mcp_servers: list[dict[str, Any]],
                           user_token: str | None,
                           candidates: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    from contextlib import AsyncExitStack

    from agents import Agent, Runner, set_default_openai_api, set_default_openai_client
    from agents.tracing import set_trace_processors
    from databricks_openai import AsyncDatabricksOpenAI

    # Route the Agents SDK at Databricks model serving. chat_completions works
    # for all Databricks models except GPT-OSS. Disable OpenAI's own tracing
    # exporter (no API key in this environment).
    set_default_openai_client(AsyncDatabricksOpenAI())
    set_default_openai_api("chat_completions")
    set_trace_processors([])
    _patch_agents_sdk()  # (A) list-content coercion, (B) strip $schema from MCP tool schemas

    mcp_report: list[dict[str, Any]] = []
    print(f"[orch-mcp] user_token_present={bool(user_token)} "
          f"token_len={len(user_token) if user_token else 0}", flush=True)

    # Normalize the running chat thread into Agents-SDK input items.
    input_items: list[dict[str, Any]] = []
    for m in messages:
        role = m.get("role", "user")
        if role == "system":
            continue
        content = m.get("content", "")
        if isinstance(content, list):
            content = " ".join(p.get("text", "") for p in content if isinstance(p, dict))
        input_items.append({"role": role, "content": content})

    def _is_model_incompat_error(ex: Exception) -> str | None:
        """Return a short reason if this looks like a model-vs-runtime
        incompatibility we can recover from by switching models, else None.

        Covers two known JEV-picks-a-quirky-model cases:
          * Gemini rejects MCP tool JSON Schema carrying a "$schema" key.
          * Reasoning models (e.g. Claude Opus reasoning) return content as a
            list of blocks incl. {'type':'reasoning'} which the Agents SDK's
            ResponseOutputText.text (expects str) fails to validate.
        """
        s = str(ex)
        if ("$schema" in s) or ("function_declarations" in s and "Unknown name" in s):
            return "rejects MCP tool schemas"
        if ("ResponseOutputText" in s) or ("'type': 'reasoning'" in s) \
                or ("reasoning" in s and "valid string" in s):
            return "returns reasoning-block output the agent runtime can't parse"
        # Gemini on the chat_completions tool path misbehaves in several ways:
        #  * reuses tool-call IDs (Agents SDK ModelBehaviorError)
        #  * rejects structured function-response content (wants a String)
        # These all mean "this model can't drive tools on this runtime."
        if ("reused a completed tool call" in s) or ("unique call ID" in s) \
                or ("unique tool call id" in s.lower()):
            return "reuses tool-call IDs the agent runtime rejects"
        if ("Invalid function response" in s) \
                or ("Expecting 'content' to be a String" in s) \
                or ("Invalid message format" in s and "function" in s):
            return "can't drive MCP tools on this runtime"
        return None

    def _fallback_chain(bad: str) -> list[str]:
        """Ordered list of models to try after `bad` fails: JEV's own ranked
        next-best candidates first (honoring JEV's intent), then the known-good
        ORCH_TOOL_SAFE_MODEL as a final safety net. De-duplicated, `bad` removed.
        """
        safe = os.environ.get("ORCH_TOOL_SAFE_MODEL", "databricks-claude-haiku-4-5")
        chain: list[str] = []
        for c in (candidates or []):
            ep = c.get("model")
            if ep and ep != bad and ep not in chain:
                chain.append(ep)
        if safe != bad and safe not in chain:
            chain.append(safe)
        return chain

    async with AsyncExitStack() as stack:
        healthy = await _connect_healthy_mcp_servers(
            stack, mcp_servers, user_token, mcp_report)

        run_model = endpoint
        agent = Agent(
            name="JEV-composed agent",
            instructions=system_prompt,
            model=run_model,
            mcp_servers=healthy,
        )
        try:
            result = await Runner.run(agent, input_items)
        except Exception as ex:  # noqa: BLE001
            # Certain JEV-chosen models are incompatible with the Agents SDK
            # runtime (Gemini's tool-schema rejection; reasoning models' block
            # output shape). Retry once on a known-good model so the demo never
            # dies on model choice. Transparently reported.
            print(f"[orch] Runner.run raised on '{endpoint}': "
                  f"{type(ex).__name__}: {str(ex)[:600]}", flush=True)
            reason = _is_model_incompat_error(ex)
            if not reason:
                raise
            # Try JEV-ranked next-best models, then the safe model. Stop at the
            # first that runs. Only the two-deep chain to keep the demo snappy.
            chain = _fallback_chain(endpoint)[:3]
            last_reason, last_bad = reason, endpoint
            result = None
            for cand in chain:
                print(f"[orch] model '{last_bad}' {last_reason}; "
                      f"retrying on '{cand}' (JEV-ranked next-best)", flush=True)
                agent = Agent(
                    name="JEV-composed agent",
                    instructions=system_prompt,
                    model=cand,
                    mcp_servers=healthy,
                )
                try:
                    result = await Runner.run(agent, input_items)
                    run_model = cand
                    mcp_report.append({
                        "label": "(model fallback)", "connection": "", "loaded": True,
                        "tools": [],
                        "error": f"'{endpoint}' {reason}; ran on '{cand}'",
                    })
                    break
                except Exception as ex2:  # noqa: BLE001
                    r2 = _is_model_incompat_error(ex2)
                    print(f"[orch] fallback '{cand}' also failed: "
                          f"{type(ex2).__name__}: {str(ex2)[:200]}", flush=True)
                    if not r2:
                        raise
                    last_reason, last_bad = r2, cand
            if result is None:
                raise ex

        # Final text output. Prefer RunResult.final_output; fall back to the
        # last assistant message text in new_items across SDK versions.
        reply = ""
        try:
            fo = getattr(result, "final_output", None)
            if isinstance(fo, str):
                reply = fo
            elif fo is not None:
                reply = str(fo)
        except Exception:  # noqa: BLE001
            reply = ""
        if not reply:
            try:
                for item in reversed(getattr(result, "new_items", None) or []):
                    raw = getattr(item, "raw_item", None)
                    content = getattr(raw, "content", None) if raw is not None else None
                    if isinstance(content, list):
                        txt = " ".join(
                            getattr(b, "text", "") or (b.get("text", "") if isinstance(b, dict) else "")
                            for b in content)
                        if txt.strip():
                            reply = txt.strip()
                            break
                    elif isinstance(content, str) and content.strip():
                        reply = content.strip()
                        break
            except Exception:  # noqa: BLE001
                pass

        # Which tools actually got called (best-effort across SDK versions).
        tools_used: list[str] = []
        for item in (getattr(result, "new_items", None) or []):
            nm = (getattr(item, "type", "") or "")
            if "tool_call" in nm and "output" not in nm:
                raw = getattr(item, "raw_item", None)
                tname = getattr(raw, "name", None) if raw is not None else None
                if tname:
                    tools_used.append(tname)

    return {
        "reply": reply,
        "tools_used": sorted(set(tools_used)),
        "mcp_report": mcp_report,
        "run_model": run_model,
    }


# --------------------------------------------------------------------------- #
# Public sync entry point (called from the FastAPI /orch/run route)
# --------------------------------------------------------------------------- #
def build_and_run(spec: dict[str, Any], messages: list[dict[str, Any]],
                  base_prompt: str = "", user_token: str | None = None) -> dict[str, Any]:
    """Build an agent from `spec` and run it on `messages`.

    Returns {reply, model, tools_used, mcp_report, skill_names, error}.

    Synchronous wrapper: the OpenAI Agents SDK is async, and this is called from
    a sync FastAPI handler, so we drive it in a dedicated thread with its own
    event loop (safe: no already-running loop in that thread).
    """
    model_spec = spec.get("model") or {}
    endpoint = model_spec.get("model")
    if not endpoint:
        return {"error": "JEV did not choose a model.", "reply": None}

    skills = spec.get("skills") or []
    mcp_servers = spec.get("mcp_servers") or []
    candidates = model_spec.get("candidates") or []
    system_prompt = _system_prompt(base_prompt, skills)

    box: dict[str, Any] = {}

    def _worker():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            box["ok"] = loop.run_until_complete(
                _run_agent_async(endpoint, system_prompt, messages,
                                 mcp_servers, user_token, candidates))
        except Exception as ex:  # noqa: BLE001
            box["err"] = ex
        finally:
            try:
                loop.close()
            except Exception:  # noqa: BLE001
                pass

    th = threading.Thread(target=_worker, daemon=True)
    th.start()
    # Overall ceiling: MCP health-checks + a few agent steps. Generous but bounded.
    overall_timeout = float(os.environ.get("ORCH_RUN_TIMEOUT", "120"))
    th.join(timeout=overall_timeout)

    if th.is_alive():
        return {"error": f"Agent run timed out after {overall_timeout}s.",
                "reply": None, "model": endpoint,
                "skill_names": [s["label"] for s in skills if s.get("selected")]}
    if "err" in box:
        return {"error": f"Agent run failed: {str(box['err'])[:220]}",
                "reply": None, "model": endpoint,
                "mcp_report": [],
                "skill_names": [s["label"] for s in skills if s.get("selected")]}

    out = box.get("ok") or {}
    return {
        "reply": out.get("reply", ""),
        "model": endpoint,
        "run_model": out.get("run_model", endpoint),
        "tools_used": out.get("tools_used", []),
        "mcp_report": out.get("mcp_report", []),
        "skill_names": [s["label"] for s in skills if s.get("selected")],
    }
