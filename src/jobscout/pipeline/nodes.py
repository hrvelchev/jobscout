"""LangGraph nodes for the per-posting paid pipeline.

Each node is an async function over PipelineState; dependencies are bound via
functools.partial in graph.build_graph, so tests inject fakes freely. Every
paid call reserves budget BEFORE it fires (see budget.Budget).
"""

from __future__ import annotations

import dataclasses

import structlog

from jobscout.budget import Budget
from jobscout.models import PipelineState, ScoreResult
from jobscout.pipeline import llm, prompts
from jobscout.pipeline.sanitize import PLACEHOLDER, sanitize_draft
from jobscout.store import Store

log = structlog.get_logger()

OUTCOME_OVER_CAP = "over_daily_cap"
OUTCOME_SCORE_FAILED = "score_failed"
OUTCOME_BELOW_THRESHOLD = "scored_below_threshold"
OUTCOME_DRAFTED = "drafted"
OUTCOME_DRAFT_UNUSABLE = "draft_unusable"

DEFAULT_CV = "default"


async def reserve_node(state: PipelineState, *, budget: Budget) -> PipelineState:
    if not await budget.reserve():
        return {**state, "outcome": OUTCOME_OVER_CAP}
    return state


async def score_node(
    state: PipelineState, *, client, store: Store, budget: Budget
) -> PipelineState:
    system, user = prompts.render_score(state["profile"], state["posting_text"])
    text = await llm.complete(
        client, store, system=system, user=user, purpose="score", max_tokens=700
    )
    payload = llm.parse_json(text) if text is not None else None

    # one corrective retry on bad JSON - budget-honest: it reserves its own call
    if payload is None and text is not None and await budget.reserve():
        system, user = prompts.render_score(state["profile"], state["posting_text"], retry=True)
        text = await llm.complete(
            client, store, system=system, user=user, purpose="score_retry", max_tokens=700
        )
        payload = llm.parse_json(text) if text is not None else None

    if payload is None:
        log.warning("score_failed", posting_id=state.get("posting_id"))
        return {**state, "outcome": OUTCOME_SCORE_FAILED}

    score = ScoreResult.from_payload(payload)
    await store.save_score(state["posting_id"], score)
    return {**state, "score": dataclasses.asdict(score)}


async def gate_node(state: PipelineState, *, draft_threshold: int) -> PipelineState:
    score = state["score"]
    assert score is not None
    cv_map = state.get("lane_cv_map") or {}
    cv_variant = cv_map.get(score["lane"], cv_map.get("other", DEFAULT_CV))
    if score["fit_score"] < draft_threshold:
        return {**state, "cv_variant": cv_variant, "outcome": OUTCOME_BELOW_THRESHOLD}
    return {**state, "cv_variant": cv_variant}


async def draft_node(
    state: PipelineState, *, client, store: Store, budget: Budget
) -> PipelineState:
    if not await budget.reserve():
        # scored but wallet empty: posting still reaches the digest, sans draft
        return {**state, "outcome": OUTCOME_OVER_CAP}
    score = state["score"]
    assert score is not None
    system, user = prompts.render_draft(
        state["profile"],
        state.get("notes_examples", ""),
        state["posting_text"],
        score["cv_keywords"],
        sanitize_note=state.get("sanitize_note"),
    )
    text = await llm.complete(
        client, store, system=system, user=user, purpose="draft", max_tokens=500
    )
    return {
        **state,
        "draft": text,
        "draft_attempts": state.get("draft_attempts", 0) + 1,
        "sanitize_note": None,
    }


async def sanitize_node(state: PipelineState, *, store: Store) -> PipelineState:
    draft = state.get("draft")
    if draft is None:  # the API call itself failed
        await store.save_draft(state["posting_id"], state["cv_variant"] or DEFAULT_CV, PLACEHOLDER)
        return {**state, "outcome": OUTCOME_DRAFT_UNUSABLE}

    cleaned, violation = sanitize_draft(draft)
    if violation and state.get("draft_attempts", 0) < 2:
        log.info("draft_sanitized_retrying", violation=violation)
        return {**state, "sanitize_note": violation}

    if violation:
        # retried and still dirty: keep the mechanically cleaned version, but
        # surface the failure honestly instead of pretending it is fine
        await store.save_draft(state["posting_id"], state["cv_variant"] or DEFAULT_CV, cleaned)
        log.warning("draft_unusable_after_retry", posting_id=state.get("posting_id"))
        return {**state, "draft": cleaned, "outcome": OUTCOME_DRAFT_UNUSABLE}

    await store.save_draft(state["posting_id"], state["cv_variant"] or DEFAULT_CV, cleaned)
    return {**state, "draft": cleaned, "outcome": OUTCOME_DRAFTED}
