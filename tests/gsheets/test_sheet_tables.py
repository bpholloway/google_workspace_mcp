"""
Unit tests for Google Sheets create_sheet_table and append_table_rows tools.

Covers two bugs found in live testing on 2026-09-03:
  - append_table_rows returned a Google 500 on header-only (zero body row)
    tables because it relied on appendCells resolving "the last row with
    data", which is ambiguous when only the header row has data.
  - create_sheet_table wrote header text at the wrong grid position for
    non-A1 anchors.
"""

import os
import sys
from unittest.mock import Mock

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from core.utils import UserInputError
from gsheets.sheets_helpers import _parse_a1_part
from gsheets.sheets_tools import append_table_rows, create_sheet_table, delete_sheet_table


def _unwrap(tool):
    """Unwrap FunctionTool + decorators to the original async function."""
    fn = tool.fn if hasattr(tool, "fn") else tool
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


def _create_mock_service(get_return):
    mock_service = Mock()
    mock_service.spreadsheets().get().execute = Mock(return_value=get_return)
    mock_service.spreadsheets().batchUpdate().execute = Mock(
        return_value={"replies": [{"addTable": {"table": {"tableId": "new_table_1"}}}, {}]}
    )
    return mock_service


class TestParseA1Part:
    """Row and column indices must be parsed independently and both 0-based."""

    @pytest.mark.parametrize(
        "anchor, expected_col, expected_row",
        [
            ("A1", 0, 0),
            ("A10", 0, 9),
            ("C3", 2, 2),
            ("AA5", 26, 4),
            ("Z100", 25, 99),
        ],
    )
    def test_parse(self, anchor, expected_col, expected_row):
        col_idx, row_idx = _parse_a1_part(anchor)
        assert col_idx == expected_col
        assert row_idx == expected_row


class TestCreateSheetTable:
    SHEETS = [{"properties": {"sheetId": 999, "title": "Close"}}]

    @pytest.mark.asyncio
    async def test_header_lands_at_anchor_for_nonzero_column_offset(self):
        mock_service = _create_mock_service({"sheets": self.SHEETS})

        await _unwrap(create_sheet_table)(
            service=mock_service,
            user_google_email="user@example.com",
            spreadsheet_id="ss_123",
            table_name="CloseTest2",
            column_names=["ID", "Timestamp", "Session"],
            sheet_name="Close",
            anchor_cell="C3",
            num_body_rows=0,
        )

        # addTable and the header updateCells must be two separate
        # batchUpdate calls — combining them in one batch 500s server-side
        # (confirmed via live retest), even though each is valid alone.
        # (The mock's own setup call shows up as a no-arg call() too, so
        # filter to calls that actually carry a request body.)
        calls = [
            c for c in mock_service.spreadsheets().batchUpdate.call_args_list
            if c.kwargs.get("body")
        ]
        assert len(calls) == 2

        add_table_body = calls[0][1]["body"]
        assert list(add_table_body["requests"][0].keys()) == ["addTable"]
        add_table = add_table_body["requests"][0]["addTable"]["table"]
        # columnIndex must be table-relative (0-based), independent of anchor.
        assert [cp["columnIndex"] for cp in add_table["columnProperties"]] == [0, 1, 2]
        assert add_table["range"]["startColumnIndex"] == 2
        assert add_table["range"]["startRowIndex"] == 2

        update_cells_body = calls[1][1]["body"]
        assert list(update_cells_body["requests"][0].keys()) == ["updateCells"]
        update_cells = update_cells_body["requests"][0]["updateCells"]
        # The header write must start at the same row/column as the table range.
        assert update_cells["start"] == {
            "sheetId": 999,
            "rowIndex": 2,
            "columnIndex": 2,
        }
        header_names = [
            cell["userEnteredValue"]["stringValue"]
            for cell in update_cells["rows"][0]["values"]
        ]
        assert header_names == ["ID", "Timestamp", "Session"]

    @pytest.mark.asyncio
    async def test_header_lands_at_anchor_for_row_offset(self):
        mock_service = _create_mock_service({"sheets": self.SHEETS})

        await _unwrap(create_sheet_table)(
            service=mock_service,
            user_google_email="user@example.com",
            spreadsheet_id="ss_123",
            table_name="CloseTest2",
            column_names=["ID", "Timestamp"],
            sheet_name="Close",
            anchor_cell="A10",
            num_body_rows=0,
        )

        calls = [
            c for c in mock_service.spreadsheets().batchUpdate.call_args_list
            if c.kwargs.get("body")
        ]
        assert len(calls) == 2
        add_table = calls[0][1]["body"]["requests"][0]["addTable"]["table"]
        update_cells = calls[1][1]["body"]["requests"][0]["updateCells"]

        # Row offset must never leak into the column position.
        assert add_table["range"]["startColumnIndex"] == 0
        assert add_table["range"]["startRowIndex"] == 9
        assert update_cells["start"]["columnIndex"] == 0
        assert update_cells["start"]["rowIndex"] == 9


