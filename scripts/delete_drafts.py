"""Delete all stored draft notes (dev utility).

Used after the 2026-09-07 decision to turn drafting off: notes are written
in chat instead, and the auto-generated leftovers only bloat digest cards.
"""

import asyncio

import asyncpg

from jobscout.config import Settings


async def main() -> None:
    conn = await asyncpg.connect(dsn=Settings().dsn)
    try:
        result = await conn.execute("DELETE FROM drafts")
        print(f"deleted: {result}")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
