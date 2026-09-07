"""dev.bg source adapter.

Verified page structure (fetched live 2026-09-07):

Listing page https://dev.bg/company/jobs/{category}/ - one card per job:
    div.job-list-item[data-job-id="554746"]      -> external_id
      a.overlay-link[href]                        -> posting url
      span.company-name                           -> company
      h6.job-title                                -> title
      span.date                                   -> "4 сеп." (BG month abbrev)
      div.tags-wrap span.badge                    -> location text (pin icon),
        "badge green bold remote" = fully remote, "badge hybrid ..." = hybrid;
        a salary badge, when present, contains "лв." / "EUR" amounts

Detail page: the description is NOT in the page body - it is loaded in an
iframe whose src is `{posting_url}?pj={id}`; fetching that URL directly
returns clean description HTML. The detail page also carries
<time datetime="YYYY-MM-DD"> with the machine-readable posting date.

Details are fetched ONLY for external_ids the store has never seen, with a
polite delay between requests - the daily new count is small.

A non-empty listing page that parses to zero cards triggers a degradation
alert flag: dev.bg changing its markup must never look like a quiet day.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

import structlog
from selectolax.parser import HTMLParser

from jobscout.models import RawPosting
from jobscout.sources.base import polite_pause
from jobscout.store import Store

log = structlog.get_logger()

LISTING_URL = "https://dev.bg/company/jobs/{category}/"

_SALARY_RE = re.compile(
    r"(\d[\d\s.,]*)\s*-\s*(\d[\d\s.,]*)\s*(лв|BGN|EUR|евро)", re.IGNORECASE
)
_TIME_RE = re.compile(r'<time datetime="(\d{4}-\d{2}-\d{2})"')


def _clean_int(raw: str) -> int | None:
    digits = re.sub(r"[^\d]", "", raw)
    return int(digits) if digits else None


def parse_salary(text: str) -> tuple[int | None, int | None, str | None]:
    match = _SALARY_RE.search(text)
    if not match:
        return None, None, None
    currency = match.group(3).lower()
    return (
        _clean_int(match.group(1)),
        _clean_int(match.group(2)),
        "BGN" if currency in ("лв", "bgn") else "EUR",
    )


def parse_listing(html: str) -> list[dict]:
    """Card dicts from a listing page. Raises nothing; returns [] on alien HTML."""
    tree = HTMLParser(html)
    cards = []
    for node in tree.css("div.job-list-item"):
        job_id = (node.attributes.get("data-job-id") or "").strip()
        link = node.css_first("a.overlay-link")
        title = node.css_first("h6.job-title")
        company = node.css_first("span.company-name")
        if not (job_id and link and title):
            continue
        badges = [b.text(strip=True) for b in node.css("div.tags-wrap span.badge")]
        badge_classes = " ".join(
            b.attributes.get("class", "") for b in node.css("div.tags-wrap span.badge")
        )
        salary_min, salary_max, currency = parse_salary(" ".join(badges))
        cards.append(
            {
                "external_id": job_id,
                "url": link.attributes.get("href") or "",
                "title": title.text(strip=True),
                "company": company.text(strip=True) if company else "",
                "location": badges[0] if badges else None,
                "remote": ("remote" in badge_classes) or None,
                "salary_min": salary_min,
                "salary_max": salary_max,
                "salary_currency": currency,
                "salary_raw": next((b for b in badges if _SALARY_RE.search(b)), None),
            }
        )
    return cards


def parse_detail(html: str) -> tuple[str, datetime | None]:
    """(description_text, posted_at) from the iframe/detail HTML."""
    posted_at = None
    time_match = _TIME_RE.search(html)
    if time_match:
        posted_at = datetime.strptime(time_match.group(1), "%Y-%m-%d").replace(tzinfo=UTC)
    tree = HTMLParser(html)
    for selector in ("div.single_job_listing", "body"):
        node = tree.css_first(selector)
        if node is not None:
            text = re.sub(r"\n{3,}", "\n\n", node.text(separator="\n", strip=True))
            return text[:8_000], posted_at
    return "", posted_at


class DevBgSource:
    def __init__(self, store: Store, http, categories: list[str]):
        self.store = store
        self.http = http
        self.categories = categories
        self.degraded: list[str] = []  # categories that parsed to zero cards

    async def fetch(self) -> list[RawPosting]:
        postings: list[RawPosting] = []
        self.degraded = []
        for category in self.categories:
            url = LISTING_URL.format(category=category)
            try:
                response = await self.http.get(url)
                response.raise_for_status()
            except Exception as exc:  # noqa: BLE001
                log.warning("devbg_listing_failed", category=category, error=str(exc))
                self.degraded.append(category)
                continue
            cards = parse_listing(response.text)
            if not cards and len(response.text) > 10_000:
                log.warning("devbg_zero_cards", category=category, bytes=len(response.text))
                self.degraded.append(category)
                continue
            new_cards = [
                c for c in cards
                if not await self.store.get_state(f"devbg_seen:{c['external_id']}")
            ]
            log.info("devbg_listing", category=category, cards=len(cards), new=len(new_cards))
            for card in new_cards:
                await polite_pause()
                description, posted_at = await self._fetch_detail(card)
                postings.append(
                    RawPosting(
                        source="devbg",
                        external_id=card["external_id"],
                        url=card["url"],
                        company=card["company"],
                        title=card["title"],
                        description=description,
                        location=card["location"],
                        remote=card["remote"],
                        salary_raw=card["salary_raw"],
                        salary_min=card["salary_min"],
                        salary_max=card["salary_max"],
                        salary_currency=card["salary_currency"],
                        posted_at=posted_at,
                    )
                )
                await self.store.set_state(f"devbg_seen:{card['external_id']}", "1")
            await polite_pause()
        return postings

    async def _fetch_detail(self, card: dict) -> tuple[str, datetime | None]:
        # the iframe URL returns clean description HTML (see module docstring)
        detail_url = f"{card['url']}?pj={card['external_id']}"
        try:
            response = await self.http.get(detail_url)
            response.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            log.warning("devbg_detail_failed", url=detail_url, error=str(exc))
            return "", None
        return parse_detail(response.text)
