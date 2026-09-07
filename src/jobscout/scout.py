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

        # one company posting the same title in N cities is one JD: the paid
        # score runs once per (company, title) group and copies to the rest
        groups: dict[tuple[str, str], list[RawPosting]] = {}
        group_order: list[tuple[str, str]] = []
        for posting in survivors:
            key = (posting.company_norm, posting.title.strip().lower())
            if key not in groups:
                groups[key] = []
                group_order.append(key)
            groups[key].append(posting)

        capped_keys = group_order[: self.max_per_run]
        for key in group_order[self.max_per_run :]:
            for posting in groups[key]:
                await self.store.update_scan_verdict(
                    posting.source, posting.external_id, "over_run_cap"
                )

        outcomes: dict[str, int] = {}
        processed_ids: set[int] = set()
        for key in capped_keys:
            rep, *variants = groups[key]
            pid = id_by_external[rep.external_id]
            processed_ids.add(pid)
            result = await self._process(pid, self._render_posting(rep))
            outcome = result.get("outcome", "unknown")
            outcomes[outcome] = outcomes.get(outcome, 0) + 1
            await self.store.update_scan_verdict(rep.source, rep.external_id, outcome)
            await self._copy_to_variants(
                result,
                [(id_by_external[v.external_id], v.source, v.external_id) for v in variants],
                outcomes,
                processed_ids,
            )

        # survivors a previous run capped out are still status 'new' with no
        # score - refetching them tomorrow hits the exact-dup key, so this
        # backlog drain is their only path to scoring
        backlog_done = 0
        room = self.max_per_run - len(capped_keys)
        if room > 0:
            rows = await self.store.unscored_new_postings(room * 4 + len(processed_ids))
            brow_groups: dict[tuple[str, str], list[dict]] = {}
            brow_order: list[tuple[str, str]] = []
            for row in rows:
                if row["posting_id"] in processed_ids:
                    continue
                key = (row["company_norm"], row["title"].strip().lower())
                if key not in brow_groups:
                    brow_groups[key] = []
                    brow_order.append(key)
                brow_groups[key].append(row)
            for key in brow_order:
                if backlog_done >= room:
                    break
                rep_row, *variant_rows = brow_groups[key]
                result = await self._process(rep_row["posting_id"], self._render_row(rep_row))
                outcome = result.get("outcome", "unknown")
                outcomes[outcome] = outcomes.get(outcome, 0) + 1
                await self.store.update_scan_verdict(
                    rep_row["source"], rep_row["external_id"], outcome
                )
                backlog_done += 1
                await self._copy_to_variants(
                    result,
                    [(r["posting_id"], r["source"], r["external_id"]) for r in variant_rows],
                    outcomes,
                    processed_ids,
                )

        counts = {
            "fetched": len(fetched),
            "duplicates": len(dup_rows),
            "survivors": len(survivors),
            "processed": len(capped_keys),
            "backlog_processed": backlog_done,
            "degraded_sources": len(degraded),
            **outcomes,
        }
        log.info("scout_run_done", **counts)
        return counts

    async def _process(self, posting_id: int, posting_text: str) -> dict:
        state: PipelineState = {
            "posting_id": posting_id,
            "posting_text": posting_text,
            "profile": self.profile,
            "notes_examples": self.notes_examples,
            "lane_cv_map": self.lane_cv_map,
            "draft_attempts": 0,
        }
        return await self.graph.ainvoke(state)

    async def _copy_to_variants(
        self,
        result: dict,
        variant_refs: list[tuple[int, str, str]],
        outcomes: dict[str, int],
        processed_ids: set[int],
    ) -> None:
        """Same-JD variants inherit the representative's score; if the rep
        never got one (budget ran out, parse failed) they fall back to the
        backlog via over_run_cap."""
        score = result.get("score")
        for vid, source, external_id in variant_refs:
            processed_ids.add(vid)
            if score is not None:
                await self.store.save_score(vid, score)
                await self.store.update_scan_verdict(source, external_id, "scored_variant")
                outcomes["scored_variant"] = outcomes.get("scored_variant", 0) + 1
            else:
                await self.store.update_scan_verdict(source, external_id, "over_run_cap")

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
