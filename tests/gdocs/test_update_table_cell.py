"""
Unit tests for Google Docs update_table_cell tool.

get_table_cell_indices derives a cell's editable text range purely from the
cell's own structural start_index/end_index (Google's API guarantees these
tile without gaps, so end_index - 1 always lands on the last paragraph's
terminating newline). It deliberately does NOT inspect the cell's internal
paragraph/textRun structure, because that structure isn't a reliable signal:

- BL-XX (36/36 repro): a live, previously-edited cell's first textRun ended
  right at the visible text, one short of the cell's real end -- reading
  indices off that run stranded the old text's last character.
- A follow-up live test on a table freshly built by create_table_with_data
  found the opposite shape: a single run spanning the text *and* the
  trailing newline -- reading indices off that run tried to delete the
  newline itself, and the Docs API rejected the whole request.

Cell-boundary math sidesteps both shapes. The mock cells below deliberately
vary their internal paragraph/run structure (single run, run split
mid-content, run including the newline) to prove the code path used is
insensitive to all of them.
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
    """Build a table cell whose sole textRun spans the *entire* paragraph,
    i.e. its own endIndex happens to include the trailing newline -- the
    shape a freshly-inserted cell tends to have. ``text`` should include the
    trailing "\\n", e.g. "\\n" for an empty cell, "Hi\\n" for a cell
    containing "Hi".

    update_table_cell must get the same correct answer regardless of this
    internal shape, since it deliberately ignores it -- see
    _cell_run_excludes_newline for the other shape observed live.
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


def _cell_run_excludes_newline(start_index, end_index, text):
    """Build a table cell whose textRun ends one short of the cell's real
    end -- the shape observed live in the BL-XX bug report, where reading
    indices off the run (instead of the cell) stranded the last character.
    ``text`` is the visible text, without a trailing newline.
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
    # Cell (0,1) spans structural indices (4, 8); content_start/content_end
    # are derived purely from that cell boundary (5, 7), never from the
    # textRun's own endIndex -- deleting only (5, 6) would strand the "i"
    # (BL-XX bug), while deleting through index 7 would try to remove the
    # paragraph's trailing newline and get rejected by the Docs API.
    assert delete_req["startIndex"] == 5
    assert delete_req["endIndex"] == 7

    insert_req = requests[1]["insertText"]
    assert insert_req["location"]["index"] == 5
    assert insert_req["text"] == "New"


@pytest.mark.asyncio
async def test_update_table_cell_overwrite_when_run_excludes_newline():
    """Same cell boundaries as the "Hi" cell above, but with the textRun
    shaped the way BL-XX's live document actually had it (run ends one
    short of the cell's end). update_table_cell must compute the identical
    delete/insert range either way, since it never reads the run's indices.
    """
    doc_data = _make_doc()
    doc_data["body"]["content"][0]["table"]["tableRows"][0]["tableCells"][1] = (
        _cell_run_excludes_newline(4, 8, "Hi")
    )
    mock_service = _create_mock_service(doc_data)

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

    requests = mock_service.documents().batchUpdate.call_args[1]["body"]["requests"]
    delete_req = requests[0]["deleteContentRange"]["range"]
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
