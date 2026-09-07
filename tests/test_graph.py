"""The pipeline's branching is the product - these tests pin every path.

FakeAnthropicClient records each request, so tests assert not only outcomes
but exactly how many paid calls happened and what was in them.
"""

import json

from conftest import FakeAnthropicClient, json_response, text_response

from jobscout.budget import Budget
from jobscout.pipeline.graph import build_graph
from jobscout.pipeline.sanitize import PLACEHOLDER

GOOD_SCORE = json.dumps(
    {
        "fit_score": 85,
        "subscores": {"stack_match": 8, "seniority_gap": 0, "degree_gate": "none", "lane": "ai"},
        "red_flags": [],
        "cv_keywords": ["Python", "RAG"],
        "reason": "fits",
    }
)
LOW_SCORE = json.dumps(
    {
        "fit_score": 40,
        "subscores": {"stack_match": 3, "seniority_gap": 2, "degree_gate": "hard", "lane": "quant"},
        "red_flags": ["degree required"],
        "cv_keywords": [],
        "reason": "poor fit",
    }
)
CLEAN_DRAFT = "Hi,\n\nI fit this role - Python and RAG are my daily work.\n\nBest, Alex"
DIRTY_DRAFT = "Hi — check https://example.com — thanks"

LANE_CV = {"ai": "cv_ai.pdf", "quant": "cv_quant.pdf", "other": "cv_default.pdf"}


def base_state(posting_id: int = 1) -> dict:
    return {
        "posting_id": posting_id,
        "posting_text": "ML Engineer at Acme: Python, RAG, Postgres.",
        "profile": "candidate profile text",
        "notes_examples": "--- example note ---\nHi...",
        "lane_cv_map": LANE_CV,
        "draft_attempts": 0,
    }


async def run_graph(store, client, *, cap=10, threshold=70, posting_id=1):
    budget = Budget(store, max_per_day=cap)
    graph = build_graph(client, store, budget, draft_threshold=threshold)
    return await graph.ainvoke(base_state(posting_id))


async def seed_posting(store) -> int:
    from test_models import make_posting

    pid = await store.insert_posting(make_posting(), None)
    assert pid is not None
    return pid


async def test_happy_path_scores_and_drafts(store):
    pid = await seed_posting(store)
    client = FakeAnthropicClient([json_response(GOOD_SCORE), text_response(CLEAN_DRAFT)])
    state = await run_graph(store, client, posting_id=pid)
    assert state["outcome"] == "drafted"
    assert len(client.calls) == 2  # exactly one score + one draft call
    assert state["cv_variant"] == "cv_ai.pdf"
    assert store.scores[pid].fit_score == 85
    draft = await store.latest_draft(pid)
    assert draft["note_text"] == CLEAN_DRAFT and draft["cv_variant"] == "cv_ai.pdf"


async def test_below_threshold_never_pays_for_draft(store):
    pid = await seed_posting(store)
    client = FakeAnthropicClient([json_response(LOW_SCORE)])
    state = await run_graph(store, client, posting_id=pid)
    assert state["outcome"] == "scored_below_threshold"
    assert len(client.calls) == 1  # score only - no draft spend
    assert store.scores[pid].fit_score == 40  # score still saved for the digest
    assert state["cv_variant"] == "cv_quant.pdf"  # lane mapping still applied


async def test_empty_wallet_makes_zero_calls(store):
    pid = await seed_posting(store)
    client = FakeAnthropicClient([json_response(GOOD_SCORE)])
    state = await run_graph(store, client, cap=0, posting_id=pid)
    assert state["outcome"] == "over_daily_cap"
    assert client.calls == []  # reserve failed BEFORE any request went out


async def test_bad_json_retries_once_then_fails(store):
    pid = await seed_posting(store)
    client = FakeAnthropicClient([text_response("not json at all")])
    state = await run_graph(store, client, posting_id=pid)
    assert state["outcome"] == "score_failed"
    assert len(client.calls) == 2  # original + one corrective retry
    assert "ONLY the JSON" in client.calls[1]["messages"][0]["content"]
    assert pid not in store.scores


async def test_dirty_draft_retries_with_feedback_then_succeeds(store):
    pid = await seed_posting(store)
    client = FakeAnthropicClient(
        [
            json_response(GOOD_SCORE),
            text_response(DIRTY_DRAFT),
            text_response(CLEAN_DRAFT),
        ]
    )
    state = await run_graph(store, client, posting_id=pid)
    assert state["outcome"] == "drafted"
    assert len(client.calls) == 3
    retry_prompt = client.calls[2]["messages"][0]["content"]
    assert "broke a rule" in retry_prompt  # corrective feedback was injected
    assert (await store.latest_draft(pid))["note_text"] == CLEAN_DRAFT


async def test_still_dirty_after_retry_keeps_cleaned_and_flags(store):
    pid = await seed_posting(store)
    client = FakeAnthropicClient([json_response(GOOD_SCORE), text_response(DIRTY_DRAFT)])
    # the single draft response repeats forever -> retry is dirty too
    state = await run_graph(store, client, posting_id=pid)
    assert state["outcome"] == "draft_unusable"
    draft = await store.latest_draft(pid)
    assert "https://" not in draft["note_text"] and "—" not in draft["note_text"]


async def test_draft_call_failure_saves_placeholder(store):
    pid = await seed_posting(store)
    client = FakeAnthropicClient([json_response(GOOD_SCORE), RuntimeError("api down")])
    state = await run_graph(store, client, posting_id=pid)
    assert state["outcome"] == "draft_unusable"
    assert (await store.latest_draft(pid))["note_text"] == PLACEHOLDER


async def test_wallet_shared_between_score_and_draft(store):
    pid = await seed_posting(store)
    client = FakeAnthropicClient([json_response(GOOD_SCORE), text_response(CLEAN_DRAFT)])
    state = await run_graph(store, client, cap=1, posting_id=pid)
    # one call allowed: the score happens, the draft is refused
    assert state["outcome"] == "over_daily_cap"
    assert len(client.calls) == 1
    assert store.scores[pid].fit_score == 85  # scored posting survives to digest
