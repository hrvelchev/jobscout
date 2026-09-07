"""Re-arm delivered-but-undecided postings for digest delivery (dev utility).

Use case: the Telegram chat was cleared, so delivered cards are gone from
view. Delivery is guarded twice - posting status AND the app_state
digest_delivered_ids list ("a card is sent once, ever") - so both must be
reset. Applied/skipped/snoozed/closed postings are untouched.
"""

import asyncio

import asyncpg

from jobscout.config import Settings


async def main() -> None:
    conn = await asyncpg.connect(dsn=Settings().dsn)
    try:
        result = await conn.execute(
            "UPDATE postings SET status = 'scored' WHERE status = 'digested'"
        )
        print(f"re-armed for digest: {result}")
        cleared = await conn.execute("DELETE FROM app_state WHERE key = 'digest_delivered_ids'")
        print(f"delivered-ids list cleared: {cleared}")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
