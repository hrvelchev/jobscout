from test_models import make_posting

from jobscout.prefilter import (
    VERDICT_EXCLUDED,
    VERDICT_NO_KEYWORD,
    VERDICT_PASSED,
    Prefilter,
)


def make_prefilter() -> Prefilter:
    return Prefilter(
        exclude_patterns=["php developer", "internship"],
        required_keywords=["python", "data engineer"],
    )


def test_exclude_beats_required_keyword():
    # contains both an exclude pattern and a required keyword: exclusion wins
    posting = make_posting(description="PHP developer role, some Python too")
    verdict, detail = make_prefilter().judge(posting)
    assert verdict == VERDICT_EXCLUDED and detail == "php developer"


def test_no_keyword_kills():
    posting = make_posting(title="Java Architect", description="Spring, Kafka, K8s")
    verdict, _ = make_prefilter().judge(posting)
    assert verdict == VERDICT_NO_KEYWORD


def test_passes_with_keyword_in_title():
    posting = make_posting(title="Senior Python Developer", description="nice job")
    verdict, detail = make_prefilter().judge(posting)
    assert verdict == VERDICT_PASSED and detail == "python"


def test_empty_required_keywords_means_everything_passes():
    prefilter = Prefilter(exclude_patterns=["php"], required_keywords=[])
    verdict, _ = prefilter.judge(make_posting(description="anything at all"))
    assert verdict == VERDICT_PASSED


def test_filter_audits_every_item_including_survivors():
    postings = [
        make_posting(external_id="a", title="Python Dev", description="x"),
        make_posting(external_id="b", title="PHP Developer job", description="x"),
        make_posting(external_id="c", title="Java Dev", description="x"),
    ]
    survivors, scan_rows = make_prefilter().filter(postings)
    assert [p.external_id for p in survivors] == ["a"]
    assert len(scan_rows) == 3  # one audit row per input, no exceptions
    verdicts = {r["external_id"]: r["verdict"] for r in scan_rows}
    assert verdicts == {"a": VERDICT_PASSED, "b": VERDICT_EXCLUDED, "c": VERDICT_NO_KEYWORD}