class TestAppendTableRows:
    @pytest.mark.asyncio
    async def test_header_only_table_uses_update_table_not_append_cells(self):
        """Header-only (zero body row) tables must not go through appendCells."""
        spreadsheet_meta = {
            "sheets": [
                {
                    "properties": {"sheetId": 5},
                    "tables": [
                        {
                            "tableId": "t1",
                            "range": {
                                "sheetId": 5,
                                "startRowIndex": 0,
                                "endRowIndex": 1,
                                "startColumnIndex": 0,
                                "endColumnIndex": 3,
                            },
                        }
                    ],
                }
            ]
        }
        mock_service = _create_mock_service(spreadsheet_meta)

        result = await _unwrap(append_table_rows)(
            service=mock_service,
            user_google_email="user@example.com",
            spreadsheet_id="ss_123",
            table_id="t1",
            values=[["a", "b", "c"]],
        )

        requests = mock_service.spreadsheets().batchUpdate.call_args[1]["body"]["requests"]
        assert not any("appendCells" in r for r in requests)

        update_table = next(r["updateTable"] for r in requests if "updateTable" in r)
        assert update_table["table"]["tableId"] == "t1"
        assert update_table["table"]["range"]["endRowIndex"] == 2
        assert update_table["fields"] == "range"

        update_cells = next(r["updateCells"] for r in requests if "updateCells" in r)
        assert update_cells["start"] == {"sheetId": 5, "rowIndex": 1, "columnIndex": 0}

        assert "endRowIndex: 2" in result

    @pytest.mark.asyncio
    async def test_append_extends_past_existing_body_rows(self):
        """A table that already has body rows still extends from its current end."""
        spreadsheet_meta = {
            "sheets": [
                {
                    "properties": {"sheetId": 5},
                    "tables": [
                        {
                            "tableId": "t2",
                            "range": {
                                "sheetId": 5,
                                "startRowIndex": 0,
                                "endRowIndex": 4,
                                "startColumnIndex": 0,
                                "endColumnIndex": 3,
                            },
                        }
                    ],
                }
            ]
        }
        mock_service = _create_mock_service(spreadsheet_meta)

        await _unwrap(append_table_rows)(
            service=mock_service,
            user_google_email="user@example.com",
            spreadsheet_id="ss_123",
            table_id="t2",
            values=[["a", "b", "c"], ["d", "e", "f"]],
        )

        requests = mock_service.spreadsheets().batchUpdate.call_args[1]["body"]["requests"]
        update_table = next(r["updateTable"] for r in requests if "updateTable" in r)
        update_cells = next(r["updateCells"] for r in requests if "updateCells" in r)

        assert update_table["table"]["range"]["endRowIndex"] == 6
        assert update_cells["start"]["rowIndex"] == 4
        assert len(update_cells["rows"]) == 2

    @pytest.mark.asyncio
    async def test_table_not_found_raises(self):
        mock_service = _create_mock_service({"sheets": []})

        with pytest.raises(UserInputError, match="not found"):
            await _unwrap(append_table_rows)(
                service=mock_service,
                user_google_email="user@example.com",
                spreadsheet_id="ss_123",
                table_id="missing",
                values=[["a"]],
            )

        mock_service.spreadsheets().batchUpdate().execute.assert_not_called()


class TestDeleteSheetTable:
    @pytest.mark.asyncio
    async def test_delete_success(self):
        mock_service = _create_mock_service({"sheets": []})

        result = await _unwrap(delete_sheet_table)(
            service=mock_service,
            user_google_email="user@example.com",
            spreadsheet_id="ss_123",
            table_id="t1",
        )

        assert "Successfully deleted table 't1'" in result
        call_args = mock_service.spreadsheets().batchUpdate.call_args
        requests = call_args[1]["body"]["requests"]
        assert requests == [{"deleteTable": {"tableId": "t1"}}]
