"""Read-only state overview (dev utility): status counts + delivery tracking."""

import asyncio

import asyncpg

from jobscout.config import Settings


async def main() -> None:
    conn = await asyncpg.connect(dsn=Settings().dsn)
    try:
        print("postings by status:")
        for r in await conn.fetch(
            "SELECT status, count(*) FROM postings GROUP BY status ORDER BY status"
        ):
            print(f"  {r['status']}: {r['count']}")
        print("scores:", await conn.fetchval("SELECT count(*) FROM scores"))
        raw = await conn.fetchval(
            "SELECT value FROM app_state WHERE key = 'digest_delivered_ids'"
        )
        print("delivered_ids:", raw)
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
