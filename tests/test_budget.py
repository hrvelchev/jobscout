from datetime import UTC, datetime

from jobscout.budget import Budget


async def test_reserve_consumes_wallet(store):
    budget = Budget(store, max_per_day=3)
    assert await budget.reserve() is True
    assert await budget.reserve(2) is True
    assert await budget.remaining_today() == 0


async def test_reserve_refunds_on_overshoot(store):
    budget = Budget(store, max_per_day=2)
    assert await budget.reserve(2) is True
    assert await budget.reserve() is False
    # the failed claim must not leak budget: counter still at cap, not above
    assert await budget.used_today() == 2


async def test_wallet_is_shared_not_per_purpose(store):
    budget = Budget(store, max_per_day=2)
    assert await budget.reserve() is True  # a "score" call
    assert await budget.reserve() is True  # a "draft" call
    assert await budget.reserve() is False  # third call of any kind


async def test_day_key_rolls_over(store):
    budget = Budget(store, max_per_day=1)
    day1 = datetime(2026, 9, 7, 23, 59, tzinfo=UTC)
    day2 = datetime(2026, 9, 8, 0, 1, tzinfo=UTC)
    assert budget.day_key(day1) != budget.day_key(day2)
    # exhaust day1's wallet manually, day2 remains untouched
    await store.bump_counter(budget.day_key(day1), 1)
    assert int(await store.get_state(budget.day_key(day2)) or 0) == 0
