"""Deterministic ranking over scored postings. No LLM here - the formula is
inspectable, testable and cheap to change."""

from __future__ import annotations

from datetime import datetime
from typing import Any

FRESH_48H_BONUS = 8
FRESH_7D_BONUS = 4
SALARY_POSTED_BONUS = 5
DREAM_COMPANY_BONUS = 15
STALE_PENALTY = 10
STALE_AFTER_DAYS = 21


def rank_value(
    row: dict[str, Any],
    *,
    lane_weights: dict[str, int],
    dream_companies: list[str],
    now: datetime,
) -> float:
    score = row["score"]
    value = float(score.fit_score)

    posted_at = row.get("posted_at") or row.get("fetched_at")
    if posted_at is not None:
        age_days = (now - posted_at).total_seconds() / 86_400
        if age_days < 2:
            value += FRESH_48H_BONUS
        elif age_days < 7:
            value += FRESH_7D_BONUS
        if age_days > STALE_AFTER_DAYS:
            value -= STALE_PENALTY

    if row.get("salary_min") is not None:
        value += SALARY_POSTED_BONUS

    value += lane_weights.get(score.lane, 0)

    if row.get("company_norm") in dream_companies:
        value += DREAM_COMPANY_BONUS

    return value


def rank(
    rows: list[dict[str, Any]],
    *,
    lane_weights: dict[str, int],
    dream_companies: list[str],
    now: datetime,
    top_n: int,
) -> list[dict[str, Any]]:
    decorated = [
        {**row, "rank_value": rank_value(
            row, lane_weights=lane_weights, dream_companies=dream_companies, now=now
        )}
        for row in rows
    ]
    decorated.sort(key=lambda r: r["rank_value"], reverse=True)
    return decorated[:top_n]
