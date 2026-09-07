"""Source adapter conventions.

A source is duck-typed (no ABC): a class constructed with (store, http_client,
config...) exposing `async def fetch(self) -> list[RawPosting]`. Politeness is
non-negotiable: one pass per scout run, INTER_REQUEST_DELAY_S between requests,
and detail pages fetched only for postings not already in the store.
"""

from __future__ import annotations

import asyncio

import httpx

INTER_REQUEST_DELAY_S = 10.0
REQUEST_TIMEOUT_S = 30.0
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"
)


def make_http_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        headers={"User-Agent": USER_AGENT},
        timeout=REQUEST_TIMEOUT_S,
        follow_redirects=True,
    )


async def polite_pause() -> None:
    await asyncio.sleep(INTER_REQUEST_DELAY_S)
