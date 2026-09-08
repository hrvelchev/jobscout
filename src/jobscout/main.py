"""Wiring: one process runs the bot, the scheduler and the pipeline."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

import structlog

from jobscout.budget import Budget
from jobscout.config import (
    load_filters,
    load_lanes,
    load_notes_examples,
    load_profile,
    load_settings,
    load_sources,
)
from jobscout.digest import send_digest
from jobscout.embeddings import FastEmbedEmbedder
from jobscout.pipeline.graph import build_graph
from jobscout.prefilter import Prefilter
from jobscout.ranking import rank
from jobscout.scheduler import start_scheduler
from jobscout.scout import Scout
from jobscout.sheet import GspreadSheet, sync_applications
from jobscout.sources.base import make_http_client
from jobscout.sources.devbg import DevBgSource
from jobscout.sources.greenhouse import GreenhouseSource
from jobscout.store.postgres import PostgresStore
from jobscout.telegram_bot import build_application
from jobscout.watch import check_applied_postings

log = structlog.get_logger()


async def _retry_startup(step: str, action, *, attempts: int = 6) -> None:
    """A freshly booted PC races Docker and the network: transient failures
    on the first connects must retry, not kill the process."""
    for attempt in range(1, attempts + 1):
        try:
            await action()
            return
        except Exception as exc:  # noqa: BLE001 - boot races are heterogeneous
            if attempt == attempts:
                raise
            log.warning("startup_retry", step=step, attempt=attempt, error=str(exc))
            await asyncio.sleep(10 * attempt)


async def run() -> None:
    settings = load_settings()
    logging.basicConfig(level=settings.log_level)
    # keep tokens out of logs no matter the level
    for noisy in ("httpx", "telegram", "anthropic", "apscheduler"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    store = PostgresStore(settings.dsn)
    await _retry_startup("postgres", store.connect)

    from anthropic import AsyncAnthropic

    client = AsyncAnthropic(api_key=settings.anthropic_api_key, max_retries=5)
    http = make_http_client()
    embedder = FastEmbedEmbedder()

    exclude_patterns, required_keywords = load_filters(settings.config_dir)
    lane_weights, lane_cv_map, dream_companies = load_lanes(settings.config_dir)
    devbg_categories, greenhouse_boards = load_sources(settings.config_dir)
    profile = load_profile(settings.config_dir)
    notes_examples = load_notes_examples(settings.config_dir)

    budget = Budget(store, max_per_day=settings.max_llm_calls_per_day)
    graph = build_graph(client, store, budget, draft_threshold=settings.draft_threshold)
    prefilter = Prefilter(exclude_patterns, required_keywords)
    scout = Scout(
        store,
        embedder,
        prefilter,
        graph,
        profile=profile,
        notes_examples=notes_examples,
        lane_cv_map=lane_cv_map,
        max_per_run=settings.max_scored_per_run,
    )
    sources = [
        DevBgSource(store, http, devbg_categories, pages=settings.devbg_pages),
        GreenhouseSource(store, http, greenhouse_boards),
    ]

    scout_lock = asyncio.Lock()

    async def run_scout() -> dict:
        if scout_lock.locked():
            log.info("scout_already_running")
            return {"skipped": 1}
        async with scout_lock:
            return await scout.run(sources)

    sheet = None
    if settings.gsheet_id and settings.google_service_account_file.exists():
        sheet = GspreadSheet(
            settings.google_service_account_file, settings.gsheet_id, settings.gsheet_tab
        )
        log.info("sheet_tracker_enabled", tab=settings.gsheet_tab)
    elif settings.gsheet_id:
        log.warning("sheet_tracker_missing_key", file=str(settings.google_service_account_file))

    async def sync_sheet() -> dict[str, int] | None:
        if sheet is None:
            return None
        try:
            return await sync_applications(store, sheet, lane_cv_map)
        except Exception as exc:  # noqa: BLE001 - the tracker must never break the bot
            log.warning("sheet_sync_failed", error=str(exc))
            return None

    app = build_application(
        settings.telegram_bot_token,
        settings.telegram_owner_id,
        store,
        run_scout=run_scout,
        run_digest=lambda: run_digest(),
        sync_sheet=sync_sheet,
        sheet_url=(
            f"https://docs.google.com/spreadsheets/d/{settings.gsheet_id}"
            if settings.gsheet_id
            else ""
        ),
    )

    async def notify(text: str) -> None:
        await app.bot.send_message(chat_id=settings.telegram_owner_id, text=text)

    async def run_digest() -> int:
        now = datetime.now(UTC)
        rows = await store.eligible_for_digest(now)
        ranked = rank(
            rows,
            lane_weights=lane_weights,
            dream_companies=dream_companies,
            now=now,
            top_n=settings.digest_top_n,
        )
        extra = []
        if now.weekday() == 0:  # Monday
            extra.append("weekly manual check: jobs.bg (not scraped - blocks bots)")
        sent = await send_digest(
            app.bot,
            settings.telegram_owner_id,
            store,
            ranked,
            dream_companies=dream_companies,
            extra_lines=extra,
        )
        await sync_sheet()
        return sent

    async def run_watch() -> None:
        await check_applied_postings(store, http, notify)
        await sync_sheet()  # closed detections land in the tracker promptly

    start_scheduler(
        settings,
        run_scout=run_scout,
        run_digest=run_digest,
        run_watch=run_watch,
        notify=notify,
    )

    log.info("jobscout_started")
    await _retry_startup("telegram", app.initialize)
    try:
        await app.start()
        await app.updater.start_polling()
        await asyncio.Event().wait()
    finally:
        await app.updater.stop()
        await app.stop()
        await app.shutdown()
        await store.close()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
