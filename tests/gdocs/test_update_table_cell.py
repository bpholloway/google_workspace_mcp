"""
Unit tests for Google Docs update_table_cell tool.

The mock document fixture mirrors the real Docs API response shape confirmed
live (BL-XX off-by-one bug report, 36/36 reproduction): a cell's text-run
endIndex covers only the visible text and excludes the paragraph's trailing
newline, which occupies the index just past it. An empty cell's paragraph is
exactly one character wide (just the implicit trailing newline).
"""

import os
import sys
from unittest.mock import Mock

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from core.utils import UserInputError
from gdocs.docs_tools import update_table_cell


def _unwrap(tool):
    """Unwrap FunctionTool + decorators to the original async function."""
    fn = tool.fn if hasattr(tool, "fn") else tool
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


def _cell(start_index, end_index, text):
    """Build a table cell matching the real Docs API shape.

    ``text`` should include the trailing "\\n" the API always appends to a
    cell's sole paragraph -- e.g. "\\n" for an empty cell, "Hi\\n" for a cell
    containing "Hi". The paragraph spans (start_index+1, end_index), but the
    textRun element within it ends one short of that (end_index - 1): the
    textRun's own endIndex covers only the visible text, excluding the
    paragraph's trailing newline which occupies the last slot.
    """
    return {
        "startIndex": start_index,
        "endIndex": end_index,
        "content": [
            {
                "startIndex": start_index + 1,
                "endIndex": end_index,
                "paragraph": {
                    "elements": [
                        {
                            "startIndex": start_index + 1,
                            "endIndex": end_index - 1,
                            "textRun": {"content": text[:-1], "textStyle": {}},
                        }
                    ],
                    "paragraphStyle": {},
                },
            }
        ],
    }


def _make_doc(num_tables=1):
    """2x2 table: (0,0) empty, (0,1) contains 'Hi', (1,0)/(1,1) empty."""
    table_element = {
        "startIndex": 1,
        "endIndex": 13,
        "table": {
            "tableRows": [
                {
                    "tableCells": [
                        _cell(2, 4, "\n"),
                        _cell(4, 8, "Hi\n"),
                    ]
                },
                {
                    "tableCells": [
                        _cell(8, 10, "\n"),
                        _cell(10, 12, "\n"),
                    ]
                },
            ]
        },
    }
    content = [table_element] * num_tables
    return {"body": {"content": content}}


def _create_mock_service(doc_data):
    mock_service = Mock()
    mock_service.documents().get().execute = Mock(return_value=doc_data)
    mock_service.documents().batchUpdate().execute = Mock(return_value={})
    return mock_service


@pytest.mark.asyncio
async def test_update_table_cell_empty_cell_inserts():
    mock_service = _create_mock_service(_make_doc())

    result = await _unwrap(update_table_cell)(
        service=mock_service,
        user_google_email="user@example.com",
        document_id="doc_123",
        table_index=0,
        row=0,
        column=0,
        new_text="New",
    )

    assert "Successfully updated cell (0, 0)" in result
    assert "doc_123" in result

    call_args = mock_service.documents().batchUpdate.call_args
    requests = call_args[1]["body"]["requests"]
    assert len(requests) == 1
    assert requests[0]["insertText"]["location"]["index"] == 3
    assert requests[0]["insertText"]["text"] == "New"


@pytest.mark.asyncio
async def test_update_table_cell_overwrite_existing():
    mock_service = _create_mock_service(_make_doc())

    result = await _unwrap(update_table_cell)(
        service=mock_service,
        user_google_email="user@example.com",
        document_id="doc_123",
        table_index=0,
        row=0,
        column=1,
        new_text="New",
    )

    assert "Successfully updated cell (0, 1)" in result

    call_args = mock_service.documents().batchUpdate.call_args
    requests = call_args[1]["body"]["requests"]
    assert len(requests) == 2

    delete_req = requests[0]["deleteContentRange"]["range"]
    # Cell (0,1) contains "Hi" spanning indices (5, 7); that's already the
    # exclusive end of the visible text (index 7 is the paragraph's trailing
    # newline, one past the textRun), so the delete covers the full (5, 7)
    # range -- deleting only (5, 6) would strand the "i" (BL-XX bug).
    assert delete_req["startIndex"] == 5
    assert delete_req["endIndex"] == 7

    insert_req = requests[1]["insertText"]
    assert insert_req["location"]["index"] == 5
    assert insert_req["text"] == "New"


@pytest.mark.asyncio
async def test_update_table_cell_table_index_out_of_range():
    mock_service = _create_mock_service(_make_doc(num_tables=1))

    with pytest.raises(UserInputError, match="table_index 1 not found"):
        await _unwrap(update_table_cell)(
            service=mock_service,
            user_google_email="user@example.com",
            document_id="doc_123",
            table_index=1,
            row=0,
            column=0,
            new_text="New",
        )

    mock_service.documents().batchUpdate().execute.assert_not_called()


@pytest.mark.asyncio
async def test_update_table_cell_row_column_out_of_range():
    mock_service = _create_mock_service(_make_doc())

    with pytest.raises(UserInputError, match="out of range"):
        await _unwrap(update_table_cell)(
            service=mock_service,
            user_google_email="user@example.com",
            document_id="doc_123",
            table_index=0,
            row=5,
            column=0,
            new_text="New",
        )

    mock_service.documents().batchUpdate().execute.assert_not_called()
