"""Read-only audit of the latest scout run: every verdict with title/company,
grouped, so a human can double-check the automated filtering."""

import asyncio
import sys

import asyncpg

from jobscout.config import Settings


async def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    conn = await asyncpg.connect(dsn=Settings().dsn)
    try:
        rows = await conn.fetch(
            "SELECT source, external_id, verdict, company, title FROM scan_log ORDER BY verdict, company"
        )
        by_verdict: dict[str, list] = {}
        for r in rows:
            by_verdict.setdefault(r["verdict"], []).append(r)
        for verdict, group in sorted(by_verdict.items()):
            print(f"\n=== {verdict} ({len(group)}) ===")
            for r in group:
                print(f"  [{r['source']}] {r['company']} - {r['title']}")
        scores = await conn.fetch(
            """SELECT p.company, p.title, s.fit_score, s.lane
               FROM scores s JOIN postings p USING (posting_id)
               ORDER BY s.fit_score DESC"""
        )
        print(f"\n=== scored ({len(scores)}) ===")
        for r in scores:
            print(f"  {r['fit_score']:3d} {r['lane']:6s} {r['company']} - {r['title']}")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
