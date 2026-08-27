"""
Regression tests for TableOperationManager.create_and_populate_table.

Covers BL-79: with includeTabsContent=True, the Docs API nests content
under tabs[].documentTab.body -- including for the single default tab of
an untabbed document -- and leaves the top-level "body" field empty. The
manager's post-creation re-fetch must fall back to the first document tab
when no explicit tab_id was requested, or it reports a false-negative
"Could not find table after creation" for a table that was actually
created.
"""

import os
import sys
from unittest.mock import Mock

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from gdocs.managers.table_operation_manager import TableOperationManager


def _cell(start_index, end_index):
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
                            "textRun": {"content": "\n", "textStyle": {}},
                        }
                    ],
                    "paragraphStyle": {},
                },
            }
        ],
    }


def _table_element(start_index=1, end_index=13):
    return {
        "startIndex": start_index,
        "endIndex": end_index,
        "table": {
            "tableRows": [
                {"tableCells": [_cell(2, 4), _cell(4, 6)]},
                {"tableCells": [_cell(8, 10), _cell(10, 12)]},
            ]
        },
    }


def _make_mock_service(get_response):
    mock_service = Mock()
    mock_service.documents().get().execute = Mock(return_value=get_response)
    mock_service.documents().batchUpdate().execute = Mock(return_value={})
    return mock_service


@pytest.mark.asyncio
async def test_create_and_populate_table_tabs_shaped_response():
    """No explicit tab_id, but the API returned tabs[] instead of a top-level
    body -- the manager must still find the freshly created table."""
    doc_with_only_tabs = {
        "body": {"content": []},
        "tabs": [
            {
                "tabProperties": {"tabId": "t.0"},
                "documentTab": {"body": {"content": [_table_element()]}},
            }
        ],
    }
    mock_service = _make_mock_service(doc_with_only_tabs)
    manager = TableOperationManager(mock_service)

    success, message, metadata = await manager.create_and_populate_table(
        document_id="doc_123",
        table_data=[["A", "B"], ["C", "D"]],
        index=1,
    )

    assert success is True, message
    assert metadata["populated_cells"] == 4


@pytest.mark.asyncio
async def test_create_and_populate_table_legacy_body_response():
    """Regression guard: documents without a tabs[] structure at all must
    keep working via the plain top-level body."""
    legacy_doc = {"body": {"content": [_table_element()]}}
    mock_service = _make_mock_service(legacy_doc)
    manager = TableOperationManager(mock_service)

    success, message, metadata = await manager.create_and_populate_table(
        document_id="doc_123",
        table_data=[["A", "B"], ["C", "D"]],
        index=1,
    )

    assert success is True, message
    assert metadata["populated_cells"] == 4


@pytest.mark.asyncio
async def test_create_and_populate_table_no_tables_found_still_fails_cleanly():
    empty_doc = {"body": {"content": []}}
    mock_service = _make_mock_service(empty_doc)
    manager = TableOperationManager(mock_service)

    success, message, metadata = await manager.create_and_populate_table(
        document_id="doc_123",
        table_data=[["A", "B"]],
        index=1,
    )

    assert success is False
    assert "Could not find table after creation" in message
