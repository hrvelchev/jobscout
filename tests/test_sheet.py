"""Applications-tracker sync over an in-memory sheet fake.

The human-column guarantee is structural (SheetPort has no operation that
writes past the bot columns), so tests focus on the reconcile contract:
append once, update in place, never duplicate, closed-status propagation.
"""

from test_models import make_posting

from jobscout.models import ScoreResult
from jobscout.sheet import BOT_HEADERS, HUMAN_HEADERS, sync_applications


class FakeSheet:
    def __init__(self):
        self.headers: list[str] | None = None
        self.rows: list[list[str]] = []  # data rows; sheet row = index + 2
        self.bot_updates: list[tuple[int, list[str]]] = []

    async def ensure_headers(self, headers: list[str]) -> None:
        self.headers = headers

    async def read_ids(self) -> dict[str, int]:
        return {row[0]: i + 2 for i, row in enumerate(self.rows) if row[0].strip()}

    async def append_rows(self, rows: list[list[str]]) -> None:
        self.rows.extend(rows)

    async def update_bot_cells(self, row: int, values: list[str]) -> None:
        self.bot_updates.append((row, values))
        self.rows[row - 2] = values


def score(fit=80, lane="ai") -> ScoreResult:
    return ScoreResult(
        fit_score=fit,
        stack_match=8,
        seniority_gap=0,
        degree_gate="none",
        lane=lane,
        red_flags=[],
        cv_keywords=[],
        reason="ok",
    )


async def seed_application(store, external_id: str, company: str, fit: int = 80) -> int:
    pid = await store.insert_posting(
        make_posting(external_id=external_id, company=company, salary_raw="3000 - 5000 лв."),
        None,
    )
    await store.save_score(pid, score(fit))
    await store.set_status(pid, "applied", note="test")
    return pid


async def test_sync_appends_applications_with_bot_values(store):
    pid = await seed_application(store, "a1", "Fibank", fit=82)
    sheet = FakeSheet()
    result = await sync_applications(store, sheet, {"ai": "cv_ai.pdf"})
    assert result == {"appended": 1, "updated": 0}
    assert sheet.headers == BOT_HEADERS + HUMAN_HEADERS
    row = sheet.rows[0]
    assert row[0] == str(pid)
    assert row[2] == "Fibank"
    assert row[5] == "AI" and row[6] == "82"
    assert row[7] == "3000 - 5000 лв."
    assert row[8] == "cv_ai.pdf"  # no draft row: CV derived from the lane map
    assert row[9] == "applied"
    assert row[1]  # applied date filled from the status event


async def test_sync_is_idempotent_and_updates_in_place(store):
    await seed_application(store, "a1", "Fibank")
    sheet = FakeSheet()
    await sync_applications(store, sheet)
    result = await sync_applications(store, sheet)
    assert result == {"appended": 0, "updated": 1}
    assert len(sheet.rows) == 1  # no duplicate row, ever


async def test_closed_by_watch_lands_in_the_tracker(store):
    pid = await seed_application(store, "a1", "Fibank")
    sheet = FakeSheet()
    await sync_applications(store, sheet)
    await store.set_status(pid, "closed", note="watch")
    await sync_applications(store, sheet)
    assert sheet.rows[0][9] == "CLOSED by employer"


async def test_skipped_and_undecided_postings_never_reach_the_sheet(store):
    await seed_application(store, "a1", "Fibank")
    other = await store.insert_posting(make_posting(external_id="s1", company="Skippy"), None)
    await store.save_score(other, score())
    await store.set_status(other, "skipped", note="test")
    sheet = FakeSheet()
    await sync_applications(store, sheet)
    assert len(sheet.rows) == 1 and sheet.rows[0][2] == "Fibank"
