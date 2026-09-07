"""One-way applications tracker: bot facts -> a Google Sheet the owner edits.

Column contract: the bot owns A..J (ID through Bot status) and rewrites them
freely on every sync; everything right of that (Stage, Round, Next step,
Notes) belongs to the human and is structurally untouchable - the port
simply has no operation that writes there. Rows with a blank ID cell are
human-added and are never matched or modified.

The sheet is a mirror, never a source: nothing here reads decisions back.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Protocol

import structlog

from jobscout.store import Store

log = structlog.get_logger()

BOT_HEADERS = [
    "ID",
    "Applied",
    "Company",
    "Role",
    "Link",
    "Lane",
    "Fit",
    "Salary",
    "CV",
    "Bot status",
]
HUMAN_HEADERS = ["Stage", "Round", "Next step", "Notes"]

STATUS_LABELS = {"closed": "CLOSED by employer"}


class SheetPort(Protocol):
    async def ensure_headers(self, headers: list[str]) -> None:
        """Write the header row if the sheet is empty or headers drifted."""
        ...

    async def read_ids(self) -> dict[str, int]:
        """Column-A value -> 1-based row number, for rows with a non-blank ID."""
        ...

    async def append_rows(self, rows: list[list[str]]) -> None: ...

    async def update_bot_cells(self, row: int, values: list[str]) -> None:
        """Overwrite ONLY the bot-owned columns (A..) of one row."""
        ...


def _bot_values(app: dict[str, Any]) -> list[str]:
    applied_at = app.get("applied_at")
    return [
        str(app["posting_id"]),
        applied_at.date().isoformat() if applied_at else "",
        app.get("company") or "",
        app.get("title") or "",
        app.get("url") or "",
        (app.get("lane") or "").upper(),
        str(app["fit_score"]) if app.get("fit_score") is not None else "",
        app.get("salary_raw") or "",
        app.get("cv_variant") or "",
        STATUS_LABELS.get(app.get("status"), app.get("status") or ""),
    ]


async def sync_applications(store: Store, sheet: SheetPort) -> dict[str, int]:
    """Reconcile every applied/closed posting into the sheet. Idempotent."""
    apps = await store.applications()
    await sheet.ensure_headers(BOT_HEADERS + HUMAN_HEADERS)
    existing = await sheet.read_ids()

    appended = 0
    updated = 0
    to_append: list[list[str]] = []
    for app in apps:
        values = _bot_values(app)
        row = existing.get(values[0])
        if row is None:
            to_append.append(values)
            appended += 1
        else:
            await sheet.update_bot_cells(row, values)
            updated += 1
    if to_append:
        await sheet.append_rows(to_append)

    log.info("sheet_synced", appended=appended, updated=updated)
    return {"appended": appended, "updated": updated}


class GspreadSheet:
    """Thin gspread adapter; every call runs in a thread (gspread is sync).

    Needs a Google service account key file and the spreadsheet shared with
    that service account's email (Editor).
    """

    def __init__(self, key_file: Path, spreadsheet_id: str, tab: str):
        self.key_file = key_file
        self.spreadsheet_id = spreadsheet_id
        self.tab = tab
        self._ws = None

    def _worksheet(self):
        if self._ws is None:
            import gspread

            client = gspread.service_account(filename=str(self.key_file))
            book = client.open_by_key(self.spreadsheet_id)
            try:
                self._ws = book.worksheet(self.tab)
            except gspread.WorksheetNotFound:
                self._ws = book.add_worksheet(self.tab, rows=200, cols=20)
        return self._ws

    async def ensure_headers(self, headers: list[str]) -> None:
        def work():
            ws = self._worksheet()
            current = ws.row_values(1)
            if current[: len(headers)] != headers:
                ws.update(
                    values=[headers],
                    range_name=f"A1:{chr(ord('A') + len(headers) - 1)}1",
                )

        await asyncio.to_thread(work)

    async def read_ids(self) -> dict[str, int]:
        def work():
            ws = self._worksheet()
            return {
                value: index
                for index, value in enumerate(ws.col_values(1), start=1)
                if index > 1 and value.strip()
            }

        return await asyncio.to_thread(work)

    async def append_rows(self, rows: list[list[str]]) -> None:
        def work():
            self._worksheet().append_rows(rows, value_input_option="USER_ENTERED")

        await asyncio.to_thread(work)

    async def update_bot_cells(self, row: int, values: list[str]) -> None:
        def work():
            last_col = chr(ord("A") + len(values) - 1)
            self._worksheet().update(values=[values], range_name=f"A{row}:{last_col}{row}")

        await asyncio.to_thread(work)
