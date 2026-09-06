"""The LLM wallet: a hard daily cap enforced in code, not by discipline.

Reserve-before-call: the counter is bumped BEFORE the paid request goes out,
so a crash mid-call over-counts (spends budget on nothing) rather than
under-counts (spends money uncounted). Score and draft calls share one wallet.
"""

from __future__ import annotations

from datetime import UTC, datetime

import structlog

from jobscout.store import Store

log = structlog.get_logger()

# Day keys use UTC deliberately: the cap is about spend, not local calendar days.
COUNTER_PREFIX = "llm_calls:"


class Budget:
    def __init__(self, store: Store, max_per_day: int):
        self.store = store
        self.max_per_day = max_per_day

    def day_key(self, now: datetime | None = None) -> str:
        now = now or datetime.now(UTC)
        return f"{COUNTER_PREFIX}{now.strftime('%Y-%m-%d')}"

    async def used_today(self) -> int:
        return int(await self.store.get_state(self.day_key()) or 0)

    async def remaining_today(self) -> int:
        return max(0, self.max_per_day - await self.used_today())

    async def reserve(self, n: int = 1) -> bool:
        """Atomically claim n calls from today's wallet. On overshoot the claim
        is refunded and False returned - the caller must not make the call."""
        new_value = await self.store.bump_counter(self.day_key(), n)
        if new_value > self.max_per_day:
            await self.store.bump_counter(self.day_key(), -n)
            log.info("budget_exhausted", used=new_value - n, cap=self.max_per_day)
            return False
        return True
