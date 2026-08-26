"""
Tests for ``get_gmail_attachment_content``, in particular that Windows-reserved
filename characters are sanitized before the attachment is saved to disk.
"""

import base64
from typing import Any, Callable
from unittest.mock import Mock

import pytest

from gmail.gmail_tools import get_gmail_attachment_content


def _unwrap(tool: Any) -> Callable[..., Any]:
    """Unwrap FunctionTool + decorators to the original async function."""
    fn = tool.fn if hasattr(tool, "fn") else tool
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


def _build_mock_service(
    payload: bytes,
    *,
    filename: str = "attachment.bin",
    mime_type: str = "application/octet-stream",
) -> Mock:
    """Build a Mock google-api service returning ``payload`` as an attachment."""
    urlsafe_b64 = base64.urlsafe_b64encode(payload).decode("ascii")

    mock_service = Mock()

    # attachments().get(...).execute() returns the raw attachment dict
    mock_service.users().messages().attachments().get().execute.return_value = {
        "size": len(payload),
        "data": urlsafe_b64,
    }

    # messages().get(...).execute() is called to resolve filename/mime;
    # return a payload with a single matching part.
    mock_service.users().messages().get().execute.return_value = {
        "payload": {
            "parts": [
                {
                    "filename": filename,
                    "mimeType": mime_type,
                    "body": {"attachmentId": "att-123", "size": len(payload)},
                }
            ],
        },
    }

    return mock_service


@pytest.fixture
def isolated_attachment_env(tmp_path, monkeypatch):
    """Route attachment storage to a temp dir and force HTTP (not stateless) mode."""
    import core.attachment_storage as storage_module
    import auth.oauth_config as oauth_config_module
    import core.config as core_config_module

    monkeypatch.setattr(storage_module, "STORAGE_DIR", tmp_path)
    monkeypatch.setattr(oauth_config_module, "is_stateless_mode", lambda: False)
    monkeypatch.setattr(core_config_module, "get_transport_mode", lambda: "http")

    # Reset the cached module-level storage singleton so our patched
    # STORAGE_DIR actually takes effect.
    monkeypatch.setattr(storage_module, "_attachment_storage", None, raising=False)

    return tmp_path


@pytest.mark.asyncio
async def test_download_response_reports_sanitized_saved_filename(
    isolated_attachment_env,
):
    """Windows-reserved filename characters should be sanitized before saving."""
    payload = b"attached email bytes"
    mock_service = _build_mock_service(
        payload, filename="RE: Foo?.eml", mime_type="message/rfc822"
    )

    result = await _unwrap(get_gmail_attachment_content)(
        service=mock_service,
        message_id="msg-1",
        attachment_id="att-123",
        user_google_email="user@example.com",
    )

    assert "Filename: RE: Foo?.eml" in result
    assert "Saved filename: RE_ Foo_" in result

    saved_files = list(isolated_attachment_env.iterdir())
    assert len(saved_files) == 1
    assert saved_files[0].name.startswith("RE_ Foo_")
    assert ":" not in saved_files[0].name
    assert "?" not in saved_files[0].name
    assert saved_files[0].read_bytes() == payload
