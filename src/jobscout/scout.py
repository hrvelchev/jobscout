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
        for posting in capped:
            state: PipelineState = {
                "posting_id": id_by_external[posting.external_id],
                "posting_text": self._render_posting(posting),
                "profile": self.profile,
                "notes_examples": self.notes_examples,
                "lane_cv_map": self.lane_cv_map,
                "draft_attempts": 0,
            }
            result = await self.graph.ainvoke(state)
            outcome = result.get("outcome", "unknown")
            outcomes[outcome] = outcomes.get(outcome, 0) + 1
            await self.store.update_scan_verdict(posting.source, posting.external_id, outcome)

        counts = {
            "fetched": len(fetched),
            "duplicates": len(dup_rows),
            "survivors": len(survivors),
            "processed": len(capped),
            "degraded_sources": len(degraded),
            **outcomes,
        }
        log.info("scout_run_done", **counts)
        return counts

    @staticmethod
    def _render_posting(posting: RawPosting) -> str:
        salary = posting.salary_raw or "not posted"
        return (
            f"Title: {posting.title}\n"
            f"Company: {posting.company}\n"
            f"Location: {posting.location or 'n/a'}"
            f"{' (remote)' if posting.remote else ''}\n"
            f"Salary: {salary}\n\n"
            f"{posting.description}"
        )
