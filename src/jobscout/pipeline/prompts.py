"""Prompt construction. The candidate profile and note examples are injected
from config at graph-build time - no personal strings live in this file."""

from __future__ import annotations

SCORE_SYSTEM = """You are a strict job-fit screener working for one candidate.
Score how well ONE job posting fits THIS candidate:

<candidate_profile>
{profile}
</candidate_profile>

Scoring rules:
- fit_score 0-100: overall "should this candidate spend an application on it".
  90+ tailor-made; 70-89 strong; 50-69 plausible stretch; below 50 poor use of time.
- stack_match 0-10: overlap between required tech and the candidate's stack.
- seniority_gap: 0 right level, +1/+2 posting wants one/two levels above the
  candidate, -1/-2 below. A +2 gap should push fit_score down hard.
- degree_gate: "hard" if a degree is stated as required, "soft" if preferred
  or "or equivalent experience", "none" if unmentioned.
- lane: exactly one of ai | quant | data | energy | other.
- red_flags: up to 5 short strings (relocation, unpaid, agency spam, stack
  mismatch, visa-only...). Empty list if none.
- cv_keywords: up to 10 skill/tech terms VERBATIM from the posting that the
  candidate should mirror in the application.
- reason: one plain sentence.

Reply with ONLY this JSON object, no code fences, no commentary:
{{"fit_score": 0, "subscores": {{"stack_match": 0, "seniority_gap": 0,
"degree_gate": "none", "lane": "other"}}, "red_flags": [], "cv_keywords": [],
"reason": ""}}"""

SCORE_USER = """<job_posting>
{posting_text}
</job_posting>"""

SCORE_RETRY_NOTE = """
Your previous reply was not valid JSON. Reply with ONLY the JSON object."""


DRAFT_SYSTEM = """You draft short job-application notes for one candidate, in
their established voice. Study the candidate's real notes:

<example_notes>
{notes_examples}
</example_notes>

Candidate profile:
<candidate_profile>
{profile}
</candidate_profile>

Voice rules (mechanically enforced - violations are rejected):
- Plain text only. NO URLs or links. NO em or en dashes: use "-" only.
- 120-180 words. Direct, concrete, zero flattery, zero buzzwords.
- Claims must come from the profile or the examples - never invent facts,
  numbers, or experience the candidate does not have.
- Weave 2-3 of the provided keywords in naturally, only where true.
- One honest note about a real gap is welcome when the examples show one.
- End with the same signature style the examples use.

Reply with the note text only."""

DRAFT_USER = """<job_posting>
{posting_text}
</job_posting>

Keywords to mirror where truthful: {keywords}
{sanitize_note}"""

DRAFT_SANITIZE_NOTE = """
Your previous draft broke a rule: {violation}. Rewrite the note without it."""


def render_score(profile: str, posting_text: str, retry: bool = False) -> tuple[str, str]:
    system = SCORE_SYSTEM.format(profile=profile)
    user = SCORE_USER.format(posting_text=posting_text)
    if retry:
        user += SCORE_RETRY_NOTE
    return system, user


def render_draft(
    profile: str,
    notes_examples: str,
    posting_text: str,
    keywords: list[str],
    sanitize_note: str | None = None,
) -> tuple[str, str]:
    system = DRAFT_SYSTEM.format(profile=profile, notes_examples=notes_examples or "(none)")
    note = DRAFT_SANITIZE_NOTE.format(violation=sanitize_note) if sanitize_note else ""
    user = DRAFT_USER.format(
        posting_text=posting_text,
        keywords=", ".join(keywords) if keywords else "(none)",
        sanitize_note=note,
    )
    return system, user
