"""Regression coverage for restored Apps Script tools and authentication."""

from unittest.mock import AsyncMock, Mock
from urllib.parse import parse_qs, urlparse

import pytest
from googleapiclient.discovery import build

from auth import service_decorator
from core.tool_tier_loader import resolve_tools_from_tier
from gappsscript import apps_script_tools


def test_core_tier_includes_existing_apps_script_tools():
    tool_names, services = resolve_tools_from_tier("core")
    assert "appscript" in services
    expected = {
        "list_script_projects",
        "get_script_project",
        "get_script_content",
        "create_script_project",
        "update_script_content",
        "run_script_function",
        "manage_deployment",
        "list_deployments",
        "list_script_processes",
        "delete_script_project",
        "list_versions",
        "create_version",
        "get_version",
        "get_script_metrics",
        "generate_trigger_code",
    }
    selected, _ = resolve_tools_from_tier("core", ["appscript"])
    assert set(selected) == expected
    assert expected <= set(tool_names)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tool_name,kwargs,expected_scope",
    [
        ("create_version", {"script_id": "test-script"}, "script.projects"),
        ("list_script_processes", {}, "script.processes"),
        ("get_script_metrics", {"script_id": "test-script"}, "script.metrics"),
        ("delete_script_project", {"script_id": "test-script"}, "drive"),
    ],
)
async def test_tools_authenticate_with_api_required_scope(
    monkeypatch, tool_name, kwargs, expected_scope
):
    service = Mock()
    authenticate = AsyncMock(return_value=(service, "test@example.com"))
    monkeypatch.setattr(service_decorator, "_authenticate_service", authenticate)
    monkeypatch.setattr(
        service_decorator,
        "_get_auth_context",
        AsyncMock(return_value=(None, None, None)),
    )
    monkeypatch.setattr(service_decorator, "_user_email_is_managed", lambda: False)
    monkeypatch.setattr(service_decorator, "_detect_oauth_version", lambda *a: False)
    monkeypatch.setattr(
        apps_script_tools, f"_{tool_name}_impl", AsyncMock(return_value="ok")
    )

    tool = getattr(apps_script_tools, tool_name)
    assert await tool(user_google_email="test@example.com", **kwargs) == "ok"
    expected = [f"https://www.googleapis.com/auth/{expected_scope}"]
    assert authenticate.await_args.args[5] == expected
    assert tool._required_google_scopes == expected
    service.close.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("script_id", [None, "test-script"])
async def test_process_filter_matches_google_client_schema(monkeypatch, script_id):
    # Use the bundled discovery schema, with HTTP execution replaced locally.
    # Unlike a permissive Mock, this client rejects unsupported parameters.
    requests = []

    def execute(request, **kwargs):
        requests.append(request)
        return {"processes": []}

    monkeypatch.setattr("googleapiclient.http.HttpRequest.execute", execute)
    service = build("script", "v1", developerKey="test-only", static_discovery=True)
    try:
        await apps_script_tools._list_script_processes_impl(
            service, "test@example.com", page_size=10, script_id=script_id
        )
    finally:
        service.close()

    query = parse_qs(urlparse(requests[0].uri).query)
    assert query["pageSize"] == ["10"]
    if script_id:
        assert query["userProcessFilter.scriptId"] == [script_id]
    else:
        assert "userProcessFilter.scriptId" not in query
