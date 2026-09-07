from jobscout.models import RawPosting, ScoreResult, normalize_company


def make_posting(**overrides) -> RawPosting:
    defaults = dict(
        source="devbg",
        external_id="x1",
        url="https://example.com/j/1",
        company="Acme Ltd",
        title="Data Engineer",
        description="Build pipelines." * 100,
    )
    defaults.update(overrides)
    return RawPosting(**defaults)


def test_normalize_company_strips_legal_suffixes():
    assert normalize_company("Acme Ltd") == "acme"
    assert normalize_company("Acme Bulgaria EOOD") == "acme"
    assert normalize_company("ACME Group Holdings, Inc.") == "acme"


def test_normalize_company_never_returns_empty():
    assert normalize_company("EOOD") == "eood"


def test_normalize_company_distinct_companies_stay_distinct():
    assert normalize_company("Acme Ltd") != normalize_company("Initech Ltd")


def test_embed_text_truncates_description():
    posting = make_posting()
    text = posting.embed_text()
    assert "Data Engineer" in text and "acme" in text
    assert len(text) < 600


def test_score_from_payload_happy_path():
    score = ScoreResult.from_payload(
        {
            "fit_score": 87,
            "subscores": {
                "stack_match": 8,
                "seniority_gap": 1,
                "degree_gate": "none",
                "lane": "ai",
            },
            "red_flags": ["on-site only"],
            "cv_keywords": ["Python", "RAG"],
            "reason": "strong match",
        }
    )
    assert score.fit_score == 87 and score.lane == "ai" and score.degree_gate == "none"
    assert score.cv_keywords == ["Python", "RAG"]


def test_score_from_payload_clamps_and_coerces():
    score = ScoreResult.from_payload(
        {
            "fit_score": 250,
            "subscores": {
                "stack_match": 99,
                "seniority_gap": -7,
                "degree_gate": "MAYBE",
                "lane": "blockchain",
            },
            "red_flags": [f"flag{i}" for i in range(20)],
            "cv_keywords": [f"kw{i}" for i in range(20)],
        }
    )
    assert score.fit_score == 100
    assert score.stack_match == 10
    assert score.seniority_gap == -2
    assert score.degree_gate == "soft"
    assert score.lane == "other"
    assert len(score.red_flags) == 5 and len(score.cv_keywords) == 10


def test_score_from_payload_missing_keys():
    score = ScoreResult.from_payload({})
    assert score.fit_score == 0 and score.lane == "other" and score.degree_gate == "soft"
