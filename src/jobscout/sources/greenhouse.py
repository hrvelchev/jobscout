"""Greenhouse job-board source: the public JSON API, no auth, no scraping.

GET https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true
-> {"jobs": [{"id", "absolute_url", "title", "updated_at",
              "location": {"name"}, "content": "<html-escaped body>"}]}

One request per watched board per run. The `content` field is HTML-escaped
HTML; it is unescaped and stripped to text for scoring.
"""

from __future__ import annotations

import html as html_mod
import re
from datetime import datetime

import structlog
from selectolax.parser import HTMLParser

from jobscout.models import RawPosting
from jobscout.sources.base import polite_pause
from jobscout.store import Store

log = structlog.get_logger()

BOARD_URL = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true"


def strip_html(content: str) -> str:
    unescaped = html_mod.unescape(content or "")
    text = HTMLParser(unescaped).text(separator="\n", strip=True)
    return re.sub(r"\n{3,}", "\n\n", text)[:8_000]


def parse_board(payload: dict, token: str) -> list[RawPosting]:
    postings = []
    for job in payload.get("jobs", []):
        job_id = str(job.get("id", "")).strip()
        title = (job.get("title") or "").strip()
        if not job_id or not title:
            continue
        posted_at = None
        raw_ts = job.get("updated_at") or job.get("first_published")
        if raw_ts:
            try:
                posted_at = datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
            except ValueError:
                pass
        postings.append(
            RawPosting(
                source="greenhouse",
                external_id=f"{token}:{job_id}",
                url=job.get("absolute_url") or "",
                company=token,
                title=title,
                description=strip_html(job.get("content", "")),
                location=(job.get("location") or {}).get("name"),
                posted_at=posted_at,
            )
        )
    return postings


class GreenhouseSource:
    def __init__(self, store: Store, http, board_tokens: list[str]):
        self.store = store
        self.http = http
        self.board_tokens = board_tokens

    async def fetch(self) -> list[RawPosting]:
        postings: list[RawPosting] = []
        for index, token in enumerate(self.board_tokens):
            if index:
                await polite_pause()
            try:
                response = await self.http.get(BOARD_URL.format(token=token))
                response.raise_for_status()
                payload = response.json()
            except Exception as exc:  # noqa: BLE001
                log.warning("greenhouse_board_failed", token=token, error=str(exc))
                continue
            board_postings = parse_board(payload, token)
            log.info("greenhouse_board", token=token, jobs=len(board_postings))
            postings.extend(board_postings)
        return postings
