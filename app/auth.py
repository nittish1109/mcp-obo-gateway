"""Validate the incoming Entra ID access token.

Only DELEGATED USER tokens issued by our tenant, for our API, carrying our scope,
are accepted. App-only (service principal) tokens are rejected outright: the whole
point of this gateway is that data is always accessed as the signed-in user, so
that OneLake identity and Fabric RLS apply.
"""

from __future__ import annotations

from dataclasses import dataclass

import jwt
from jwt import PyJWKClient

from app.config import Settings


class AuthError(Exception):
    """Raised when the inbound token is missing or invalid (-> HTTP 401)."""


@dataclass(frozen=True)
class UserContext:
    token: str      # the raw inbound token (used as the OBO assertion)
    oid: str        # user object id (stable per user; used for isolation + audit)
    tid: str
    upn: str        # preferred_username / upn, for audit logs only


class TokenValidator:
    def __init__(self, settings: Settings, jwk_client: PyJWKClient | None = None) -> None:
        self._s = settings
        self._jwks = jwk_client or PyJWKClient(settings.jwks_uri, cache_keys=True, lifespan=3600)

    def validate(self, auth_header: str | None) -> UserContext:
        if not auth_header or not auth_header.lower().startswith("bearer "):
            raise AuthError("missing bearer token")
        token = auth_header.split(" ", 1)[1].strip()
        if not token:
            raise AuthError("missing bearer token")

        try:
            signing_key = self._jwks.get_signing_key_from_jwt(token).key
            claims = jwt.decode(
                token,
                signing_key,
                algorithms=["RS256"],
                audience=self._s.accepted_audiences,
                issuer=self._s.issuer,
                options={"require": ["exp", "iat", "aud", "iss"]},
                leeway=60,
            )
        except jwt.PyJWTError as exc:
            raise AuthError(f"invalid token: {exc}") from exc

        if claims.get("tid") != self._s.tenant_id:
            raise AuthError("token from unexpected tenant")

        # Reject app-only tokens: they carry `roles`, not `scp`, and have idtyp=app.
        if claims.get("idtyp") == "app" or "scp" not in claims:
            raise AuthError("app-only tokens are not accepted; a signed-in user is required")

        scopes = set(str(claims.get("scp", "")).split())
        if self._s.required_scope not in scopes:
            raise AuthError(f"token lacks required scope '{self._s.required_scope}'")

        oid = claims.get("oid")
        if not oid:
            raise AuthError("token has no user object id (oid)")

        return UserContext(
            token=token,
            oid=oid,
            tid=claims["tid"],
            upn=claims.get("preferred_username") or claims.get("upn") or "",
        )
