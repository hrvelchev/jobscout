"""Integration tests against real Postgres + pgvector (marker: pg).

Run: docker compose up -d db && pytest -m pg
The same Store contract the unit suite exercises via FakeStore.
"""

import asyncio
import json
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest

from jobscout.config import Settings
from jobscout.models import RawPosting, ScoreResult
from jobscout.store.postgres import PostgresStore

pytestmark = pytest.mark.pg

TABLES = ("events", "drafts", "scores", "scan_log", "usage_log", "app_state", "postings")


async def _test_dsn() -> str:
    """Never run destructive tests against the live database: a local .env
    points POSTGRES_DB at it, so force a separate *_test db (creating it on
    first use). CI already provisions 'jobscout_test' directly."""
    settings = Settings()
    if not settings.postgres_db.endswith("_test"):
        admin = await asyncpg.connect(dsn=settings.dsn)
        test_db = f"{settings.postgres_db}_test"
        try:
            exists = await admin.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", test_db)
            if not exists:
                await admin.execute(f'CREATE DATABASE "{test_db}"')
        finally:
            await admin.close()
        settings.postgres_db = test_db
    return settings.dsn


@pytest.fixture
async def pg():
    store = PostgresStore(await _test_dsn())
    await store.connect()
    for table in TABLES:
        await store.pool.execute(f"TRUNCATE {table} RESTART IDENTITY CASCADE")
    yield store
    await store.close()


def make_posting(**overrides) -> RawPosting:
    defaults = dict(
        source="devbg",
        external_id="x1",
        url="https://example.com/1",
        company="Acme Ltd",
        title="Data Engineer",
        description="pipelines",
    )
    defaults.update(overrides)
    return RawPosting(**defaults)


VEC_A = [1.0] + [0.0] * 383
VEC_A_CLOSE = [0.96, 0.28] + [0.0] * 382  # cosine 0.96
VEC_FAR = [0.0, 1.0] + [0.0] * 382


async def test_schema_init_is_idempotent(pg):
    # connect() ran the schema once; running the whole file again must be a no-op
    from importlib import resources

    schema = resources.files("jobscout.store").joinpath("schema.sql").read_text("utf-8")
    await pg.pool.execute(schema)


async def test_unique_conflict_returns_none(pg):
    assert await pg.insert_posting(make_posting(), None) == 1
    assert await pg.insert_posting(make_posting(), None) is None


async def test_vector_roundtrip_and_semantic_query(pg):
    await pg.insert_posting(make_posting(external_id="a"), VEC_A)  # source=devbg
    # other source, same company, cosine 0.96 -> duplicate found
    assert await pg.find_semantic_dup(VEC_A_CLOSE, "acme", "greenhouse") == 1
    # same source never dups: a board's own similar roles are distinct jobs
    assert await pg.find_semantic_dup(VEC_A_CLOSE, "acme", "devbg") is None
    # different company, same vector -> no duplicate
    assert await pg.find_semantic_dup(VEC_A_CLOSE, "initech", "greenhouse") is None
    # same company, orthogonal vector -> no duplicate
    assert await pg.find_semantic_dup(VEC_FAR, "acme", "greenhouse") is None


async def test_unscored_new_postings_backlog(pg):
    scored = await pg.insert_posting(make_posting(external_id="b1"), None)
    pending = await pg.insert_posting(make_posting(external_id="b2"), None)
    await pg.insert_posting(make_posting(external_id="b3"), None)  # prefilter-rejected
    stranded = await pg.insert_posting(make_posting(external_id="b4"), None)  # crash leftover
    await pg.record_scan("devbg", "b1", "over_run_cap")
    await pg.record_scan("devbg", "b2", "over_run_cap")
    await pg.record_scan("devbg", "b3", "excluded")
    await pg.record_scan("devbg", "b4", "passed")
    await pg.save_score(
        scored,
        ScoreResult(
            fit_score=50,
            stack_match=5,
            seniority_gap=0,
            degree_gate="none",
            lane="ai",
            red_flags=[],
            cv_keywords=[],
            reason="ok",
        ),
    )
    rows = await pg.unscored_new_postings(10)
    assert [r["posting_id"] for r in rows] == [pending, stranded]


