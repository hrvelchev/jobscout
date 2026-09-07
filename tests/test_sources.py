"""Source adapter tests over pinned fixture files - if dev.bg or Greenhouse
change their markup/shape, these fixtures plus the degradation flag are the
tripwire."""

import json
from pathlib import Path

import pytest

from jobscout.sources import devbg as devbg_mod
from jobscout.sources import greenhouse as gh_mod
from jobscout.sources.devbg import DevBgSource, parse_detail, parse_listing, parse_salary
from jobscout.sources.greenhouse import GreenhouseSource, parse_board, strip_html

FIXTURES = Path(__file__).parent / "fixtures"


class FakeResponse:
    def __init__(self, text: str = "", payload: dict | None = None, status: int = 200):
        self.text = text
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


class FakeHttp:
    """Maps url-substring -> FakeResponse; records every request url."""

    def __init__(self, routes: dict[str, FakeResponse]):
        self.routes = routes
        self.requested: list[str] = []

    async def get(self, url: str) -> FakeResponse:
        self.requested.append(url)
        for key, response in self.routes.items():
            if key in url:
                return response
        return FakeResponse(status=404)


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    async def instant():
        return None

    monkeypatch.setattr(devbg_mod, "polite_pause", instant)
    monkeypatch.setattr(gh_mod, "polite_pause", instant)


# --- dev.bg parsing ---------------------------------------------------------

def test_parse_listing_extracts_cards():
    cards = parse_listing((FIXTURES / "devbg_listing.html").read_text(encoding="utf-8"))
    assert len(cards) == 2  # malformed third card skipped, not crashed
    first = cards[0]
    assert first["external_id"] == "554746"
    assert first["company"] == "Acme Ltd"
    assert first["title"] == "Junior Python Developer"
    assert first["url"].endswith("acme-junior-python-developer/")
    assert first["remote"] is True


def test_parse_listing_salary_badge():
    cards = parse_listing((FIXTURES / "devbg_listing.html").read_text(encoding="utf-8"))
    second = cards[1]
    assert (second["salary_min"], second["salary_max"]) == (3000, 5500)
    assert second["salary_currency"] == "BGN"
    assert second["remote"] is None


def test_parse_salary_variants():
    assert parse_salary("2 500 - 4 000 лв.") == (2500, 4000, "BGN")
    assert parse_salary("3000-6600 EUR net") == (3000, 6600, "EUR")
    assert parse_salary("no salary here") == (None, None, None)


def test_parse_listing_alien_html_returns_empty():
    assert parse_listing("<html><body><h1>redesign!</h1></body></html>") == []


def test_parse_detail_description_and_date():
    text, posted_at = parse_detail((FIXTURES / "devbg_detail.html").read_text(encoding="utf-8"))
    assert "Junior Python Developer" in text and "pandas" in text
    assert posted_at is not None and posted_at.date().isoformat() == "2026-09-04"


# --- dev.bg source flow -----------------------------------------------------

async def test_devbg_fetch_only_new_ids_get_detail_requests(store):
    listing_html = (FIXTURES / "devbg_listing.html").read_text(encoding="utf-8")
    detail_html = (FIXTURES / "devbg_detail.html").read_text(encoding="utf-8")
    http = FakeHttp({
        "/company/jobs/python/": FakeResponse(text=listing_html),
        "?pj=": FakeResponse(text=detail_html),
    })
    source = DevBgSource(store, http, ["python"])
    postings = await source.fetch()
    assert {p.external_id for p in postings} == {"554746", "554747"}
    assert postings[0].description  # detail was fetched and parsed

    # second run: both ids seen -> zero detail requests
    http.requested.clear()
    postings2 = await source.fetch()
    assert postings2 == []
    assert all("?pj=" not in u for u in http.requested)


async def test_devbg_zero_cards_from_real_html_sets_degraded(store):
    http = FakeHttp({"/company/jobs/python/": FakeResponse(text="<html>" + "x" * 20_000)})
    source = DevBgSource(store, http, ["python"])
    assert await source.fetch() == []
    assert source.degraded == ["python"]


async def test_devbg_listing_http_error_sets_degraded(store):
    http = FakeHttp({"/company/jobs/python/": FakeResponse(status=500)})
    source = DevBgSource(store, http, ["python"])
    assert await source.fetch() == []
    assert source.degraded == ["python"]


# --- greenhouse -------------------------------------------------------------

def test_parse_board_unescapes_content_and_skips_malformed():
    payload = json.loads((FIXTURES / "greenhouse_jobs.json").read_text(encoding="utf-8"))
    postings = parse_board(payload, "examplecompany")
    assert len(postings) == 2  # ghost skipped
    analyst = postings[0]
    assert analyst.external_id == "examplecompany:4957001"
    assert analyst.company == "examplecompany"
    assert "Python and SQL required" in analyst.description
    assert "&lt;" not in analyst.description and "<div>" not in analyst.description
    assert analyst.posted_at is not None
    assert postings[1].posted_at is None  # bad timestamp tolerated


def test_strip_html_flattens_escaped_markup():
    text = strip_html("&lt;p&gt;one&lt;/p&gt;&lt;p&gt;two&lt;/p&gt;")
    assert "one" in text and "two" in text and "<p>" not in text


async def test_greenhouse_fetch_continues_past_broken_board(store):
    good = json.loads((FIXTURES / "greenhouse_jobs.json").read_text(encoding="utf-8"))
    http = FakeHttp({
        "boards-api.greenhouse.io/v1/boards/broken/": FakeResponse(status=500),
        "boards-api.greenhouse.io/v1/boards/examplecompany/": FakeResponse(payload=good),
    })
    source = GreenhouseSource(store, http, ["broken", "examplecompany"])
    postings = await source.fetch()
    assert len(postings) == 2  # broken board logged and skipped, good one parsed
