"""Closed-posting watch: applied postings are re-checked every couple of days.

A posting dying after you applied is information you want the same day, not
at the interview you were hoping for (lesson learned the manual way).
"""

from __future__ import annotations

import structlog

from jobscout.sources.base import polite_pause
from jobscout.sources.greenhouse import BOARD_URL
from jobscout.store import Store

log = structlog.get_logger()


async def check_applied_postings(store: Store, http, notify) -> int:
    """Re-check every applied posting; alert + mark closed for dead ones.
    Returns the number of newly-detected closures."""
    applied = await store.postings_with_status("applied")
    if not applied:
        return 0

    # one board fetch per greenhouse token, not per posting
    greenhouse_ids: dict[str, set[str]] = {}
    for row in applied:
        if row["source"] == "greenhouse":
            token = row["external_id"].split(":", 1)[0]
            greenhouse_ids.setdefault(token, set())
    for token in greenhouse_ids:
        try:
            response = await http.get(BOARD_URL.format(token=token))
            response.raise_for_status()
            payload = response.json()
            greenhouse_ids[token] = {f"{token}:{job.get('id')}" for job in payload.get("jobs", [])}
        except Exception as exc:  # noqa: BLE001
            log.warning("watch_board_failed", token=token, error=str(exc))
            greenhouse_ids[token] = None  # unknown - do not false-alarm
        await polite_pause()

    closed = 0
    for row in applied:
        is_closed = False
        if row["source"] == "greenhouse":
            token = row["external_id"].split(":", 1)[0]
            live_ids = greenhouse_ids.get(token)
            is_closed = live_ids is not None and row["external_id"] not in live_ids
        elif row["source"] == "devbg":
            try:
                response = await http.get(row["url"])
                is_closed = response.status_code in (404, 410)
            except Exception as exc:  # noqa: BLE001
                log.warning("watch_fetch_failed", url=row["url"], error=str(exc))
            await polite_pause()

        if is_closed:
            closed += 1
            await store.set_status(row["posting_id"], "closed", note="watch detected closure")
            await notify(
                f"Posting #{row['posting_id']} appears CLOSED: "
                f"{row['company']} - {row['title']}\n{row['url']}"
            )
    if closed:
        log.info("watch_closures", count=closed)
    return closed
