"""Callback-button behavior via the real PTB Application (built, never started)."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

from test_models import make_posting

from jobscout.models import ScoreResult
from jobscout.telegram_bot import build_application

OWNER = 111
FAKE_TOKEN = "1111111111:TEST-token-not-real"


def get_callback_handler(app):
    from telegram.ext import CallbackQueryHandler

    for group in app.handlers.values():
        for handler in group:
            if isinstance(handler, CallbackQueryHandler):
                return handler.callback
    raise AssertionError("no callback handler registered")


def make_update(data: str, user_id: int = OWNER):
    query = SimpleNamespace(
        data=data,
        answer=AsyncMock(),
        edit_message_text=AsyncMock(),
        edit_message_reply_markup=AsyncMock(),
        message=SimpleNamespace(text="card text", reply_text=AsyncMock()),
    )
    update = SimpleNamespace(
        callback_query=query,
        effective_user=SimpleNamespace(id=user_id),
    )
    return update, query


async def seed(store, company="Acme Ltd", external_id="x1"):
    pid = await store.insert_posting(make_posting(company=company, external_id=external_id), None)
    await store.save_score(
        pid,
        ScoreResult(
            fit_score=80,
            stack_match=8,
            seniority_gap=0,
            degree_gate="none",
            lane="ai",
        ),
    )
    await store.save_draft(pid, "cv_ai.pdf", "draft")
    return pid


async def invoke(store, data, user_id=OWNER):
    app = build_application(FAKE_TOKEN, OWNER, store)
    handler = get_callback_handler(app)
    update, query = make_update(data, user_id)
    await handler(update, None)
    return query


async def test_applied_sets_status_and_event(store):
    pid = await seed(store)
    query = await invoke(store, f"job_applied_{pid}")
    assert store.postings[pid]["status"] == "applied"
    assert any(e["to_status"] == "applied" for e in store.events)
    assert "APPLIED" in query.edit_message_text.await_args.args[0]


async def test_applied_warns_on_recent_same_company(store):
    first = await seed(store, company="Acme Ltd")
    await store.set_status(first, "applied")
    second = await store.insert_posting(make_posting(external_id="x2", company="Acme EOOD"), None)
    # note: FakeStore company_norm equality - both normalize to "acme"
    query = await invoke(store, f"job_applied_{second}")
    assert "already applied" in query.edit_message_text.await_args.args[0]


async def test_skip_and_snooze(store):
    pid = await seed(store)
    await invoke(store, f"job_skip_{pid}")
    assert store.postings[pid]["status"] == "skipped"

    pid2 = await seed(store, external_id="x2")
    await invoke(store, f"job_snooze_{pid2}")
    assert store.postings[pid2]["status"] == "snoozed"
    assert store.postings[pid2]["snooze_until"] is not None


async def test_details_replies_with_description_and_draft(store):
    pid = await seed(store)
    query = await invoke(store, f"job_details_{pid}")
    text = query.message.reply_text.await_args.args[0]
    assert "Build pipelines" in text and "draft" in text
    assert store.postings[pid]["status"] == "scored"  # details is read-only


async def test_unauthorized_user_is_silently_dropped(store):
    pid = await seed(store)
    query = await invoke(store, f"job_applied_{pid}", user_id=999)
    assert store.postings[pid]["status"] == "scored"  # nothing happened
    query.edit_message_text.assert_not_awaited()


async def test_edit_failure_still_acks_via_reply(store):
    pid = await seed(store)
    app = build_application(FAKE_TOKEN, OWNER, store)
    handler = get_callback_handler(app)
    update, query = make_update(f"job_skip_{pid}")
    query.edit_message_text.side_effect = RuntimeError("message too old")
    await handler(update, None)
    assert store.postings[pid]["status"] == "skipped"
    query.message.reply_text.assert_awaited()  # the ack survived the edit failure


async def test_cost_command_reports_usage(store):
    await store.log_usage("claude-haiku-4-5", 1000, 100, 0.0015, "score")
    app = build_application(FAKE_TOKEN, OWNER, store)
    from telegram.ext import CommandHandler

    cost_handler = None
    for group in app.handlers.values():
        for handler in group:
            if isinstance(handler, CommandHandler) and "cost" in handler.commands:
                cost_handler = handler.callback
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=OWNER),
        message=SimpleNamespace(reply_text=AsyncMock()),
    )
    await cost_handler(update, None)
    text = update.message.reply_text.await_args.args[0]
    assert "1 calls" in text and "$0.00" in text
