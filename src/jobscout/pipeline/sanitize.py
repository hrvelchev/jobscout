"""Mechanical enforcement of draft rules the prompt merely requests.

Returns (cleaned_text, violation_note). A non-None note means the model broke
a rule badly enough that a retry-with-feedback is warranted (Ceco discipline:
one corrective retry, then accept the cleaned version).
"""

from __future__ import annotations

import re

MAX_DRAFT_CHARS = 1_200

_URL_RE = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)
_FENCE_RE = re.compile(r"```+")

PLACEHOLDER = "(draft unusable after retry - write this note by hand)"


def sanitize_draft(text: str) -> tuple[str, str | None]:
    violations: list[str] = []
    cleaned = text.strip()

    if _URL_RE.search(cleaned):
        cleaned = _URL_RE.sub("", cleaned)
        violations.append("contained a URL - never include links, the CV carries them")

    if "—" in cleaned or "–" in cleaned:
        cleaned = cleaned.replace("—", "-").replace("–", "-")
        violations.append("used em/en dashes - use plain '-' only")

    if _FENCE_RE.search(cleaned):
        cleaned = _FENCE_RE.sub("", cleaned)  # cosmetic; not retry-worthy

    cleaned = re.sub(r"[ \t]+\n", "\n", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()

    if len(cleaned) > MAX_DRAFT_CHARS:
        cleaned = cleaned[:MAX_DRAFT_CHARS].rsplit(" ", 1)[0].rstrip() + "..."

    return cleaned, ("; ".join(violations) if violations else None)
