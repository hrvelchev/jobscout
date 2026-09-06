from jobscout.pipeline.sanitize import MAX_DRAFT_CHARS, sanitize_draft


def test_clean_draft_passes_unchanged():
    text = "Hi,\n\nI am applying for the role - my stack fits.\n\nBest, Alex"
    cleaned, violation = sanitize_draft(text)
    assert cleaned == text and violation is None


def test_em_and_en_dashes_replaced_and_flagged():
    cleaned, violation = sanitize_draft("strong fit — really – yes")
    assert "—" not in cleaned and "–" not in cleaned
    assert "- really - yes" in cleaned
    assert violation and "dash" in violation


def test_urls_stripped_and_flagged():
    cleaned, violation = sanitize_draft("see https://example.com/me for more")
    assert "https://" not in cleaned
    assert violation and "URL" in violation


def test_code_fences_removed_without_retry_flag():
    cleaned, violation = sanitize_draft("```\nhello\n```")
    assert "```" not in cleaned and cleaned == "hello"
    assert violation is None  # cosmetic fix, not worth a paid retry


def test_overlong_draft_trimmed_at_word_boundary():
    cleaned, violation = sanitize_draft("word " * 400)
    assert len(cleaned) <= MAX_DRAFT_CHARS + 3
    assert cleaned.endswith("...") and not cleaned.rstrip(".").endswith(" ")
    assert violation is None
