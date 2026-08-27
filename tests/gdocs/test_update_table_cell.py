"""
Unit tests for Google Docs update_table_cell tool.

The mock document fixture mirrors the real Docs API response shape verified
live against a throwaway test document (see the deploy notes for
commit history): each table cell's content paragraph carries its own
startIndex/endIndex on the textRun, and an empty cell's paragraph is exactly
one character wide (just the implicit trailing newline).
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
    containing "Hi". The paragraph/textRun span is (start_index+1, end_index),
    matching the offset-by-one observed in the live verification.
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
                            "endIndex": end_index,
                            "textRun": {"content": text, "textStyle": {}},
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
    # Cell (0,1) content spans (5, 8); the delete must stop one short of the
    # paragraph's own end (the trailing newline can't be deleted -- verified
    # live: deleting the full range 400s with "Cannot delete the requested range").
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
