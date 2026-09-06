# jobscout

A daily job-hunting agent that works *for* you and never *as* you: it fetches
postings, dedupes them semantically, scores each one against your profile
with an LLM, and delivers a ranked morning Telegram digest — each card with a
draft application note and the CV variant to attach. **You read, you edit,
you apply. The bot never applies for you.**

Built as a working tool first and a demonstration second: LangGraph where
branching is genuinely load-bearing, Postgres + pgvector for semantic dedupe,
Docker for the runtime, and an offline test suite where the network is
blocked at the socket level.

## How it works

```
07:45  fetch (dev.bg categories + Greenhouse board watchlist)
       -> exact dedupe (source, external_id)
       -> semantic dedupe (pgvector cosine >= 0.90 AND same company)
       -> free prefilter (exclude patterns, required keywords) - audited verdicts
       -> per-survivor LangGraph pipeline:
            reserve budget -> LLM score -> gate -> LLM draft -> sanitize
08:30  ranked digest to Telegram: top-N cards with
       [Applied] [Skip] [Snooze 3d] [Details] buttons
/2d    closed-posting watch: applied postings re-checked; alerts if one dies
```

Design positions, stated up front:

- **No auto-applying.** Application quality beats application volume, and a
  bot-submitted application is a worse application. Drafts are drafts.
- **No LinkedIn automation, no scraping of bot-hostile boards.** The digest
  reminds you to check those manually instead.
- **Costs are capped in code, not by discipline**: the daily LLM budget is
  reserved *before* every call; when the wallet is empty the pipeline says so
  in the audit log instead of spending.
- **Every kill is audited.** Each fetched posting gets a recorded verdict
  (duplicate, excluded, below threshold, over budget...) - the pipeline never
  silently swallows anything.

## Quickstart

```bash
git clone https://github.com/hrvelchev/jobscout && cd jobscout
cp .env.example .env                       # fill in Anthropic + Telegram + DB
for f in config/*.example.*; do cp "$f" "${f/.example/}"; done
# edit config/profile.md - this is what postings are scored against
docker compose up -d db
pip install -e .[dev]
python -m jobscout.main
```

## Tests

```bash
pytest -m "not pg"      # offline suite: stubbed LLM, fake store, sockets disabled
docker compose up -d db
pytest -m pg            # integration: real Postgres + pgvector
```

## License

MIT
