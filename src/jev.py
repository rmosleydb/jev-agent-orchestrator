"""
JEV (TypeSafe System One) client + composition helpers.

JEV is NOT an LLM. Given a `state` (the incoming request/conversation) and a
set of typed `questions`, it returns typed, calibrated decisions. All questions
in one call are evaluated in parallel and in isolation against the same state,
so a single round trip can decide: which model, which MCP servers, and which
skills to compose into an agent.

Primitives used here:
  - choice : pick exactly one option   -> {choice, probabilities, confidence}
  - noul   : P(statement is true) 0..1 -> {noul, confidence}

Composition strategy:
  * model   -> ONE `choice` question over model descriptions.
  * mcp/skill -> ONE `noul` question PER candidate. Everything whose noul clears
    a threshold is selected ("anything with meaningful signal gets selected").
"""
from __future__ import annotations

import json
import os
import time
from typing import Any

import requests

JEV_API_URL = os.environ.get("JEV_API_URL", "https://api.typesafe.ai/v1/systemone")
JEV_MODEL = os.environ.get("JEV_MODEL", "jev-latest")
JEV_TIMEOUT = float(os.environ.get("JEV_TIMEOUT", "45"))
JEV_API_KEY = os.environ.get("JEV_API_KEY") or os.environ.get("TYPESAFE_API_KEY")

# Anything scoring at/above this noul is selected.
SELECT_THRESHOLD = float(os.environ.get("JEV_SELECT_THRESHOLD", "0.4"))

_session = requests.Session()


def _post(payload: dict[str, Any]) -> tuple[dict[str, Any], float]:
    t0 = time.perf_counter()
    resp = _session.post(
        JEV_API_URL,
        headers={"Authorization": f"Bearer {JEV_API_KEY}", "Content-Type": "application/json"},
        data=json.dumps(payload),
        timeout=JEV_TIMEOUT,
    )
    latency_ms = round((time.perf_counter() - t0) * 1000, 1)
    resp.raise_for_status()
    return resp.json(), latency_ms


def compose(
    state_text: str,
    models: list[dict[str, str]],
    mcp_servers: list[dict[str, str]],
    skills: list[dict[str, str]],
    threshold: float | None = None,
) -> dict[str, Any]:
    """Ask JEV to compose an agent for `state_text` in a single call.

    models      : [{"label","description","model"}]  -> one `choice`
    mcp_servers : [{"label","description", ...}]      -> one `noul` each
    skills      : [{"label","description", ...}]      -> one `noul` each

    Returns a spec dict:
      {
        "model": {"label","model","confidence","probabilities"},
        "mcp_servers": [ {..server.., "noul","selected"} ],
        "skills":      [ {..skill..,  "noul","selected"} ],
        "latency_ms": float,
      }
    """
    thr = SELECT_THRESHOLD if threshold is None else threshold
    questions: dict[str, Any] = {}

    if models:
        questions["model"] = {
            "type": "choice",
            "instructions": (
                "Choose the single best model to handle the most recent user "
                "request, based on each model's description."
            ),
            "criteria": {m["label"]: (m.get("description") or m["label"]) for m in models},
        }

    for i, s in enumerate(mcp_servers):
        questions[f"mcp_{i}"] = {
            "type": "noul",
            "instructions": (
                f"Handling this request would benefit from the '{s['label']}' tool. "
                f"That tool: {s.get('description') or s['label']}"
            ),
        }

    for i, s in enumerate(skills):
        questions[f"skill_{i}"] = {
            "type": "noul",
            "instructions": (
                f"This request should follow the '{s['label']}' skill/playbook. "
                f"That skill: {s.get('description') or s['label']}"
            ),
        }

    payload = {
        "model": JEV_MODEL,
        "state": {"request": state_text},
        "questions": questions,
    }
    body, latency_ms = _post(payload)
    answers = body.get("answers") or {}

    # ---- model choice ----
    model_out = None
    if models and "model" in answers:
        a = answers["model"]
        chosen = a.get("choice")
        match = next((m for m in models if m["label"] == chosen), None)
        probs = a.get("probabilities") or {}
        # Ranked candidate list (label -> endpoint), highest probability first.
        # Used by the agent runtime for a smart, JEV-ranked model fallback when
        # the top pick is incompatible with the runtime (instead of a fixed model).
        by_label = {m["label"]: m.get("model") for m in models}
        candidates = [
            {"label": lbl, "model": by_label.get(lbl), "probability": round(float(p), 4)}
            for lbl, p in sorted(probs.items(), key=lambda kv: -float(kv[1]))
            if by_label.get(lbl)
        ]
        model_out = {
            "label": chosen,
            "model": (match or {}).get("model"),
            "confidence": a.get("confidence"),
            "probabilities": probs,
            "candidates": candidates,
        }

    # ---- mcp servers ----
    mcp_out = []
    for i, s in enumerate(mcp_servers):
        a = answers.get(f"mcp_{i}", {})
        noul = float(a.get("noul") or 0.0)
        mcp_out.append({**s, "noul": round(noul, 4), "selected": noul >= thr})

    # ---- skills ----
    skill_out = []
    for i, s in enumerate(skills):
        a = answers.get(f"skill_{i}", {})
        noul = float(a.get("noul") or 0.0)
        skill_out.append({**s, "noul": round(noul, 4), "selected": noul >= thr})

    return {
        "model": model_out,
        "mcp_servers": mcp_out,
        "skills": skill_out,
        "threshold": thr,
        "latency_ms": latency_ms,
    }
