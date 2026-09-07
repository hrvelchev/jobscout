"""In-memory fakes implementing the app's seams: Store and Embedder.

FakeStore implements the full Store protocol over dicts - unit tests exercise
the real pipeline logic with zero I/O. The pg-marked integration tests run the
same contract against real Postgres.
"""

from __future__ import annotations

import hashlib
import math
import random
from datetime import UTC, datetime, timedelta
from typing import Any

from jobscout.models import RawPosting, ScoreResult


class FakeEmbedder:
    """Deterministic: same text -> same unit vector. `overrides` lets a test
    pin exact vectors to control cosine similarity precisely."""

    def __init__(self, dim: int = 8, overrides: dict[str, list[float]] | None = None):
        self.dim = dim
        self.overrides = overrides or {}

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._one(t) for t in texts]

    def _one(self, text: str) -> list[float]:
        if text in self.overrides:
            return self.overrides[text]
        seed = int.from_bytes(hashlib.sha256(text.encode()).digest()[:8], "big")
        rng = random.Random(seed)
        vec = [rng.uniform(-1, 1) for _ in range(self.dim)]
        norm = math.sqrt(sum(x * x for x in vec)) or 1.0
        return [x / norm for x in vec]


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(x * x for x in b)) or 1.0
    return dot / (na * nb)


