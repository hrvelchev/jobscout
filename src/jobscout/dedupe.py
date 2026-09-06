"""Ingestion with two dedupe layers.

Exact: UNIQUE(source, external_id) - the same posting refetched tomorrow.
Semantic: cosine >= 0.90 AND matching normalized company - the same job
cross-posted on two boards. The company guard stops staffing agencies'
similar-but-different postings from collapsing into one.

Duplicates are stored and audited, never scored or digested.
"""

from __future__ import annotations

import structlog

from jobscout.embeddings import Embedder
from jobscout.models import RawPosting
from jobscout.store import Store

log = structlog.get_logger()

SEMANTIC_THRESHOLD = 0.90

VERDICT_DUP_EXACT = "dup_exact"
VERDICT_DUP_SEMANTIC = "dup_semantic"


async def ingest(
    postings: list[RawPosting], store: Store, embedder: Embedder
) -> tuple[list[tuple[int, RawPosting]], list[dict]]:
    """Insert postings; return ([(posting_id, posting)] of fresh ones,
    scan_rows for the duplicates so every fetched item stays audited)."""
    if not postings:
        return [], []
    embeddings = await embedder.embed([p.embed_text() for p in postings])

    fresh: list[tuple[int, RawPosting]] = []
    dup_rows: list[dict] = []
    counts = {VERDICT_DUP_EXACT: 0, VERDICT_DUP_SEMANTIC: 0}

    for posting, embedding in zip(postings, embeddings, strict=True):
        # semantic check FIRST, against the store as it was before this item -
        # then insert, so two identical postings in one batch also pair up
        dup_of = await store.find_semantic_dup(
            embedding, posting.company_norm, threshold=SEMANTIC_THRESHOLD
        )
        posting_id = await store.insert_posting(posting, embedding)
        if posting_id is None:
            counts[VERDICT_DUP_EXACT] += 1
            dup_rows.append(_scan_row(posting, VERDICT_DUP_EXACT, ""))
            continue
        if dup_of is not None:
            await store.mark_duplicate(posting_id, dup_of)
            counts[VERDICT_DUP_SEMANTIC] += 1
            dup_rows.append(_scan_row(posting, VERDICT_DUP_SEMANTIC, f"of #{dup_of}"))
            continue
        fresh.append((posting_id, posting))

    log.info(
        "ingest_done",
        fetched=len(postings),
        fresh=len(fresh),
        dup_exact=counts[VERDICT_DUP_EXACT],
        dup_semantic=counts[VERDICT_DUP_SEMANTIC],
    )
    return fresh, dup_rows


def _scan_row(posting: RawPosting, verdict: str, detail: str) -> dict:
    return {
        "source": posting.source,
        "external_id": posting.external_id,
        "verdict": verdict,
        "detail": detail,
        "url": posting.url,
        "company": posting.company,
        "title": posting.title,
    }
