from types import SimpleNamespace
from unittest.mock import AsyncMock

from telegram.error import TelegramError
from test_models import make_posting

from jobscout.digest import escape_md, format_card, send_digest
from jobscout.models import ScoreResult


def make_bot() -> SimpleNamespace:
    return SimpleNamespace(send_message=AsyncMock())


async def seed_scored(store, external_id="e1", fit=85, company="Acme Ltd"):
    pid = await store.insert_posting(make_posting(external_id=external_id, company=company), None)
    await store.save_score(
        pid,
        ScoreResult(
            fit_score=fit,
            stack_match=8,
            seniority_gap=0,
            degree_gate="none",
            lane="ai",
            red_flags=["on-site"],
            cv_keywords=["Python"],
            reason="fits",
        ),
    )
    await store.save_draft(pid, "cv_ai.pdf", "Hi - draft text")
    return pid


async def eligible_rows(store):
    from datetime import UTC, datetime

    return await store.eligible_for_digest(datetime.now(UTC))


def test_escape_md_covers_special_chars():
    assert escape_md("a-b.c(d)!") == r"a\-b\.c\(d\)\!"


async def test_card_contains_score_company_draft(store):
    pid = await seed_scored(store)
    row = (await eligible_rows(store))[0]
    card = format_card(row, await store.latest_draft(pid), watched=False)
    assert "85/100" in card and "Acme Ltd" in card
    assert "Hi - draft text" in card and "cv\\_ai\\.pdf" in card


async def test_digest_sends_header_and_cards_and_marks_digested(store):
    await seed_scored(store, "e1")
    await seed_scored(store, "e2", company="Initech")
    bot = make_bot()
    sent = await send_digest(bot, 1, store, await eligible_rows(store), dream_companies=[])
    assert sent == 2
    assert bot.send_message.await_count == 3  # header + 2 cards
    assert all(r["status"] == "digested" for r in store.postings.values())


async def test_digest_never_resends_delivered(store):
    await seed_scored(store, "e1")
    bot = make_bot()
    rows = await eligible_rows(store)
    assert await send_digest(bot, 1, store, rows, dream_companies=[]) == 1
    # even if the same row is passed again, delivered-ids block it
    assert await send_digest(bot, 1, store, rows, dream_companies=[]) == 0


async def test_empty_day_still_sends_explicit_message(store):
    bot = make_bot()
    sent = await send_digest(
        bot, 1, store, [], dream_companies=[], extra_lines=["weekly manual check: jobs.bg"]
    )
    assert sent == 0
    text = bot.send_message.await_args_list[0].kwargs["text"]
    assert "nothing new" in text and "jobs.bg" in text


async def test_markdown_failure_falls_back_to_plain_text(store):
    await seed_scored(store, "e1")
    bot = make_bot()
    bot.send_message.side_effect = [None, TelegramError("bad markup"), None]
    sent = await send_digest(bot, 1, store, await eligible_rows(store), dream_companies=[])
    assert sent == 1
    fallback = bot.send_message.await_args_list[2]
    assert "parse_mode" not in fallback.kwargs  # plain retry, still with buttons
    assert fallback.kwargs["reply_markup"] is not None


async def test_watched_company_flagged(store):
    await seed_scored(store, "e1", company="Initech Ltd")
    bot = make_bot()
    await send_digest(bot, 1, store, await eligible_rows(store), dream_companies=["initech"])
    card = bot.send_message.await_args_list[1].kwargs["text"]
    assert "watched company" in card
