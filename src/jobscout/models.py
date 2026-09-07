"""Core value types shared across the pipeline."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import TypedDict

# Legal/geographic suffixes that make the same employer look different across
# boards ("Acme Ltd" on dev.bg vs "Acme Bulgaria EOOD" on Greenhouse).
_COMPANY_NOISE = {
    "ltd",
    "ead",
    "ood",
    "eood",
    "ad",
    "jsc",
    "plc",
    "gmbh",
    "inc",
    "llc",
    "co",
    "sa",
    "srl",
    "bv",
    "bulgaria",
    "sofia",
    "group",
    "holdings",
}


def normalize_company(name: str) -> str:
    tokens = re.split(r"[^a-z0-9]+", name.lower())
    kept = [t for t in tokens if t and t not in _COMPANY_NOISE]
    return " ".join(kept) or name.lower().strip()


@dataclass(frozen=True)
class RawPosting:
    source: str  # "devbg" | "greenhouse"
    external_id: str
    url: str
    company: str
    title: str
    description: str
    location: str | None = None
    remote: bool | None = None
    salary_raw: str | None = None
    salary_min: int | None = None
    salary_max: int | None = None
    salary_currency: str | None = None
    posted_at: datetime | None = None

    @property
    def company_norm(self) -> str:
        return normalize_company(self.company)

    def embed_text(self) -> str:
        """The text a posting is embedded on: stable identity, not full body."""
        return f"{self.title}\n{self.company_norm}\n{self.location or ''}\n{self.description[:500]}"


VALID_LANES = ("ai", "quant", "data", "energy", "other")
VALID_DEGREE_GATES = ("hard", "soft", "none")


@dataclass
class ScoreResult:
    fit_score: int
    stack_match: int
    seniority_gap: int
    degree_gate: str
    lane: str
    red_flags: list[str] = field(default_factory=list)
    cv_keywords: list[str] = field(default_factory=list)
    reason: str = ""

    @classmethod
    def from_payload(cls, payload: dict) -> ScoreResult:
        """Coerce a model-produced dict into a valid result. Clamps, never raises
        on out-of-range values - a weird score must not kill the pipeline."""
        sub = payload.get("subscores") or {}
        lane = str(sub.get("lane", "other")).lower()
        gate = str(sub.get("degree_gate", "soft")).lower()
        return cls(
            fit_score=max(0, min(100, int(payload.get("fit_score", 0)))),
            stack_match=max(0, min(10, int(sub.get("stack_match", 0)))),
            seniority_gap=max(-2, min(2, int(sub.get("seniority_gap", 0)))),
            degree_gate=gate if gate in VALID_DEGREE_GATES else "soft",
            lane=lane if lane in VALID_LANES else "other",
            red_flags=[str(x) for x in (payload.get("red_flags") or [])][:5],
            cv_keywords=[str(x) for x in (payload.get("cv_keywords") or [])][:10],
            reason=str(payload.get("reason", ""))[:300],
        )


class PipelineState(TypedDict, total=False):
    """State threaded through the LangGraph per-posting pipeline."""

    posting_id: int
    posting_text: str
    profile: str
    notes_examples: str
    lane_cv_map: dict[str, str]
    score: dict | None  # ScoreResult as dict, JSON-serializable
    cv_variant: str | None
    draft: str | None
    draft_attempts: int
    sanitize_note: str | None
    outcome: str  # over_daily_cap | score_failed | scored_below_threshold
    #             | drafted | draft_unusable