async def test_applications_tracker_rows(pg):
    pid = await pg.insert_posting(make_posting(salary_raw="3000 lv"), None)
    await pg.save_score(
        pid,
        ScoreResult(
            fit_score=82,
            stack_match=8,
            seniority_gap=0,
            degree_gate="none",
            lane="ai",
            red_flags=[],
            cv_keywords=[],
            reason="ok",
        ),
    )
    await pg.set_status(pid, "applied", note="test")
    rows = await pg.applications()
    assert len(rows) == 1
    row = rows[0]
    assert row["fit_score"] == 82 and row["lane"] == "ai"
    assert row["salary_raw"] == "3000 lv"
    assert row["applied_at"] is not None
    await pg.set_status(pid, "closed")
    assert (await pg.applications())[0]["status"] == "closed"


async def test_bump_counter_atomic_under_concurrency(pg):
    results = await asyncio.gather(*(pg.bump_counter("k", 1) for _ in range(50)))
    assert await pg.get_state("k") == "50"
    assert sorted(results) == list(range(1, 51))  # every increment observed exactly once


async def test_set_status_writes_event(pg):
    pid = await pg.insert_posting(make_posting(), None)
    await pg.set_status(pid, "applied", note="via test")
    row = await pg.pool.fetchrow("SELECT * FROM events WHERE posting_id = $1", pid)
    assert row["from_status"] == "new" and row["to_status"] == "applied"


async def test_applications_view_and_same_company_guard(pg):
    pid = await pg.insert_posting(make_posting(company="Acme Ltd"), None)
    await pg.save_draft(pid, "cv_ai.pdf", "note")
    await pg.set_status(pid, "applied")
    rows = await pg.pool.fetch("SELECT * FROM applications")
    assert len(rows) == 1 and rows[0]["cv_variant"] == "cv_ai.pdf"
    assert await pg.applied_same_company_since("acme", days=90) is True
    assert await pg.applied_same_company_since("initech", days=90) is False


async def test_eligible_for_digest_honors_snooze(pg):
    now = datetime.now(UTC)
    pid = await pg.insert_posting(make_posting(), None)
    await pg.save_score(
        pid,
        ScoreResult(
            fit_score=80,
            stack_match=8,
            seniority_gap=0,
            degree_gate="none",
            lane="ai",
            red_flags=[],
            cv_keywords=["Python"],
            reason="ok",
        ),
    )
    assert len(await pg.eligible_for_digest(now)) == 1
    await pg.set_snooze(pid, now + timedelta(days=3))
    assert await pg.eligible_for_digest(now) == []
    assert len(await pg.eligible_for_digest(now + timedelta(days=4))) == 1
    row = (await pg.eligible_for_digest(now + timedelta(days=4)))[0]
    assert row["score"].cv_keywords == ["Python"]


async def test_scan_log_upsert_semantics(pg):
    await pg.record_scan("devbg", "e1", "passed", url="u", company="c", title="t")
    await pg.record_scan("devbg", "e1", "excluded")  # first verdict wins inserts
    row = await pg.pool.fetchrow("SELECT * FROM scan_log")
    assert row["verdict"] == "passed"
    await pg.update_scan_verdict("devbg", "e1", "scored", "ok")
    row = await pg.pool.fetchrow("SELECT * FROM scan_log")
    assert row["verdict"] == "scored"


async def test_usage_log_and_since(pg):
    await pg.log_usage("claude-haiku-4-5", 1000, 200, 0.002, "score")
    n, total = await pg.usage_since(datetime.now(UTC) - timedelta(hours=1))
    assert n == 1 and abs(total - 0.002) < 1e-9
    # jsonb columns round-trip through save_score too
    pid = await pg.insert_posting(make_posting(external_id="j1"), None)
    await pg.save_score(
        pid,
        ScoreResult(
            fit_score=70,
            stack_match=5,
            seniority_gap=1,
            degree_gate="soft",
            lane="data",
            red_flags=["x"],
            cv_keywords=["SQL"],
            reason="r",
        ),
    )
    raw = await pg.pool.fetchrow("SELECT red_flags FROM scores WHERE posting_id = $1", pid)
    assert json.loads(raw["red_flags"]) == ["x"]
