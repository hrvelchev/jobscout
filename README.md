# jobscout

[![ci](https://github.com/hrvelchev/jobscout/actions/workflows/ci.yml/badge.svg)](https://github.com/hrvelchev/jobscout/actions/workflows/ci.yml)

A daily job-hunting agent that works *for* you and never *as* you: it fetches
postings, dedupes them semantically, scores each one against your profile
with an LLM, and delivers a ranked morning Telegram digest - each card with a
draft application note and the CV variant to attach. **You read, you edit,
you apply. The bot never applies for you.**

Built as a working tool first and a demonstration second: LangGraph where
branching is genuinely load-bearing, Postgres + pgvector for semantic dedupe,
Docker for the runtime, CI running the full suite - 79 offline tests with the
network disabled at socket level, plus 9 integration tests against real
pgvector.

## How it works

```
                         07:45 Europe/Sofia - scout
  +--------------------+      +--------------------------+
  | dev.bg categories  |----->|                          |
  | (HTML, politely)   |      |   exact dedupe           |   every posting
  +--------------------+      |   (source, external_id)  |   gets exactly one
  +--------------------+      |         |                |   audited verdict
  | Greenhouse boards  |----->|   semantic dedupe        |   in scan_log:
  | (public JSON API)  |      |   pgvector cosine >= .90 |   dup_exact
  +--------------------+      |   AND same company       |   dup_semantic
                              |         |                |   excluded
                              |   free prefilter         |   no_lane_kw
                              |   exclude + keywords     |   over_run_cap
                              +-----------|--------------+   over_daily_cap
                                          v                  score_failed
                     per survivor: LangGraph pipeline        drafted ...
             +--------------------------------------------+
             |  reserve --> score --> gate --> draft --> sanitize
             |     |          |        |         |          |
             |   empty      bad     below     wallet     rule broken?
             |   wallet?    JSON?   threshold? empty?    retry ONCE with
             |   END,       retry   END       END        feedback, then
             |   0 calls    once    (no draft (scored,   accept cleaned
             |              then    is paid   no draft)
             |              END     for)
             +--------------------------------------------+
                                          v
                         08:30 - digest (deterministic ranking:
                fit + freshness + salary-posted + lane weight
                       + watched-company - staleness)
             +--------------------------------------------+
             |  Telegram card: score, why, red flags,     |
             |  CV keywords, which CV file, draft note    |
             |  [Applied] [Skip] [Snooze 3d] [Details]    |
             +--------------------------------------------+
                                          v
              button presses -> status + append-only event log
              "Applied" warns if you applied to the same
              company in the last 90 days
                                          v
              every 2 days - watch: applied postings re-checked;
              a posting that died gets you an alert the same day
```

## Design positions, stated up front

- **No auto-applying.** Application quality beats volume, and a bot-submitted
  application is a worse application. Drafts are drafts.
- **No LinkedIn automation, no scraping of bot-hostile boards.** The Monday
  digest reminds you to check those manually instead.
- **Costs are capped in code, not by discipline.** The daily LLM wallet is
  reserved *before* every call (a crash over-counts, never under-counts);
  score and draft share one wallet; an empty wallet ends the pipeline before
  any request leaves the machine.
- **Every kill is audited.** Duplicate, excluded, over-budget, failed -
  nothing is silently swallowed, and an empty digest says "nothing today"
  explicitly, because silence must never look like a malfunction.
- **Markup drift is loud.** If dev.bg redesigns and real HTML parses to zero
  postings, that is a degradation alert, not a quiet day.

## Quickstart

```bash
git clone https://github.com/hrvelchev/jobscout && cd jobscout
cp .env.example .env                     # Anthropic key, Telegram token, DB creds
for f in config/*.example.*; do cp "$f" "${f/.example/}"; done
# edit config/profile.md - postings are scored against THIS
docker compose up -d db
pip install -e .[dev]
python -m jobscout.main
```

Telegram commands: `/scout` (run now), `/digest` (send now), `/cost`
(last 30 days of LLM spend).

## Tests

```bash
pytest -m "not pg"       # 79 offline: stubbed LLM, in-memory store, sockets blocked
docker compose up -d db
pytest -m pg             # 9 integration: real Postgres + pgvector
```

The graph tests assert *paid-call counts*, not just outcomes: an empty wallet
makes zero API calls, a below-threshold score never buys a draft, and the
draft retry provably carries corrective feedback in the recorded request.

## Stack

Python 3.13 · LangGraph · Postgres 17 + pgvector (HNSW) · fastembed
(bge-small, local ONNX - no embedding API) · asyncpg · python-telegram-bot ·
APScheduler · httpx + selectolax · pydantic-settings · structlog · Docker
Compose · GitHub Actions · pytest + pytest-socket

## License

MIT
