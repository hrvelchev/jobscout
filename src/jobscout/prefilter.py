"""Free filter that kills postings BEFORE any paid LLM call.

Verdict order is fixed and every fetched posting gets exactly one audited
verdict - the pipeline never silently swallows anything.
"""

from __future__ import annotations

import structlog

from jobscout.models import RawPosting

log = structlog.get_logger()

VERDICT_EXCLUDED = "excluded"
VERDICT_NO_KEYWORD = "no_lane_kw"
VERDICT_PASSED = "passed"


class Prefilter:
    def __init__(self, exclude_patterns: list[str], required_keywords: list[str]):
        self.exclude_patterns = [p.lower() for p in exclude_patterns]
        self.required_keywords = [k.lower() for k in required_keywords]

    def _haystack(self, posting: RawPosting) -> str:
        return f"{posting.title}\n{posting.description}".lower()

    def judge(self, posting: RawPosting) -> tuple[str, str]:
        """(verdict, detail). Order: excluded beats no_lane_kw beats passed."""
        haystack = self._haystack(posting)
        for pattern in self.exclude_patterns:
            if pattern in haystack:
                return VERDICT_EXCLUDED, pattern
        if self.required_keywords:
            for keyword in self.required_keywords:
                if keyword in haystack:
                    return VERDICT_PASSED, keyword
            return VERDICT_NO_KEYWORD, ""
        return VERDICT_PASSED, ""

    def filter(self, postings: list[RawPosting]) -> tuple[list[RawPosting], list[dict]]:
        """(survivors, scan_rows) - one scan row per input, survivors included,
        shaped for Store.record_scan."""
        survivors: list[RawPosting] = []
        scan_rows: list[dict] = []
        counts = {VERDICT_EXCLUDED: 0, VERDICT_NO_KEYWORD: 0, VERDICT_PASSED: 0}
        for posting in postings:
            verdict, detail = self.judge(posting)
            counts[verdict] += 1
            scan_rows.append(
                {
                    "source": posting.source,
                    "external_id": posting.external_id,
                    "verdict": verdict,
                    "detail": detail,
                    "url": posting.url,
                    "company": posting.company,
                    "title": posting.title,
                }
            )
            if verdict == VERDICT_PASSED:
                survivors.append(posting)
        log.info(
            "prefilter_done",
            fetched=len(postings),
            survivors=counts[VERDICT_PASSED],
            excluded=counts[VERDICT_EXCLUDED],
            no_keyword=counts[VERDICT_NO_KEYWORD],
        )
        return survivors, scan_rows
