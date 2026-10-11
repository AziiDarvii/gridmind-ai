"""Claude client for agent reasoning and explanations.

The LLM never performs electrical calculations. It receives computed evidence
(and, in the tool loop, can call read-only tools that return solver data) and
writes explanations. Without ANTHROPIC_API_KEY every call falls back to a
deterministic template so the workflow still runs end to end.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Callable

import anthropic

from app.config import SETTINGS

log = logging.getLogger(__name__)

MAX_TOKENS = 16000
MAX_TOOL_ROUNDS = 6
FALLBACK_BETA = "server-side-fallback-2026-07-01"
ENGINEERING_SYSTEM = (
    "You are a senior power-system protection and planning engineer reviewing results from an AC power-flow "
    "engine. Use only the numbers provided or returned by tools; never invent values, ratings or lengths. "
    "Quote values with units (pu, kV, %, MW, Mvar). Be concise and specific. If evidence is insufficient, say so."
)


def _client() -> anthropic.Anthropic | None:
    if not SETTINGS.llm_enabled:
        return None
    return anthropic.Anthropic(max_retries=2, timeout=120.0)


def _text(response: Any) -> str:
    return "".join(b.text for b in response.content if b.type == "text").strip()


def _create(client: anthropic.Anthropic, **kwargs: Any) -> Any:
    return client.beta.messages.create(
        model=SETTINGS.anthropic_model, max_tokens=MAX_TOKENS, betas=[FALLBACK_BETA], fallbacks="default",
        output_config={"effort": "medium"}, **kwargs)


def _error_text(exc: Exception) -> str:
    if isinstance(exc, anthropic.AuthenticationError):
        return "Claude API authentication failed (check ANTHROPIC_API_KEY)"
    if isinstance(exc, anthropic.RateLimitError):
        return "Claude API rate limited"
    if isinstance(exc, anthropic.APIStatusError):
        return f"Claude API error {exc.status_code}"
    if isinstance(exc, anthropic.APIConnectionError):
        return "Claude API unreachable"
    return f"Claude call failed: {type(exc).__name__}"


def narrate(task: str, evidence: dict, fallback: str) -> dict:
    """Single call: explain computed evidence. Returns text plus provenance."""
    client = _client()
    if client is None:
        return {"text": fallback, "used_llm": False, "model": None, "note": "LLM disabled (no ANTHROPIC_API_KEY)"}
    try:
        response = _create(client, system=ENGINEERING_SYSTEM, messages=[{
            "role": "user", "content": f"{task}\n\nComputed evidence (JSON):\n{json.dumps(evidence, default=str)[:60000]}"}])
        if response.stop_reason == "refusal":
            return {"text": fallback, "used_llm": False, "model": response.model, "note": "model declined; template used"}
        return {"text": _text(response) or fallback, "used_llm": True, "model": response.model,
                "request_id": getattr(response, "_request_id", None)}
    except anthropic.APIError as exc:
        log.warning("narrate failed: %s", exc)
        return {"text": fallback, "used_llm": False, "model": None, "note": _error_text(exc)}


def tool_loop(task: str, tools: list[dict], handlers: dict[str, Callable[[dict], Any]], fallback: str) -> dict:
    """Manual agentic loop: Claude may call read-only solver tools, bounded rounds."""
    client = _client()
    if client is None:
        return {"text": fallback, "used_llm": False, "tool_calls": [], "note": "LLM disabled (no ANTHROPIC_API_KEY)"}
    messages: list[dict] = [{"role": "user", "content": task}]
    calls: list[dict] = []
    try:
        for _ in range(MAX_TOOL_ROUNDS):
            response = _create(client, system=ENGINEERING_SYSTEM, tools=tools, messages=messages)
            if response.stop_reason == "refusal":
                return {"text": fallback, "used_llm": False, "tool_calls": calls, "note": "model declined"}
            if response.stop_reason != "tool_use":
                return {"text": _text(response) or fallback, "used_llm": True, "tool_calls": calls,
                        "model": response.model}
            messages.append({"role": "assistant", "content": response.content})
            results = []
            for block in response.content:
                if block.type != "tool_use":
                    continue
                handler = handlers.get(block.name)
                try:
                    output = handler(dict(block.input)) if handler else {"error": f"unknown tool {block.name}"}
                    is_error = handler is None
                except (KeyError, ValueError, TypeError) as exc:
                    output, is_error = {"error": str(exc)}, True
                calls.append({"tool": block.name, "input": dict(block.input), "is_error": is_error})
                results.append({"type": "tool_result", "tool_use_id": block.id, "is_error": is_error,
                                "content": json.dumps(output, default=str)[:20000]})
            messages.append({"role": "user", "content": results})
        return {"text": fallback, "used_llm": True, "tool_calls": calls, "note": "tool-round budget exhausted"}
    except anthropic.APIError as exc:
        log.warning("tool loop failed: %s", exc)
        return {"text": fallback, "used_llm": False, "tool_calls": calls, "note": _error_text(exc)}
