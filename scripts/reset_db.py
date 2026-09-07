"""Wipe all jobscout tables for a clean rescan (dev utility).

Everything in this database is a regenerable scrape cache + pipeline
artifacts; statuses the owner set by hand are lost, so run deliberately.
"""

import asyncio

import asyncpg

from jobscout.config import Settings

TABLES = ("events", "drafts", "scores", "scan_log", "usage_log", "app_state", "postings")


async def main() -> None:
    conn = await asyncpg.connect(dsn=Settings().dsn)
    try:
        await conn.execute(f"TRUNCATE {', '.join(TABLES)} RESTART IDENTITY CASCADE")
        remaining = await conn.fetchval("SELECT count(*) FROM postings")
        print(f"wiped {len(TABLES)} tables; postings now: {remaining}")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
