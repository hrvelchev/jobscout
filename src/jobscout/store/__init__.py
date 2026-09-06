"""Store protocol: the single seam between the app and Postgres.

Unit tests run against tests/fakes.FakeStore (pure Python); integration tests
(marker `pg`) run the same contract against real Postgres + pgvector.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol

from jobscout.models import RawPosting, ScoreResult


class Store(Protocol):
    # --- postings -----------------------------------------------------------
    async def insert_posting(
        self, posting: RawPosting, embedding: list[float] | None
    ) -> int | None:
        """Insert; return posting_id, or None if (source, external_id) exists."""
        ...

    async def find_semantic_dup(
        self, embedding: list[float], company_norm: str, threshold: float = 0.90
    ) -> int | None:
        """posting_id of a non-duplicate posting with cosine >= threshold AND a
        matching company_norm (equal or substring either way), else None."""
        ...

    async def mark_duplicate(self, posting_id: int, duplicate_of: int) -> None: ...

    async def get_posting(self, posting_id: int) -> dict[str, Any] | None: ...

    async def set_status(self, posting_id: int, status: str, note: str | None = None) -> None:
        """Update status + append an event row (append-only audit)."""
        ...

    async def set_snooze(self, posting_id: int, until: datetime) -> None: ...

    async def postings_with_status(self, *statuses: str) -> list[dict[str, Any]]: ...

    async def eligible_for_digest(self, now: datetime) -> list[dict[str, Any]]:
        """Scored, non-duplicate, not yet digested/applied/skipped/closed and
        not snoozed (or snooze lapsed) - joined with their score row."""
        ...

    async def applied_same_company_since(self, company_norm: str, days: int) -> bool: ...

    # --- pipeline artifacts -------------------------------------------------
    async def save_score(self, posting_id: int, score: ScoreResult) -> None: ...

    async def save_draft(self, posting_id: int, cv_variant: str, note_text: str) -> None: ...

    async def latest_draft(self, posting_id: int) -> dict[str, Any] | None: ...

    # --- audit / state / budget --------------------------------------------
    async def record_scan(
        self, source: str, external_id: str, verdict: str, detail: str = "",
        url: str = "", company: str = "", title: str = "",
    ) -> None:
        """Upsert-ignore: the first verdict for (source, external_id) wins the
        insert; use update_scan_verdict to overwrite deliberately."""
        ...

    async def update_scan_verdict(
        self, source: str, external_id: str, verdict: str, detail: str = ""
    ) -> None: ...

    async def add_event(
        self, event_type: str, posting_id: int | None = None,
        from_status: str | None = None, to_status: str | None = None,
        note: str | None = None,
    ) -> None: ...

    async def get_state(self, key: str) -> str | None: ...

    async def set_state(self, key: str, value: str) -> None: ...

    async def bump_counter(self, key: str, delta: int = 1) -> int:
        """Atomically add delta; return the new value. The budget primitive -
        must be race-free under concurrent calls."""
        ...

    async def log_usage(
        self, model: str, input_tokens: int, output_tokens: int,
        cost_usd: float, purpose: str,
    ) -> None: ...

    async def usage_since(self, since: datetime) -> tuple[int, float]:
        """(call_count, total_cost_usd) since the given moment."""
        ...
