"""Scheduled jobs. Every job wrapper catches, logs AND tells the owner -
a crashed cron that only logs is a silent malfunction."""

from __future__ import annotations

import contextlib

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

log = structlog.get_logger()


def start_scheduler(settings, *, run_scout, run_digest, run_watch, notify) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=settings.timezone)

    def wrap(name, fn):
        async def job():
            try:
                await fn()
            except Exception as exc:  # noqa: BLE001
                log.exception("job_failed", job=name)
                with contextlib.suppress(Exception):
                    await notify(f"jobscout job '{name}' failed: {exc}")

        return job

    scheduler.add_job(
        wrap("scout", run_scout),
        CronTrigger(hour=settings.scout_hour, minute=settings.scout_minute),
        max_instances=1,
        coalesce=True,
        id="scout",
    )
    scheduler.add_job(
        wrap("digest", run_digest),
        CronTrigger(hour=settings.digest_hour, minute=settings.digest_minute),
        max_instances=1,
        coalesce=True,
        id="digest",
    )
    scheduler.add_job(
        wrap("watch", run_watch),
        CronTrigger(day="*/2", hour=9, minute=15),
        max_instances=1,
        coalesce=True,
        id="watch",
    )
    scheduler.start()
    log.info("scheduler_started", timezone=settings.timezone)
    return scheduler
