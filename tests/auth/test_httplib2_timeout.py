"""Regression tests for Issue #835 — httplib2 socket timeout."""

import os
import sys
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from auth.google_auth import _build_authorized_http  # noqa: E402


def test_build_authorized_http_uses_explicit_timeout():
    """_build_authorized_http must construct httplib2.Http with a timeout."""
    mock_credentials = MagicMock()
    mock_http = MagicMock()
    mock_authorized = MagicMock()

    with (
        patch("auth.google_auth.httplib2.Http", return_value=mock_http) as mock_http_cls,
        patch(
            "auth.google_auth.google_auth_httplib2.AuthorizedHttp",
            return_value=mock_authorized,
        ) as mock_auth_http_cls,
    ):
        result = _build_authorized_http(mock_credentials, timeout=42)

    mock_http_cls.assert_called_once_with(timeout=42)
    mock_auth_http_cls.assert_called_once_with(mock_credentials, http=mock_http)
    assert result is mock_authorized


def test_build_authorized_http_default_timeout_is_30():
    """Default timeout should be 30 seconds."""
    mock_credentials = MagicMock()
    mock_http = MagicMock()

    with (
        patch("auth.google_auth.httplib2.Http", return_value=mock_http) as mock_http_cls,
        patch(
            "auth.google_auth.google_auth_httplib2.AuthorizedHttp",
        ) as mock_auth_http_cls,
    ):
        _build_authorized_http(mock_credentials)

    mock_http_cls.assert_called_once_with(timeout=30)
    mock_auth_http_cls.assert_called_once_with(mock_credentials, http=mock_http)
