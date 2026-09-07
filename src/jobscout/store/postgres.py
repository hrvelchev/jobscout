"""Postgres + pgvector implementation of the Store protocol (asyncpg pool)."""

from __future__ import annotations

from datetime import datetime
from importlib import resources
from typing import Any

import asyncpg
import structlog
from pgvector.asyncpg import register_vector

from jobscout.models import RawPosting, ScoreResult

log = structlog.get_logger()


async def _init_connection(conn: asyncpg.Connection) -> None:
    await register_vector(conn)


class PostgresStore:
    def __init__(self, dsn: str):
        self.dsn = dsn
        self.pool: asyncpg.Pool | None = None

    async def connect(self) -> None:
        # bootstrap with a PLAIN connection: the vector type must exist before
        # any pooled connection tries to register its codec
        boot = await asyncpg.connect(self.dsn)
        try:
            schema = resources.files("jobscout.store").joinpath("schema.sql").read_text("utf-8")
            await boot.execute(schema)
            await boot.execute(
                "CREATE INDEX IF NOT EXISTS idx_postings_embedding "
                "ON postings USING hnsw (embedding vector_cosine_ops)"
            )
        finally:
            await boot.close()
        self.pool = await asyncpg.create_pool(
            self.dsn, min_size=1, max_size=5, init=_init_connection
        )
        log.info("store_connected")

    async def close(self) -> None:
        if self.pool:
            await self.pool.close()

    # --- postings -----------------------------------------------------------
    async def insert_posting(
        self, posting: RawPosting, embedding: list[float] | None
    ) -> int | None:
        row = await self.pool.fetchrow(
            """
            INSERT INTO postings (source, external_id, url, company, company_norm,
                title, description, location, remote, salary_raw, salary_min,
                salary_max, salary_currency, posted_at, embedding)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15)
            ON CONFLICT (source, external_id) DO NOTHING
            RETURNING posting_id
            """,
            posting.source,
            posting.external_id,
            posting.url,
            posting.company,
            posting.company_norm,
            posting.title,
            posting.description,
            posting.location,
            posting.remote,
            posting.salary_raw,
            posting.salary_min,
            posting.salary_max,
            posting.salary_currency,
            posting.posted_at,
            embedding,
        )
        return row["posting_id"] if row else None

    async def find_semantic_dup(
        self, embedding: list[float], company_norm: str, source: str, threshold: float = 0.90
    ) -> int | None:
        row = await self.pool.fetchrow(
            """
            SELECT posting_id, 1 - (embedding <=> $1) AS sim
            FROM postings
            WHERE embedding IS NOT NULL
              AND duplicate_of IS NULL
              AND source <> $4
              AND (company_norm = $2
                   OR position(company_norm IN $2) > 0
                   OR position($2 IN company_norm) > 0)
              AND 1 - (embedding <=> $1) >= $3
            ORDER BY embedding <=> $1
            LIMIT 1
            """,
            embedding,
            company_norm,
            threshold,
            source,
        )
        return row["posting_id"] if row else None

    async def mark_duplicate(self, posting_id: int, duplicate_of: int) -> None:
        await self.pool.execute(
            "UPDATE postings SET duplicate_of = $2 WHERE posting_id = $1",
            posting_id,
            duplicate_of,
        )

    async def get_posting(self, posting_id: int) -> dict[str, Any] | None:
        row = await self.pool.fetchrow("SELECT * FROM postings WHERE posting_id = $1", posting_id)
        return dict(row) if row else None

    async def set_status(self, posting_id: int, status: str, note: str | None = None) -> None:
        async with self.pool.acquire() as conn, conn.transaction():
            old = await conn.fetchval(
                "SELECT status FROM postings WHERE posting_id = $1", posting_id
            )
            await conn.execute(
                "UPDATE postings SET status = $2 WHERE posting_id = $1", posting_id, status
            )
            await conn.execute(
                "INSERT INTO events (posting_id, event_type, from_status, to_status, note) "
                "VALUES ($1, 'status_change', $2, $3, $4)",
                posting_id,
                old,
                status,
                note,
            )

    async def set_snooze(self, posting_id: int, until: datetime) -> None:
        await self.pool.execute(
            "UPDATE postings SET snooze_until = $2 WHERE posting_id = $1", posting_id, until
        )
        await self.set_status(posting_id, "snoozed")

    async def postings_with_status(self, *statuses: str) -> list[dict[str, Any]]:
        rows = await self.pool.fetch(
            "SELECT * FROM postings WHERE status = ANY($1::text[])", list(statuses)
        )
        return [dict(r) for r in rows]

    async def applications(self) -> list[dict[str, Any]]:
        rows = await self.pool.fetch(
            """
            SELECT p.posting_id, p.company, p.title, p.url, p.status, p.salary_raw,
                   s.fit_score, s.lane,
                   (SELECT max(e.created_at) FROM events e
                     WHERE e.posting_id = p.posting_id AND e.to_status = 'applied') AS applied_at,
                   (SELECT d.cv_variant FROM drafts d WHERE d.posting_id = p.posting_id
                     ORDER BY d.generated_at DESC LIMIT 1) AS cv_variant
            FROM postings p
            LEFT JOIN scores s USING (posting_id)
            WHERE p.status IN ('applied', 'closed')
            ORDER BY applied_at NULLS LAST, p.posting_id
            """
        )
        return [dict(r) for r in rows]

    async def unscored_new_postings(self, limit: int) -> list[dict[str, Any]]:
        rows = await self.pool.fetch(
            """
            SELECT p.* FROM postings p
            JOIN scan_log sl ON sl.source = p.source AND sl.external_id = p.external_id
            WHERE sl.verdict IN ('over_run_cap', 'over_daily_cap', 'passed')
              AND p.status = 'new'
              AND p.duplicate_of IS NULL
              AND NOT EXISTS (SELECT 1 FROM scores s WHERE s.posting_id = p.posting_id)
            ORDER BY p.posting_id
            LIMIT $1
            """,
            limit,
        )
        return [dict(r) for r in rows]

    async def eligible_for_digest(self, now: datetime) -> list[dict[str, Any]]:
        rows = await self.pool.fetch(
            """
            SELECT p.*, s.fit_score, s.lane, s.stack_match, s.seniority_gap,
                   s.degree_gate, s.red_flags, s.cv_keywords, s.reason
            FROM postings p
            JOIN scores s ON s.posting_id = p.posting_id
            WHERE p.duplicate_of IS NULL
              AND (p.status = 'scored'
                   OR (p.status = 'snoozed' AND (p.snooze_until IS NULL OR p.snooze_until <= $1)))
            """,
            now,
        )
        out = []
        for r in rows:
            import json

            row = dict(r)
            row["score"] = ScoreResult(
                fit_score=r["fit_score"],
                stack_match=r["stack_match"],
                seniority_gap=r["seniority_gap"],
                degree_gate=r["degree_gate"],
                lane=r["lane"],
                red_flags=json.loads(r["red_flags"]),
                cv_keywords=json.loads(r["cv_keywords"]),
                reason=r["reason"],
            )
            out.append(row)
        return out

    async def applied_same_company_since(self, company_norm: str, days: int) -> bool:
        row = await self.pool.fetchrow(
            """
            SELECT 1
            FROM events e JOIN postings p ON p.posting_id = e.posting_id
            WHERE e.to_status = 'applied'
              AND e.created_at > now() - make_interval(days => $2)
              AND p.company_norm = $1
            LIMIT 1
            """,
            company_norm,
            days,
        )
        return row is not None

    # --- pipeline artifacts -------------------------------------------------
    async def save_score(self, posting_id: int, score: ScoreResult) -> None:
        import json

        async with self.pool.acquire() as conn, conn.transaction():
            await conn.execute(
                """
                INSERT INTO scores (posting_id, fit_score, lane, stack_match,
                    seniority_gap, degree_gate, red_flags, cv_keywords, reason)
                VALUES ($1,$2,$3,$4,$5,$6,$7::jsonb,$8::jsonb,$9)
                ON CONFLICT (posting_id) DO UPDATE SET
                    fit_score = EXCLUDED.fit_score, lane = EXCLUDED.lane,
                    stack_match = EXCLUDED.stack_match,
                    seniority_gap = EXCLUDED.seniority_gap,
                    degree_gate = EXCLUDED.degree_gate,
                    red_flags = EXCLUDED.red_flags,
                    cv_keywords = EXCLUDED.cv_keywords,
                    reason = EXCLUDED.reason, scored_at = now()
                """,
                posting_id,
                score.fit_score,
                score.lane,
                score.stack_match,
                score.seniority_gap,
                score.degree_gate,
                json.dumps(score.red_flags),
                json.dumps(score.cv_keywords),
                score.reason,
            )
            await conn.execute(
                "UPDATE postings SET status = 'scored' WHERE posting_id = $1", posting_id
            )

    async def save_draft(self, posting_id: int, cv_variant: str, note_text: str) -> None:
        await self.pool.execute(
            "INSERT INTO drafts (posting_id, cv_variant, note_text) VALUES ($1,$2,$3)",
            posting_id,
            cv_variant,
            note_text,
        )

    async def latest_draft(self, posting_id: int) -> dict[str, Any] | None:
        row = await self.pool.fetchrow(
            "SELECT * FROM drafts WHERE posting_id = $1 "
            "ORDER BY generated_at DESC, draft_id DESC LIMIT 1",
            posting_id,
        )
        return dict(row) if row else None

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
        await self.pool.execute(
            """
            INSERT INTO scan_log (source, external_id, verdict, detail, url, company, title)
            VALUES ($1,$2,$3,$4,$5,$6,$7)
            ON CONFLICT (source, external_id) DO NOTHING
            """,
            source,
            external_id,
            verdict,
            detail,
            url,
            company,
            title,
        )

    async def update_scan_verdict(
        self, source: str, external_id: str, verdict: str, detail: str = ""
    ) -> None:
        await self.pool.execute(
            """
            INSERT INTO scan_log (source, external_id, verdict, detail)
            VALUES ($1,$2,$3,$4)
            ON CONFLICT (source, external_id) DO UPDATE
                SET verdict = EXCLUDED.verdict, detail = EXCLUDED.detail
            """,
            source,
            external_id,
            verdict,
            detail,
        )

    async def add_event(
        self,
        event_type: str,
        posting_id: int | None = None,
        from_status: str | None = None,
        to_status: str | None = None,
        note: str | None = None,
    ) -> None:
        await self.pool.execute(
            "INSERT INTO events (posting_id, event_type, from_status, to_status, note) "
            "VALUES ($1,$2,$3,$4,$5)",
            posting_id,
            event_type,
            from_status,
            to_status,
            note,
        )

    async def get_state(self, key: str) -> str | None:
        return await self.pool.fetchval("SELECT value FROM app_state WHERE key = $1", key)

    async def set_state(self, key: str, value: str) -> None:
        await self.pool.execute(
            "INSERT INTO app_state (key, value) VALUES ($1,$2) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()",
            key,
            value,
        )

    async def bump_counter(self, key: str, delta: int = 1) -> int:
        # single-statement atomic upsert: race-free under concurrency
        return await self.pool.fetchval(
            """
            INSERT INTO app_state (key, value) VALUES ($1, ($2::int)::text)
            ON CONFLICT (key) DO UPDATE
                SET value = ((app_state.value)::int + $2::int)::text, updated_at = now()
            RETURNING (value)::int
            """,
            key,
            delta,
        )

    async def log_usage(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cost_usd: float,
        purpose: str,
    ) -> None:
        await self.pool.execute(
            "INSERT INTO usage_log (model, input_tokens, output_tokens, cost_usd, purpose) "
            "VALUES ($1,$2,$3,$4,$5)",
            model,
            input_tokens,
            output_tokens,
            cost_usd,
            purpose,
        )

    async def usage_since(self, since: datetime) -> tuple[int, float]:
        row = await self.pool.fetchrow(
            "SELECT count(*) AS n, coalesce(sum(cost_usd), 0) AS total "
            "FROM usage_log WHERE created_at >= $1",
            since,
        )
        return int(row["n"]), float(row["total"])
