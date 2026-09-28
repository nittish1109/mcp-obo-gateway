"""OAuth 2.0 On-Behalf-Of exchange: inbound user token -> Power BI-scoped user token.

MSAL sends grant_type=urn:ietf:params:oauth:grant-type:jwt-bearer with the inbound
token as the assertion and our confidential-client credentials. The returned token
still represents THE USER, so the downstream Power BI MCP server applies that user's
permissions and RLS.

Design rules enforced here:
  * No fallback. If OBO fails, the request fails. There is no service-principal path.
  * Per-user isolation. Cached tokens are keyed by a hash of the inbound assertion and
    are only returned to a caller presenting that same assertion. We also check the
    OBO token's `oid` matches the inbound user before using it.
"""

from __future__ import annotations

import hashlib
import threading
import time
from dataclasses import dataclass
from typing import Any, Protocol

import anyio
import jwt

from app.auth import UserContext
from app.config import Settings


class OboError(Exception):
    """OBO exchange failed (-> request fails; never falls back)."""


class _MsalLike(Protocol):
    def acquire_token_on_behalf_of(self, user_assertion: str, scopes: list[str]) -> dict[str, Any]:
        ...


@dataclass
class _Entry:
    token: str
    expires_at: float
    oid: str


class OboExchanger:
    def __init__(self, settings: Settings, msal_app: _MsalLike | None = None,
                 refresh_skew_s: int = 300, max_entries: int = 5000) -> None:
        self._s = settings
        self._app = msal_app  # built lazily: MSAL contacts Entra on construction, and
        self._app_lock = threading.Lock()  # we don't want startup to depend on that.
        self._skew = refresh_skew_s
        self._max = max_entries
        self._cache: dict[str, _Entry] = {}
        self._lock = threading.Lock()

    def _msal(self) -> _MsalLike:
        if self._app is None:
            with self._app_lock:
                if self._app is None:
                    import msal

                    try:
                        self._app = msal.ConfidentialClientApplication(
                            client_id=self._s.api_client_id,
                            client_credential=self._s.api_client_secret,
                            authority=self._s.authority,
                        )
                    except Exception as exc:  # noqa: BLE001
                        raise OboError(f"cannot initialise MSAL: {exc}") from exc
        return self._app

    @staticmethod
    def _key(assertion: str) -> str:
        return hashlib.sha256(assertion.encode("utf-8")).hexdigest()

    def _get_cached(self, key: str, oid: str) -> str | None:
        now = time.time()
        with self._lock:
            entry = self._cache.get(key)
            if entry and entry.oid == oid and entry.expires_at - self._skew > now:
                return entry.token
            if entry:
                self._cache.pop(key, None)
        return None

    def _put(self, key: str, entry: _Entry) -> None:
        now = time.time()
        with self._lock:
            if len(self._cache) >= self._max:
                for k in [k for k, e in self._cache.items() if e.expires_at <= now]:
                    del self._cache[k]
                if len(self._cache) >= self._max:  # still full: drop the soonest-expiring
                    del self._cache[min(self._cache, key=lambda k: self._cache[k].expires_at)]
            self._cache[key] = entry

    def exchange_sync(self, user: UserContext) -> str:
        key = self._key(user.token)
        cached = self._get_cached(key, user.oid)
        if cached:
            return cached

        result = self._msal().acquire_token_on_behalf_of(
            user_assertion=user.token, scopes=[self._s.downstream_scope]
        )
        if not result or "access_token" not in result:
            err = (result or {}).get("error", "unknown_error")
            desc = (result or {}).get("error_description", "")
            raise OboError(f"OBO failed: {err}: {desc[:300]}")

        token = result["access_token"]
        # Defence in depth: the delegated token must belong to the same user.
        try:
            obo_claims = jwt.decode(token, options={"verify_signature": False})
        except jwt.PyJWTError as exc:
            raise OboError(f"OBO returned an unparseable token: {exc}") from exc
        if obo_claims.get("oid") != user.oid:
            raise OboError("OBO token user does not match inbound user; refusing")
        if obo_claims.get("idtyp") == "app":
            raise OboError("OBO returned an app-only token; refusing")

        expires_in = int(result.get("expires_in", 3600))
        self._put(key, _Entry(token=token, expires_at=time.time() + expires_in, oid=user.oid))
        return token

    async def exchange(self, user: UserContext) -> str:
        # MSAL is synchronous (HTTP via requests); keep it off the event loop.
        return await anyio.to_thread.run_sync(self.exchange_sync, user)
