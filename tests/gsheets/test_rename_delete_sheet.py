"""
Unit tests for Google Sheets rename_sheet and delete_sheet tools.

Tests happy paths, sheet-not-found resolution, and the last-sheet-delete
refusal guard.
"""

import os
import sys
from unittest.mock import Mock

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from core.utils import UserInputError
from gsheets.sheets_tools import rename_sheet, delete_sheet


def _unwrap(tool):
    """Unwrap FunctionTool + decorators to the original async function."""
    fn = tool.fn if hasattr(tool, "fn") else tool
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


def _create_mock_service(sheets):
    mock_service = Mock()
    mock_service.spreadsheets().get().execute = Mock(
        return_value={"sheets": sheets}
    )
    mock_service.spreadsheets().batchUpdate().execute = Mock(return_value={})
    return mock_service


TWO_SHEETS = [
    {"properties": {"sheetId": 0, "title": "Sheet1"}},
    {"properties": {"sheetId": 111, "title": "Budget"}},
]

ONE_SHEET = [
    {"properties": {"sheetId": 0, "title": "Sheet1"}},
]


@pytest.mark.asyncio
async def test_rename_sheet_success():
    mock_service = _create_mock_service(TWO_SHEETS)

    result = await _unwrap(rename_sheet)(
        service=mock_service,
        user_google_email="user@example.com",
        spreadsheet_id="ss_123",
        sheet_name="Budget",
        new_name="Q1 Budget",
    )

    assert "Successfully renamed sheet 'Budget' to 'Q1 Budget'" in result
    assert "ss_123" in result

    call_args = mock_service.spreadsheets().batchUpdate.call_args
    request_body = call_args[1]["body"]
    requests = request_body["requests"]
    assert len(requests) == 1

    update_req = requests[0]["updateSheetProperties"]
    assert update_req["properties"]["sheetId"] == 111
    assert update_req["properties"]["title"] == "Q1 Budget"
    assert update_req["fields"] == "title"


@pytest.mark.asyncio
async def test_rename_sheet_not_found_raises():
    mock_service = _create_mock_service(TWO_SHEETS)

    with pytest.raises(UserInputError, match="not found"):
        await _unwrap(rename_sheet)(
            service=mock_service,
            user_google_email="user@example.com",
            spreadsheet_id="ss_123",
            sheet_name="Nonexistent",
            new_name="Whatever",
        )

    # No batchUpdate should have been attempted once resolution fails.
    mock_service.spreadsheets().batchUpdate().execute.assert_not_called()


@pytest.mark.asyncio
async def test_delete_sheet_success():
    mock_service = _create_mock_service(TWO_SHEETS)

    result = await _unwrap(delete_sheet)(
        service=mock_service,
        user_google_email="user@example.com",
        spreadsheet_id="ss_123",
        sheet_name="Budget",
    )

    assert "Successfully deleted sheet 'Budget'" in result
    assert "ss_123" in result

    call_args = mock_service.spreadsheets().batchUpdate.call_args
    request_body = call_args[1]["body"]
    requests = request_body["requests"]
    assert len(requests) == 1
    assert requests[0]["deleteSheet"]["sheetId"] == 111


@pytest.mark.asyncio
async def test_delete_sheet_not_found_raises():
    mock_service = _create_mock_service(TWO_SHEETS)

    with pytest.raises(UserInputError, match="not found"):
        await _unwrap(delete_sheet)(
            service=mock_service,
            user_google_email="user@example.com",
            spreadsheet_id="ss_123",
            sheet_name="Nonexistent",
        )

    mock_service.spreadsheets().batchUpdate().execute.assert_not_called()


@pytest.mark.asyncio
async def test_delete_sheet_refuses_last_sheet():
    mock_service = _create_mock_service(ONE_SHEET)

    with pytest.raises(UserInputError, match="Cannot delete the only sheet"):
        await _unwrap(delete_sheet)(
            service=mock_service,
            user_google_email="user@example.com",
            spreadsheet_id="ss_123",
            sheet_name="Sheet1",
        )

    mock_service.spreadsheets().batchUpdate().execute.assert_not_called()
