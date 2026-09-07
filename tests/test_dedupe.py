from fakes import FakeEmbedder
from test_models import make_posting

from jobscout.dedupe import VERDICT_DUP_EXACT, VERDICT_DUP_SEMANTIC, ingest

# hand-built unit vectors with exact, known cosines
V_A = [1.0, 0.0, 0.0, 0.0]
V_A_CLOSE = [0.96, 0.28, 0.0, 0.0]  # cosine(V_A, V_A_CLOSE) = 0.96 >= 0.90
V_FAR = [0.0, 1.0, 0.0, 0.0]  # cosine = 0.0


def embedder_for(postings_vectors: dict) -> FakeEmbedder:
    return FakeEmbedder(dim=4, overrides=postings_vectors)


async def test_exact_duplicate_skipped_and_audited(store):
    posting = make_posting(external_id="same")
    embedder = FakeEmbedder(dim=4)
    fresh1, dups1 = await ingest([posting], store, embedder)
    fresh2, dups2 = await ingest([posting], store, embedder)
    assert len(fresh1) == 1 and dups1 == []
    assert fresh2 == [] and dups2[0]["verdict"] == VERDICT_DUP_EXACT


async def test_semantic_duplicate_same_company_marked(store):
    devbg = make_posting(
        source="devbg", external_id="d1", company="Acme Ltd", title="ML Engineer", description="a"
    )
    greenhouse = make_posting(
        source="greenhouse",
        external_id="g1",
        company="Acme Bulgaria EOOD",
        title="ML Engineer (Sofia)",
        description="b",
    )
    embedder = embedder_for({devbg.embed_text(): V_A, greenhouse.embed_text(): V_A_CLOSE})
    fresh1, _ = await ingest([devbg], store, embedder)
    fresh2, dups2 = await ingest([greenhouse], store, embedder)
    assert fresh2 == []
    assert dups2[0]["verdict"] == VERDICT_DUP_SEMANTIC
    dup_row = next(r for r in store.postings.values() if r["source"] == "greenhouse")
    assert dup_row["duplicate_of"] == fresh1[0][0]


async def test_similar_text_different_company_is_kept(store):
    a = make_posting(external_id="a1", company="Acme Ltd", title="Python Dev")
    b = make_posting(external_id="b1", company="Initech Ltd", title="Python Dev")
    embedder = embedder_for({a.embed_text(): V_A, b.embed_text(): V_A_CLOSE})
    await ingest([a], store, embedder)
    fresh, dups = await ingest([b], store, embedder)
    # identical-looking job, different employer: NOT a duplicate
    assert len(fresh) == 1 and dups == []


async def test_below_threshold_same_company_is_kept(store):
    a = make_posting(external_id="a1", company="Acme", title="Data Engineer")
    b = make_posting(external_id="b1", company="Acme", title="Frontend Designer")
    embedder = embedder_for({a.embed_text(): V_A, b.embed_text(): V_FAR})
    await ingest([a], store, embedder)
    fresh, dups = await ingest([b], store, embedder)
    # same employer, genuinely different job: kept
    assert len(fresh) == 1 and dups == []


async def test_duplicates_never_reach_scoring(store):
    """A semantically-duplicated posting must be invisible to the digest."""
    from datetime import UTC, datetime

    a = make_posting(external_id="a1", company="Acme", title="ML Engineer")
    b = make_posting(
        source="greenhouse", external_id="b1", company="Acme EOOD", title="ML Engineer role"
    )
    embedder = embedder_for({a.embed_text(): V_A, b.embed_text(): V_A_CLOSE})
    await ingest([a, b], store, embedder)
    eligible = await store.eligible_for_digest(datetime.now(UTC))
    assert all(r["external_id"] != "b1" for r in eligible)
