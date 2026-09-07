"""Thin LLM helper: one-shot completion with usage logging, never raises."""

from __future__ import annotations

import json
import re
from typing import Any

import structlog

log = structlog.get_logger()

MODEL = "claude-haiku-4-5"
COST_PER_MTOK_IN = 1.00
COST_PER_MTOK_OUT = 5.00

_FENCE_RE = re.compile(r"^```[a-z]*\n?|```$", re.MULTILINE)


def strip_code_fence(text: str) -> str:
    return _FENCE_RE.sub("", text).strip()


def parse_json(text: str) -> dict[str, Any] | None:
    try:
        payload = json.loads(strip_code_fence(text))
    except (json.JSONDecodeError, TypeError):
        return None
    return payload if isinstance(payload, dict) else None


async def complete(
    client, store, *, system: str, user: str, purpose: str, max_tokens: int = 700
) -> str | None:
    """One messages.create call. Logs cost; returns joined text or None on any
    API failure - a single bad call must not kill the scout run."""
    try:
        response = await client.messages.create(
            model=MODEL,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
    except Exception as exc:  # noqa: BLE001 - boundary with the outside world
        log.warning("llm_call_failed", purpose=purpose, error=str(exc))
        return None
    usage = getattr(response, "usage", None)
    if usage is not None:
        cost = (
            usage.input_tokens * COST_PER_MTOK_IN + usage.output_tokens * COST_PER_MTOK_OUT
        ) / 1_000_000
        await store.log_usage(MODEL, usage.input_tokens, usage.output_tokens, cost, purpose)
    return "".join(block.text for block in response.content if getattr(block, "type", "") == "text")
