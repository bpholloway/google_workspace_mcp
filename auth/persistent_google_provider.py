"""
PersistentGoogleProvider: GoogleProvider subclass that persists Google
refresh tokens to the local credential store during token exchange.

In OAuth 2.1 standard mode, FastMCP handles /oauth2callback internally and
stores tokens in an encrypted key-value store under ``~/.fastmcp/oauth-proxy/``.
The legacy OAuth callback in ``auth/google_auth.py`` never fires in that mode,
so the per-user credential file expected by the session-store backfill
(``_merge_refresh_material_from_disk`` in ``auth/oauth21_session_store.py``)
is never written. Without it the backfill has nothing to read and tool calls
fail with ``RefreshError`` once the Google access token expires mid-session.

This subclass overrides ``_extract_upstream_claims``, the smallest seam that
receives the full Google token response on both the initial authorization
code exchange (``OAuthProxy.exchange_authorization_code``) and the refresh
exchange (``OAuthProxy.exchange_refresh_token``). On each call it writes a
full ``google.oauth2.credentials.Credentials`` record to the credential store
so the on-disk file stays current through rotation and restarts.

The override never breaks the auth flow on persistence failure — all errors
are caught and logged.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import httpx
from fastmcp.server.auth.providers.google import GoogleProvider
from google.oauth2.credentials import Credentials

logger = logging.getLogger(__name__)

GOOGLE_TOKEN_URI = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"


class PersistentGoogleProvider(GoogleProvider):
    """GoogleProvider that writes Google refresh tokens to the credential store."""

    async def _extract_upstream_claims(
        self, idp_tokens: dict[str, Any]
    ) -> Optional[dict[str, Any]]:
        claims = await super()._extract_upstream_claims(idp_tokens)
        try:
            await self._persist_google_credentials(idp_tokens)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning(
                "Failed to persist Google credentials during token exchange: %s",
                exc,
            )
        return claims

    async def _persist_google_credentials(
        self, idp_tokens: dict[str, Any]
    ) -> None:
        access_token = idp_tokens.get("access_token")
        if not access_token:
            logger.debug(
                "Google token response missing access_token; skipping persist"
            )
            return

        email = await self._resolve_user_email(access_token)
        if not email:
            logger.debug(
                "Could not resolve user email for Google token response; "
                "skipping credential persist"
            )
            return

        from auth.credential_store import get_credential_store

        store = get_credential_store()
        try:
            existing = store.get_credential(email)
        except Exception:  # pragma: no cover - defensive
            existing = None

        # On refresh exchanges Google often omits refresh_token; fall back to
        # whatever we persisted on the initial authorization.
        refresh_token = idp_tokens.get("refresh_token") or (
            existing.refresh_token if existing else None
        )
        if not refresh_token:
            logger.debug(
                "No refresh_token available for %s (upstream omitted and no "
                "prior on-disk token); skipping persist",
                email,
            )
            return

        client_id, client_secret = self._get_upstream_client_credentials()

        expiry: Optional[datetime] = None
        expires_in = idp_tokens.get("expires_in")
        if expires_in is not None:
            try:
                expiry = (
                    datetime.now(timezone.utc)
                    + timedelta(seconds=int(expires_in))
                ).replace(tzinfo=None)
            except (TypeError, ValueError):  # pragma: no cover - defensive
                expiry = None

        scopes: Optional[list[str]] = None
        scope_str = idp_tokens.get("scope")
        if isinstance(scope_str, str) and scope_str:
            scopes = scope_str.split()
        elif existing and existing.scopes:
            scopes = list(existing.scopes)

        credentials = Credentials(
            token=access_token,
            refresh_token=refresh_token,
            token_uri=GOOGLE_TOKEN_URI,
            client_id=client_id,
            client_secret=client_secret,
            scopes=scopes,
            expiry=expiry,
        )

        try:
            stored_ok = store.store_credential(email, credentials)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning(
                "Error persisting Google credentials for %s: %s", email, exc
            )
            return

        if stored_ok:
            logger.info(
                "Persisted Google OAuth credentials for %s "
                "(refresh_token_from_upstream=%s)",
                email,
                "yes" if idp_tokens.get("refresh_token") else "no",
            )

    def _get_upstream_client_credentials(
        self,
    ) -> tuple[Optional[str], Optional[str]]:
        client_id = getattr(self, "_upstream_client_id", None)
        secret_obj = getattr(self, "_upstream_client_secret", None)
        client_secret: Optional[str] = None
        if secret_obj is not None:
            if hasattr(secret_obj, "get_secret_value"):
                try:
                    client_secret = secret_obj.get_secret_value()
                except Exception:  # pragma: no cover - defensive
                    client_secret = None
            elif isinstance(secret_obj, str):
                client_secret = secret_obj
        return client_id, client_secret

    async def _resolve_user_email(self, access_token: str) -> Optional[str]:
        """Fetch the authenticated user's email from Google's userinfo endpoint.

        Using userinfo (instead of decoding id_token) works uniformly on both
        the initial exchange and the refresh exchange, where Google does not
        re-issue an id_token.
        """
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.get(
                    GOOGLE_USERINFO_URL,
                    headers={
                        "Authorization": f"Bearer {access_token}",
                        "User-Agent": "GoogleWorkspaceMCP-PersistentGoogleProvider",
                    },
                )
                if response.status_code == 200:
                    email = response.json().get("email")
                    if email:
                        return email
                    logger.debug("Google userinfo response missing email field")
                else:
                    logger.debug(
                        "Google userinfo returned status %d",
                        response.status_code,
                    )
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("Failed to fetch Google userinfo: %s", exc)
        return None
