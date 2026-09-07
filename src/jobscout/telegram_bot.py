"""Owner-facing Telegram bot: digest buttons + a few commands.

Only the whitelisted owner id is served; everything else is silently dropped.
Button presses are the ONLY write path a human has - and even those only move
statuses. Nothing here applies to jobs; nothing here talks to employers.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

import structlog
from telegram import Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

from jobscout.store import Store

log = structlog.get_logger()

CALLBACK_RE = re.compile(r"^job_(applied|skip|snooze|details)_(\d+)$")
SNOOZE_DAYS = 3
DUPLICATE_WARN_DAYS = 90


def build_application(
    token: str,
    owner_id: int,
    store: Store,
    *,
    run_scout=None,
    run_digest=None,
    sync_sheet=None,
    sheet_url: str = "",
) -> Application:
    app = Application.builder().token(token).build()

    def authorized(update: Update) -> bool:
        user = update.effective_user
        return user is not None and user.id == owner_id

    async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        await query.answer()
        if not authorized(update):
            log.warning("unauthorized_callback", user=update.effective_user)
            return
        match = CALLBACK_RE.match(query.data or "")
        if not match:
            return
        action, posting_id = match.group(1), int(match.group(2))
        posting = await store.get_posting(posting_id)
        if posting is None:
            await query.edit_message_reply_markup(reply_markup=None)
            return

        if action == "applied":
            warning = ""
            if await store.applied_same_company_since(posting["company_norm"], DUPLICATE_WARN_DAYS):
                warning = "\n! you already applied to this company recently"
            await store.set_status(posting_id, "applied", note="via digest button")
            if sync_sheet is not None:
                await sync_sheet()  # errors are swallowed inside; ack still lands
            await _confirm(query, f"marked APPLIED{warning}")
        elif action == "skip":
            await store.set_status(posting_id, "skipped", note="via digest button")
            await _confirm(query, "skipped")
        elif action == "snooze":
            until = datetime.now(UTC) + timedelta(days=SNOOZE_DAYS)
            await store.set_snooze(posting_id, until)
            await _confirm(query, f"snoozed until {until.date().isoformat()}")
        elif action == "details":
            draft = await store.latest_draft(posting_id)
            text = (posting.get("description") or "(no description)")[:3_500]
            if draft:
                text += f"\n\n--- draft ({draft['cv_variant']}) ---\n{draft['note_text']}"
            await query.message.reply_text(text, disable_web_page_preview=True)

    async def _confirm(query, suffix: str) -> None:
        try:
            base = query.message.text or ""
            await query.edit_message_text(f"{base}\n\n- {suffix}", disable_web_page_preview=True)
        except Exception:  # noqa: BLE001 - formatting must never lose the ack
            await query.message.reply_text(f"- {suffix}")

    async def cmd_cost(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not authorized(update):
            return
        since = datetime.now(UTC) - timedelta(days=30)
        calls, total = await store.usage_since(since)
        await update.message.reply_text(f"LLM usage, last 30 days: {calls} calls, ${total:.2f}")

    async def cmd_scout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not authorized(update) or run_scout is None:
            return
        await update.message.reply_text("scouting...")
        counts = await run_scout()
        await update.message.reply_text(f"scout done: {counts}")

    async def cmd_digest(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not authorized(update) or run_digest is None:
            return
        sent = await run_digest()
        if sent == 0:
            await update.message.reply_text("digest: nothing new to send")

    async def cmd_sheet(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not authorized(update):
            return
        if sync_sheet is None or not sheet_url:
            await update.message.reply_text("sheet tracker is not configured")
            return
        result = await sync_sheet()
        if result is None:
            await update.message.reply_text("sheet sync failed - check the logs")
            return
        await update.message.reply_text(
            f"sheet synced: {result['appended']} new, {result['updated']} updated\n{sheet_url}",
            disable_web_page_preview=True,
        )

    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(CommandHandler("cost", cmd_cost))
    app.add_handler(CommandHandler("scout", cmd_scout))
    app.add_handler(CommandHandler("digest", cmd_digest))
    app.add_handler(CommandHandler("sheet", cmd_sheet))
    return app
