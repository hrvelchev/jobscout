"""One-time styling for the applications tracker sheet (run after first sync).

Gives the tab the classic tracker look: dark header, frozen top row, Stage
and Round dropdowns, strong colored chips on Stage, and whole-row tinting by
stage (red = rejected, yellow = in progress, green = offer). Pure styling -
the daily sync never touches formatting, so hand-tweaks survive.
"""

import sys

import gspread

sys.path.insert(0, "src")
from jobscout.config import Settings  # noqa: E402

STAGES = ["Screening", "Interviewing", "Offer", "Rejected", "Ghosted"]
ROUNDS = ["1st Round", "2nd Round", "3rd Round", "Final Round", "NA"]

GREEN_DARK = {"red": 0.22, "green": 0.46, "blue": 0.18}
GREEN_LIGHT = {"red": 0.85, "green": 0.92, "blue": 0.83}
YELLOW = {"red": 0.95, "green": 0.76, "blue": 0.20}
YELLOW_LIGHT = {"red": 1.0, "green": 0.95, "blue": 0.80}
RED = {"red": 0.80, "green": 0.0, "blue": 0.0}
RED_LIGHT = {"red": 0.96, "green": 0.80, "blue": 0.80}
GRAY = {"red": 0.60, "green": 0.60, "blue": 0.60}
GRAY_LIGHT = {"red": 0.94, "green": 0.94, "blue": 0.94}
ORANGE_LIGHT = {"red": 0.99, "green": 0.90, "blue": 0.80}
WHITE = {"red": 1.0, "green": 1.0, "blue": 1.0}

WIDTHS = [40, 90, 160, 260, 180, 60, 50, 140, 120, 140, 120, 110, 140, 320]

STAGE_COL, ROUND_COL, BOT_STATUS_COL = 10, 11, 9  # 0-based: K, L, J


def grid(sheet_id, rows, c1, c2, r1=1):
    return {
        "sheetId": sheet_id,
        "startRowIndex": r1,
        "endRowIndex": rows,
        "startColumnIndex": c1,
        "endColumnIndex": c2,
    }


def chip_rule(sheet_id, rows, col, text, bg, fg=WHITE, bold=True):
    return {
        "addConditionalFormatRule": {
            "rule": {
                "ranges": [grid(sheet_id, rows, col, col + 1)],
                "booleanRule": {
                    "condition": {"type": "TEXT_EQ", "values": [{"userEnteredValue": text}]},
                    "format": {
                        "backgroundColor": bg,
                        "textFormat": {"foregroundColor": fg, "bold": bold},
                    },
                },
            },
            "index": 0,
        }
    }


def row_tint_rule(sheet_id, rows, formula, bg):
    return {
        "addConditionalFormatRule": {
            "rule": {
                "ranges": [grid(sheet_id, rows, 0, 14)],
                "booleanRule": {
                    "condition": {
                        "type": "CUSTOM_FORMULA",
                        "values": [{"userEnteredValue": formula}],
                    },
                    "format": {"backgroundColor": bg},
                },
            },
            "index": 0,
        }
    }


def main() -> None:
    settings = Settings()
    client = gspread.service_account(filename=str(settings.google_service_account_file))
    book = client.open_by_key(settings.gsheet_id)
    ws = book.worksheet(settings.gsheet_tab)
    sheet_id = ws.id
    rows = ws.row_count

    requests = [
        # frozen, dark, bold white header
        {
            "updateSheetProperties": {
                "properties": {"sheetId": sheet_id, "gridProperties": {"frozenRowCount": 1}},
                "fields": "gridProperties.frozenRowCount",
            }
        },
        {
            "repeatCell": {
                "range": grid(sheet_id, 1, 0, 14, r1=0),
                "cell": {
                    "userEnteredFormat": {
                        "backgroundColor": GREEN_DARK,
                        "textFormat": {"foregroundColor": WHITE, "bold": True},
                        "horizontalAlignment": "CENTER",
                    }
                },
                "fields": "userEnteredFormat(backgroundColor,textFormat,horizontalAlignment)",
            }
        },
        # dropdowns
        {
            "setDataValidation": {
                "range": grid(sheet_id, rows, STAGE_COL, STAGE_COL + 1),
                "rule": {
                    "condition": {
                        "type": "ONE_OF_LIST",
                        "values": [{"userEnteredValue": s} for s in STAGES],
                    },
                    "showCustomUi": True,
                    "strict": False,
                },
            }
        },
        {
            "setDataValidation": {
                "range": grid(sheet_id, rows, ROUND_COL, ROUND_COL + 1),
                "rule": {
                    "condition": {
                        "type": "ONE_OF_LIST",
                        "values": [{"userEnteredValue": r} for r in ROUNDS],
                    },
                    "showCustomUi": True,
                    "strict": False,
                },
            }
        },
    ]

    # column widths
    for index, width in enumerate(WIDTHS):
        requests.append(
            {
                "updateDimensionProperties": {
                    "range": {
                        "sheetId": sheet_id,
                        "dimension": "COLUMNS",
                        "startIndex": index,
                        "endIndex": index + 1,
                    },
                    "properties": {"pixelSize": width},
                    "fields": "pixelSize",
                }
            }
        )

    # row tinting by stage (rule order = precedence; chips added after,
    # at index 0, so they win over tints on their own cell)
    requests += [
        # one rule per value: OR() breaks on non-English spreadsheet locales
        # (argument separator differs), plain comparisons never do
        row_tint_rule(sheet_id, rows, '=$K2="Rejected"', RED_LIGHT),
        row_tint_rule(sheet_id, rows, '=$K2="Ghosted"', GRAY_LIGHT),
        row_tint_rule(sheet_id, rows, '=$K2="Interviewing"', YELLOW_LIGHT),
        row_tint_rule(sheet_id, rows, '=$K2="Screening"', YELLOW_LIGHT),
        row_tint_rule(sheet_id, rows, '=$K2="Offer"', GREEN_LIGHT),
        row_tint_rule(sheet_id, rows, '=$J2="CLOSED by employer"', GRAY_LIGHT),
    ]

    # strong chips on Stage + muted chip on closed bot status + round tint
    requests += [
        chip_rule(sheet_id, rows, STAGE_COL, "Rejected", RED),
        chip_rule(sheet_id, rows, STAGE_COL, "Offer", GREEN_DARK),
        chip_rule(
            sheet_id, rows, STAGE_COL, "Interviewing", YELLOW, fg={"red": 0, "green": 0, "blue": 0}
        ),
        chip_rule(
            sheet_id,
            rows,
            STAGE_COL,
            "Screening",
            YELLOW_LIGHT,
            fg={"red": 0, "green": 0, "blue": 0},
            bold=False,
        ),
        chip_rule(sheet_id, rows, STAGE_COL, "Ghosted", GRAY),
        chip_rule(
            sheet_id,
            rows,
            BOT_STATUS_COL,
            "CLOSED by employer",
            GRAY_LIGHT,
            fg={"red": 0.3, "green": 0.3, "blue": 0.3},
            bold=False,
        ),
        {
            "addConditionalFormatRule": {
                "rule": {
                    "ranges": [grid(sheet_id, rows, ROUND_COL, ROUND_COL + 1)],
                    "booleanRule": {
                        "condition": {"type": "NOT_BLANK"},
                        "format": {"backgroundColor": ORANGE_LIGHT},
                    },
                },
                "index": 0,
            }
        },
    ]

    book.batch_update({"requests": requests})
    print(f"styled: tab '{settings.gsheet_tab}', {len(requests)} formatting requests applied")


if __name__ == "__main__":
    main()
