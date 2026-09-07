from datetime import datetime, timedelta

from jobscout.models import ScoreResult
from jobscout.ranking import rank, rank_value

NOW = datetime(2026, 9, 8, 12, 0)
WEIGHTS = {"ai": 5, "data": 3, "other": 0}


def make_row(fit=70, lane="other", posted_days_ago=10.0, salary_min=None, company="acme"):
    return {
        "score": ScoreResult(
            fit_score=fit, stack_match=5, seniority_gap=0, degree_gate="none", lane=lane
        ),
        "posted_at": NOW - timedelta(days=posted_days_ago),
        "salary_min": salary_min,
        "company_norm": company,
    }


def value(row, dreams=()):
    return rank_value(row, lane_weights=WEIGHTS, dream_companies=list(dreams), now=NOW)


def test_baseline_is_fit_score():
    assert value(make_row(fit=70)) == 70


def test_freshness_bonuses():
    assert value(make_row(posted_days_ago=1)) == 70 + 8
    assert value(make_row(posted_days_ago=5)) == 70 + 4
    assert value(make_row(posted_days_ago=10)) == 70


def test_stale_penalty():
    assert value(make_row(posted_days_ago=30)) == 70 - 10


def test_salary_and_lane_and_dream_bonuses():
    row = make_row(lane="ai", salary_min=3000, posted_days_ago=10, company="initech")
    assert value(row, dreams=["initech"]) == 70 + 5 + 5 + 15


def test_rank_orders_and_caps():
    rows = [
        make_row(fit=60, posted_days_ago=1),  # 68
        make_row(fit=90, posted_days_ago=30),  # 80
        make_row(fit=75, lane="ai", posted_days_ago=10),  # 80 -> tie, stable enough
        make_row(fit=50, posted_days_ago=10),  # 50
    ]
    top = rank(rows, lane_weights=WEIGHTS, dream_companies=[], now=NOW, top_n=2)
    assert len(top) == 2
    assert all(t["rank_value"] == 80 for t in top)


def test_missing_posted_at_falls_back_to_fetched_at():
    row = make_row()
    row["posted_at"] = None
    row["fetched_at"] = NOW - timedelta(days=1)
    assert value(row) == 70 + 8
