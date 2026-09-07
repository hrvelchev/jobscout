"""The morning digest: ranked cards with buttons, delivered to one owner.

Rules inherited from the author's production bots:
- MarkdownV2 is escaped char-by-char; the draft lives in a code fence and is
  deliberately not escaped.
- A TelegramError on a card retries the same card as plain text - formatting
  must never cost a delivery.
- Delivered posting ids are tracked in app_state: a card is sent once, ever.
- The digest ALWAYS sends something. A silent morning is indistinguishable
  from a broken bot, so an empty day still says so explicitly.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import structlog
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import TelegramError

from jobscout.store import Store

log = structlog.get_logger()

DELIVERED_KEY = "digest_delivered_ids"
MAX_DELIVERED_TRACKED = 1000
_MD2_SPECIAL = set(r"_*[]()~`>#+-=|{}.!\\")


def escape_md(text: str) -> str:
    return "".join(("\\" + ch) if ch in _MD2_SPECIAL else ch for ch in text)


def keyboard(posting_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Applied", callback_data=f"job_applied_{posting_id}"),
                InlineKeyboardButton("Skip", callback_data=f"job_skip_{posting_id}"),
            ],
            [
                InlineKeyboardButton("Snooze 3d", callback_data=f"job_snooze_{posting_id}"),
                InlineKeyboardButton("Details", callback_data=f"job_details_{posting_id}"),
            ],
        ]
    )


def format_card(row: dict[str, Any], draft: dict[str, Any] | None, *, watched: bool) -> str:
    score = row["score"]
    salary = row.get("salary_raw") or "no salary posted"
    posted = row.get("posted_at")
    age = f"{(datetime.now(UTC) - posted).days}d ago" if posted else "date unknown"
    flags = ", ".join(score.red_flags) if score.red_flags else "none"
    keywords = ", ".join(score.cv_keywords) if score.cv_keywords else "-"
    header = (
        f"*\\#{row['posting_id']} \\- {score.fit_score}/100 \\- "
        f"{escape_md(score.lane.upper())} \\- {escape_md(row['company'])}*"
    )
    lines = [
        header,
        f"*{escape_md(row['title'])}* \\- {escape_md(row.get('location') or 'location n/a')}",
        escape_md(f"{salary} - posted {age} - {row['source']}")
        + (" \\- *watched company*" if watched else ""),
        "",
        f"*Why:* {escape_md(score.reason or '-')}",
        "",
        f"*Flags:* {escape_md(flags)}",
        "",
        f"*Keywords:* {escape_md(keywords)}",
        "",
    ]
    if draft:
        lines.append(f"*CV:* {escape_md(draft['cv_variant'])}")
        lines.append("```\n" + draft["note_text"] + "\n```")
    lines.append(escape_md(row["url"]))
    return "\n".join(lines)


def plain_card(row: dict[str, Any], draft: dict[str, Any] | None) -> str:
    score = row["score"]
    parts = [
        f"#{row['posting_id']} - {score.fit_score}/100 - {row['company']} - {row['title']}",
        "",
        f"Why: {score.reason}",
    ]
    if draft:
        parts.append(f"CV: {draft['cv_variant']}\n\n{draft['note_text']}")
    parts.append(row["url"])
    return "\n".join(parts)


async def _delivered_ids(store: Store) -> list[int]:
    import json

    raw = await store.get_state(DELIVERED_KEY)
    return json.loads(raw) if raw else []


async def _mark_delivered(store: Store, delivered: list[int]) -> None:
    import json

    await store.set_state(DELIVERED_KEY, json.dumps(delivered[-MAX_DELIVERED_TRACKED:]))


async def send_digest(
    bot,
    chat_id: int,
    store: Store,
    ranked_rows: list[dict[str, Any]],
    *,
    dream_companies: list[str],
    extra_lines: list[str] | None = None,
) -> int:
    """Send header + one card per row; returns the number of cards sent."""
    delivered = await _delivered_ids(store)
    fresh_rows = [r for r in ranked_rows if r["posting_id"] not in delivered]

    header = f"jobscout digest: {len(fresh_rows)} posting(s)"
    for line in extra_lines or []:
        header += f"\n{line}"
    if not fresh_rows:
        header = "jobscout digest: nothing new today (that is a report, not a malfunction)"
        for line in extra_lines or []:
            header += f"\n{line}"
    await bot.send_message(chat_id=chat_id, text=header)

    sent = 0
    for row in fresh_rows:
        posting_id = row["posting_id"]
        draft = await store.latest_draft(posting_id)
        watched = row.get("company_norm") in dream_companies
        try:
            await bot.send_message(
                chat_id=chat_id,
                text=format_card(row, draft, watched=watched),
                parse_mode="MarkdownV2",
                reply_markup=keyboard(posting_id),
                disable_web_page_preview=True,
            )
        except TelegramError as exc:
            log.warning("digest_markdown_failed", posting_id=posting_id, error=str(exc))
            await bot.send_message(
                chat_id=chat_id,
                text=plain_card(row, draft),
                reply_markup=keyboard(posting_id),
                disable_web_page_preview=True,
            )
        delivered.append(posting_id)
        await store.set_status(posting_id, "digested")
        sent += 1
    await _mark_delivered(store, delivered)
    log.info("digest_sent", cards=sent)
    return sent
