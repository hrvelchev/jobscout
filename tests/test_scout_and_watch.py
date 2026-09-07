"""End-to-end scout run (fake sources + fake LLM through the REAL graph) and
the closed-posting watch."""

import json
from unittest.mock import AsyncMock

from conftest import FakeAnthropicClient, json_response, text_response
from fakes import FakeEmbedder
from test_models import make_posting
from test_sources import FakeHttp, FakeResponse

from jobscout.budget import Budget
from jobscout.pipeline.graph import build_graph
from jobscout.prefilter import Prefilter
from jobscout.scout import Scout
from jobscout.watch import check_applied_postings

GOOD_SCORE = json.dumps(
    {
        "fit_score": 85,
        "subscores": {"stack_match": 8, "seniority_gap": 0, "degree_gate": "none", "lane": "ai"},
        "red_flags": [],
        "cv_keywords": ["Python"],
        "reason": "fits",
    }
)
CLEAN_DRAFT = "Hi,\n\nGood fit - Python daily.\n\nBest, Alex"


class ListSource:
    def __init__(self, postings, degraded=()):
        self._postings = postings
        self.degraded = list(degraded)

    async def fetch(self):
        return self._postings


def make_scout(store, client, max_per_run=25):
    budget = Budget(store, max_per_day=100)
    graph = build_graph(client, store, budget, draft_threshold=70)
    prefilter = Prefilter(exclude_patterns=["php"], required_keywords=["python"])
    return Scout(
        store,
        FakeEmbedder(),
        prefilter,
        graph,
        profile="profile",
        notes_examples="notes",
        lane_cv_map={"ai": "cv_ai.pdf", "other": "cv_default.pdf"},
        max_per_run=max_per_run,
    )


async def test_scout_full_run_audits_everything(store):
    postings = [
        make_posting(external_id="ok", title="Python Dev", description="great python role"),
        make_posting(external_id="php", title="PHP Dev", description="php stuff"),
        make_posting(external_id="java", title="Java Dev", description="no snake here"),
    ]
    client = FakeAnthropicClient([json_response(GOOD_SCORE), text_response(CLEAN_DRAFT)])
    scout = make_scout(store, client)
    counts = await scout.run([ListSource(postings)])

    assert counts["fetched"] == 3 and counts["survivors"] == 1
    assert counts["drafted"] == 1
    verdicts = {k[1]: v["verdict"] for k, v in store.scan_log.items()}
    assert verdicts == {"ok": "drafted", "php": "excluded", "java": "no_lane_kw"}
    # posting scored + drafted end-to-end through the real graph
    pid = next(pid for pid, row in store.postings.items() if row["external_id"] == "ok")
    assert store.scores[pid].fit_score == 85
    assert (await store.latest_draft(pid))["cv_variant"] == "cv_ai.pdf"


async def test_scout_run_cap_rewrites_verdicts(store):
    postings = [
        make_posting(external_id=f"p{i}", title=f"Python Dev {i}", description="python")
        for i in range(3)
    ]
    client = FakeAnthropicClient([json_response(GOOD_SCORE), text_response(CLEAN_DRAFT)])
    scout = make_scout(store, client, max_per_run=1)
    counts = await scout.run([ListSource(postings)])
    assert counts["processed"] == 1
    over_cap = [v for v in store.scan_log.values() if v["verdict"] == "over_run_cap"]
    assert len(over_cap) == 2


async def test_scout_backlog_drains_over_cap_survivors(store):
    """Survivors beyond the run cap are exact dups on the next fetch, so the
    backlog drain is their only route to scoring - one per run here."""
    postings = [
        make_posting(external_id=f"p{i}", title=f"Python Dev {i}", description="python")
        for i in range(3)
    ]
    client = FakeAnthropicClient([json_response(GOOD_SCORE), text_response(CLEAN_DRAFT)] * 3)
    scout = make_scout(store, client, max_per_run=1)
    counts1 = await scout.run([ListSource(postings)])
    assert counts1["processed"] == 1 and counts1["backlog_processed"] == 0
    counts2 = await scout.run([ListSource(postings)])  # all exact dups now
    assert counts2["processed"] == 0 and counts2["backlog_processed"] == 1
    counts3 = await scout.run([ListSource([])])
    assert counts3["backlog_processed"] == 1
    assert len(store.scores) == 3  # every survivor eventually scored
    verdicts = [v["verdict"] for v in store.scan_log.values()]
    assert verdicts.count("drafted") == 3 and "over_run_cap" not in verdicts


async def test_scout_duplicate_audited_not_processed(store):
    posting = make_posting(external_id="dup1", title="Python Dev", description="python")
    client = FakeAnthropicClient([json_response(GOOD_SCORE), text_response(CLEAN_DRAFT)])
    scout = make_scout(store, client)
    await scout.run([ListSource([posting])])
    counts2 = await scout.run([ListSource([posting])])
    assert counts2["duplicates"] == 1 and counts2["processed"] == 0


# --- watch ------------------------------------------------------------------


async def test_watch_devbg_404_marks_closed_and_alerts(store):
    pid = await store.insert_posting(
        make_posting(external_id="d1", url="https://dev.bg/company/jobads/x/"), None
    )
    await store.set_status(pid, "applied")
    http = FakeHttp({})  # everything 404s
    notify = AsyncMock()
    closed = await check_applied_postings(store, http, notify)
    assert closed == 1
    assert store.postings[pid]["status"] == "closed"
    assert "CLOSED" in notify.await_args.args[0]


async def test_watch_greenhouse_absent_id_closed_present_id_kept(store):
    gone = await store.insert_posting(
        make_posting(source="greenhouse", external_id="acme:1", url="https://gh/1"), None
    )
    alive = await store.insert_posting(
        make_posting(source="greenhouse", external_id="acme:2", url="https://gh/2"), None
    )
    await store.set_status(gone, "applied")
    await store.set_status(alive, "applied")
    board = {"jobs": [{"id": 2}]}
    http = FakeHttp({"boards-api.greenhouse.io/v1/boards/acme/": FakeResponse(payload=board)})
    notify = AsyncMock()
    assert await check_applied_postings(store, http, notify) == 1
    assert store.postings[gone]["status"] == "closed"
    assert store.postings[alive]["status"] == "applied"


async def test_watch_board_error_does_not_false_alarm(store):
    pid = await store.insert_posting(
        make_posting(source="greenhouse", external_id="acme:1", url="https://gh/1"), None
    )
    await store.set_status(pid, "applied")
    http = FakeHttp({"boards-api.greenhouse.io/v1/boards/acme/": FakeResponse(status=500)})
    notify = AsyncMock()
    assert await check_applied_postings(store, http, notify) == 0
    assert store.postings[pid]["status"] == "applied"  # unknown != closed
