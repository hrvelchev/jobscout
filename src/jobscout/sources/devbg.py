"""dev.bg source adapter.

Verified page structure (fetched live 2026-09-07):

Listing page https://dev.bg/company/jobs/{category}/ - one card per job:
    div.job-list-item[data-job-id="554746"]      -> external_id
      a.overlay-link[href]                        -> posting url
      span.company-name                           -> company
      h6.job-title                                -> title
      span.date                                   -> "4 сеп." (BG month abbrev)
      div.tags-wrap span.badge                    -> location is the badge's
        OWN text (pin icon); hybrid badges nest a span.suffix-hybrid plus
        hidden tooltip text INSIDE the location badge, so deep text extraction
        glues "СофияHybridКомбиниран..." together. "badge ... remote" class =
        fully remote, "badge hybrid ..." = hybrid; a salary badge, when
        present, contains "лв." / "EUR" amounts.

Detail page (the posting url): carries <time datetime="YYYY-MM-DD"> with the
machine-readable posting date. For custom-designed ads the description is NOT
in the page body - it sits in iframe#custom-job-design, whose src uses a pj
id that DIFFERS from the listing's data-job-id (guessing `?pj={data-job-id}`
returns an empty CSP shell - verified live 2026-09-07). So: fetch the detail
page, read the iframe src from it, fetch that. Non-custom ads keep the
description in div.single_job_listing directly.

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

_SALARY_RE = re.compile(r"(\d[\d\s.,]*)\s*-\s*(\d[\d\s.,]*)\s*(лв|BGN|EUR|евро)", re.IGNORECASE)
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
        badge_nodes = node.css("div.tags-wrap span.badge")
        badge_classes = " ".join(b.attributes.get("class") or "" for b in badge_nodes)
        deep_texts = [" ".join(b.text(separator=" ", strip=True).split()) for b in badge_nodes]
        # location must come from the badge's OWN text nodes: hybrid suffix and
        # tooltip text live in child elements of the same badge (see docstring)
        own_texts = [" ".join((b.text(deep=False) or "").split()) for b in badge_nodes]
        location = next((t for t in own_texts if t and not _SALARY_RE.search(t)), None)
        if location and "hybrid" in badge_classes:
            location += " (hybrid)"
        salary_min, salary_max, currency = parse_salary(" ".join(deep_texts))
        cards.append(
            {
                "external_id": job_id,
                "url": link.attributes.get("href") or "",
                "title": title.text(strip=True),
                "company": company.text(strip=True) if company else "",
                "location": location,
                "remote": ("remote" in badge_classes) or None,
                "salary_min": salary_min,
                "salary_max": salary_max,
                "salary_currency": currency,
                "salary_raw": next((t for t in deep_texts if _SALARY_RE.search(t)), None),
            }
        )
    return cards


def _clean_text(node) -> str:
    for junk in node.css("script, style"):
        junk.decompose()
    return re.sub(r"\n{3,}", "\n\n", node.text(separator="\n", strip=True))[:8_000]


def parse_detail(html: str) -> tuple[str, datetime | None, str | None]:
    """(description_text, posted_at, custom_design_iframe_src) from detail HTML.

    A non-None iframe src means the ad is custom-designed and the real
    description lives at that URL - the returned text is then mostly page
    chrome and the caller should fetch the iframe."""
    posted_at = None
    time_match = _TIME_RE.search(html)
    if time_match:
        posted_at = datetime.strptime(time_match.group(1), "%Y-%m-%d").replace(tzinfo=UTC)
    tree = HTMLParser(html)
    iframe = tree.css_first("iframe#custom-job-design")
    iframe_src = ((iframe.attributes.get("src") or "").strip() or None) if iframe else None
    for selector in ("div.single_job_listing", "body"):
        node = tree.css_first(selector)
        if node is not None:
            return _clean_text(node), posted_at, iframe_src
    return "", posted_at, iframe_src


def parse_iframe_description(html: str) -> str:
    tree = HTMLParser(html)
    node = tree.css_first("body")
    return _clean_text(node) if node is not None else ""


class DevBgSource:
    def __init__(self, store: Store, http, categories: list[str], pages: int = 1):
        self.store = store
        self.http = http
        self.categories = categories
        self.pages = pages  # >1 only for a deliberate backfill; daily = newest 20
        self.degraded: list[str] = []  # categories that parsed to zero cards

    async def fetch(self) -> list[RawPosting]:
        postings: list[RawPosting] = []
        self.degraded = []
        for category in self.categories:
            cards: list[dict] = []
            page_failed = False
            for page in range(1, self.pages + 1):
                base = LISTING_URL.format(category=category)
                url = base if page == 1 else f"{base}page/{page}/"
                if page > 1:
                    await polite_pause()
                try:
                    response = await self.http.get(url)
                    response.raise_for_status()
                except Exception as exc:  # noqa: BLE001
                    if page == 1:
                        log.warning("devbg_listing_failed", category=category, error=str(exc))
                        self.degraded.append(category)
                        page_failed = True
                    else:
                        # a category with fewer pages 404s here - that is fine
                        log.info("devbg_no_more_pages", category=category, page=page)
                    break
                page_cards = parse_listing(response.text)
                if page == 1 and not page_cards and len(response.text) > 10_000:
                    log.warning("devbg_zero_cards", category=category, bytes=len(response.text))
                    self.degraded.append(category)
                    page_failed = True
                    break
                if not page_cards:
                    break
                cards.extend(page_cards)
            if page_failed:
                continue
            new_cards = [
                c for c in cards if not await self.store.get_state(f"devbg_seen:{c['external_id']}")
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
        try:
            response = await self.http.get(card["url"])
            response.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            log.warning("devbg_detail_failed", url=card["url"], error=str(exc))
            return "", None
        description, posted_at, iframe_src = parse_detail(response.text)
        if iframe_src:
            await polite_pause()
            try:
                frame = await self.http.get(iframe_src)
                frame.raise_for_status()
                frame_text = parse_iframe_description(frame.text)
                # below ~200 chars it's an empty shell; keep the page fallback
                if len(frame_text) > 200:
                    description = frame_text
            except Exception as exc:  # noqa: BLE001
                log.warning("devbg_iframe_failed", url=iframe_src, error=str(exc))
        return description, posted_at