class FakeStore:
    def __init__(self) -> None:
        self.postings: dict[int, dict[str, Any]] = {}
        self.scores: dict[int, ScoreResult] = {}
        self.drafts: list[dict[str, Any]] = []
        self.events: list[dict[str, Any]] = []
        self.scan_log: dict[tuple[str, str], dict[str, Any]] = {}
        self.state: dict[str, str] = {}
        self.usage: list[dict[str, Any]] = []
        self._next_id = 1

    # --- postings -----------------------------------------------------------
    async def insert_posting(
        self, posting: RawPosting, embedding: list[float] | None
    ) -> int | None:
        for row in self.postings.values():
            if row["source"] == posting.source and row["external_id"] == posting.external_id:
                return None
        pid = self._next_id
        self._next_id += 1
        self.postings[pid] = {
            "posting_id": pid,
            "source": posting.source,
            "external_id": posting.external_id,
            "url": posting.url,
            "company": posting.company,
            "company_norm": posting.company_norm,
            "title": posting.title,
            "description": posting.description,
            "location": posting.location,
            "remote": posting.remote,
            "salary_raw": posting.salary_raw,
            "salary_min": posting.salary_min,
            "salary_max": posting.salary_max,
            "posted_at": posting.posted_at,
            "fetched_at": datetime.now(UTC),
            "embedding": embedding,
            "duplicate_of": None,
            "status": "new",
            "snooze_until": None,
        }
        return pid

    async def find_semantic_dup(
        self, embedding: list[float], company_norm: str, source: str, threshold: float = 0.90
    ) -> int | None:
        best: tuple[float, int] | None = None
        for pid, row in self.postings.items():
            if row["embedding"] is None or row["duplicate_of"] is not None:
                continue
            if row["source"] == source:
                continue
            other = row["company_norm"]
            if not (other == company_norm or other in company_norm or company_norm in other):
                continue
            sim = cosine(embedding, row["embedding"])
            if sim >= threshold and (best is None or sim > best[0]):
                best = (sim, pid)
        return best[1] if best else None

    async def mark_duplicate(self, posting_id: int, duplicate_of: int) -> None:
        self.postings[posting_id]["duplicate_of"] = duplicate_of

    async def get_posting(self, posting_id: int) -> dict[str, Any] | None:
        return self.postings.get(posting_id)

    async def set_status(self, posting_id: int, status: str, note: str | None = None) -> None:
        row = self.postings[posting_id]
        await self.add_event(
            "status_change", posting_id, from_status=row["status"], to_status=status, note=note
        )
        row["status"] = status

    async def set_snooze(self, posting_id: int, until: datetime) -> None:
        self.postings[posting_id]["snooze_until"] = until
        await self.set_status(posting_id, "snoozed")

    async def postings_with_status(self, *statuses: str) -> list[dict[str, Any]]:
        return [r for r in self.postings.values() if r["status"] in statuses]

    async def unscored_new_postings(self, limit: int) -> list[dict[str, Any]]:
        rows = [
            r
            for pid, r in sorted(self.postings.items())
            if r["status"] == "new"
            and r["duplicate_of"] is None
            and pid not in self.scores
            and self.scan_log.get((r["source"], r["external_id"]), {}).get("verdict")
            in ("over_run_cap", "over_daily_cap", "passed")
        ]
        return rows[:limit]

    async def eligible_for_digest(self, now: datetime) -> list[dict[str, Any]]:
        out = []
        for pid, row in self.postings.items():
            if row["duplicate_of"] is not None:
                continue
            if row["status"] not in ("scored", "snoozed"):
                continue
            if row["status"] == "snoozed" and row["snooze_until"] and row["snooze_until"] > now:
                continue
            score = self.scores.get(pid)
            if score is None:
                continue
            out.append({**row, "score": score})
        return out

    async def applied_same_company_since(self, company_norm: str, days: int) -> bool:
        cutoff = datetime.now(UTC) - timedelta(days=days)
        for ev in self.events:
            if ev["event_type"] != "status_change" or ev["to_status"] != "applied":
                continue
            if ev["created_at"] < cutoff:
                continue
            row = self.postings.get(ev["posting_id"])
            if row and row["company_norm"] == company_norm:
                return True
        return False

    # --- pipeline artifacts -------------------------------------------------
    async def save_score(self, posting_id: int, score: ScoreResult) -> None:
        if not isinstance(score, ScoreResult):
            # PostgresStore reads attributes off this object - a permissive
            # fake here once let a dict through that crashed the live run
            raise TypeError(f"save_score expects ScoreResult, got {type(score).__name__}")
        self.scores[posting_id] = score
        self.postings[posting_id]["status"] = "scored"

    async def save_draft(self, posting_id: int, cv_variant: str, note_text: str) -> None:
        self.drafts.append(
            {
                "posting_id": posting_id,
                "cv_variant": cv_variant,
                "note_text": note_text,
                "generated_at": datetime.now(UTC),
            }
        )

    async def latest_draft(self, posting_id: int) -> dict[str, Any] | None:
        matching = [d for d in self.drafts if d["posting_id"] == posting_id]
        return matching[-1] if matching else None

    # --- audit / state / budget --------------------------------------------
    async def record_scan(
        self,
        source: str,
        external_id: str,
        verdict: str,
        detail: str = "",
        url: str = "",
        company: str = "",
        title: str = "",
    ) -> None:
        key = (source, external_id)
        if key not in self.scan_log:
            self.scan_log[key] = {
                "verdict": verdict,
                "detail": detail,
                "url": url,
                "company": company,
                "title": title,
            }

    async def update_scan_verdict(
        self, source: str, external_id: str, verdict: str, detail: str = ""
    ) -> None:
        entry = self.scan_log.setdefault((source, external_id), {})
        entry["verdict"] = verdict
        entry["detail"] = detail

    async def add_event(
        self,
        event_type: str,
        posting_id: int | None = None,
        from_status: str | None = None,
        to_status: str | None = None,
        note: str | None = None,
    ) -> None:
        self.events.append(
            {
                "event_type": event_type,
                "posting_id": posting_id,
                "from_status": from_status,
                "to_status": to_status,
                "note": note,
                "created_at": datetime.now(UTC),
            }
        )

    async def get_state(self, key: str) -> str | None:
        return self.state.get(key)

    async def set_state(self, key: str, value: str) -> None:
        self.state[key] = value

    async def bump_counter(self, key: str, delta: int = 1) -> int:
        value = int(self.state.get(key, "0")) + delta
        self.state[key] = str(value)
        return value

    async def log_usage(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cost_usd: float,
        purpose: str,
    ) -> None:
        self.usage.append(
            {
                "model": model,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cost_usd": cost_usd,
                "purpose": purpose,
                "created_at": datetime.now(UTC),
            }
        )

    async def usage_since(self, since: datetime) -> tuple[int, float]:
        rows = [u for u in self.usage if u["created_at"] >= since]
        return len(rows), sum(u["cost_usd"] for u in rows)
