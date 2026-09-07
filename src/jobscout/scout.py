"""Scout orchestration: fetch -> dedupe -> prefilter -> graph per survivor.

Every fetched posting ends with exactly one audited verdict in scan_log.
"""

from __future__ import annotations

import structlog

from jobscout.dedupe import ingest
from jobscout.embeddings import Embedder
from jobscout.models import PipelineState, RawPosting
from jobscout.prefilter import Prefilter
from jobscout.store import Store

log = structlog.get_logger()


class Scout:
    def __init__(
        self,
        store: Store,
        embedder: Embedder,
        prefilter: Prefilter,
        graph,
        *,
        profile: str,
        notes_examples: str,
        lane_cv_map: dict[str, str],
        max_per_run: int,
    ):
        self.store = store
        self.embedder = embedder
        self.prefilter = prefilter
        self.graph = graph
        self.profile = profile
        self.notes_examples = notes_examples
        self.lane_cv_map = lane_cv_map
        self.max_per_run = max_per_run

    async def run(self, sources: list) -> dict[str, int]:
        fetched: list[RawPosting] = []
        degraded: list[str] = []
        for source in sources:
            fetched.extend(await source.fetch())
            degraded.extend(getattr(source, "degraded", []))

        fresh, dup_rows = await ingest(fetched, self.store, self.embedder)
        for row in dup_rows:
            await self.store.record_scan(**row)

        id_by_external = {posting.external_id: pid for pid, posting in fresh}
        survivors, scan_rows = self.prefilter.filter([posting for _, posting in fresh])
        for row in scan_rows:
            await self.store.record_scan(**row)

        capped = survivors[: self.max_per_run]
        for posting in survivors[self.max_per_run :]:
            await self.store.update_scan_verdict(
                posting.source, posting.external_id, "over_run_cap"
            )

        outcomes: dict[str, int] = {}
        processed_ids: set[int] = set()
        for posting in capped:
            pid = id_by_external[posting.external_id]
            processed_ids.add(pid)
            outcome = await self._process(pid, self._render_posting(posting))
            outcomes[outcome] = outcomes.get(outcome, 0) + 1
            await self.store.update_scan_verdict(posting.source, posting.external_id, outcome)

        # survivors a previous run capped out are still status 'new' with no
        # score - refetching them tomorrow hits the exact-dup key, so this
        # backlog drain is their only path to scoring
        backlog_done = 0
        room = self.max_per_run - len(capped)
        if room > 0:
            for row in await self.store.unscored_new_postings(room + len(processed_ids)):
                if row["posting_id"] in processed_ids or backlog_done >= room:
                    continue
                outcome = await self._process(row["posting_id"], self._render_row(row))
                outcomes[outcome] = outcomes.get(outcome, 0) + 1
                await self.store.update_scan_verdict(row["source"], row["external_id"], outcome)
                backlog_done += 1

        counts = {
            "fetched": len(fetched),
            "duplicates": len(dup_rows),
            "survivors": len(survivors),
            "processed": len(capped),
            "backlog_processed": backlog_done,
            "degraded_sources": len(degraded),
            **outcomes,
        }
        log.info("scout_run_done", **counts)
        return counts

    async def _process(self, posting_id: int, posting_text: str) -> str:
        state: PipelineState = {
            "posting_id": posting_id,
            "posting_text": posting_text,
            "profile": self.profile,
            "notes_examples": self.notes_examples,
            "lane_cv_map": self.lane_cv_map,
            "draft_attempts": 0,
        }
        result = await self.graph.ainvoke(state)
        return result.get("outcome", "unknown")

    @staticmethod
    def _render_text(
        *,
        title: str,
        company: str,
        location: str | None,
        remote: bool | None,
        salary_raw: str | None,
        description: str,
    ) -> str:
        return (
            f"Title: {title}\n"
            f"Company: {company}\n"
            f"Location: {location or 'n/a'}"
            f"{' (remote)' if remote else ''}\n"
            f"Salary: {salary_raw or 'not posted'}\n\n"
            f"{description}"
        )

    @staticmethod
    def _render_posting(posting: RawPosting) -> str:
        return Scout._render_text(
            title=posting.title,
            company=posting.company,
            location=posting.location,
            remote=posting.remote,
            salary_raw=posting.salary_raw,
            description=posting.description,
        )

    @staticmethod
    def _render_row(row: dict) -> str:
        return Scout._render_text(
            title=row["title"],
            company=row["company"],
            location=row["location"],
            remote=row["remote"],
            salary_raw=row["salary_raw"],
            description=row["description"],
        )
