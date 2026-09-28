import time
import uuid

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from app.config import Settings

TENANT = "11111111-1111-1111-1111-111111111111"
API_ID = "22222222-2222-2222-2222-222222222222"


@pytest.fixture(scope="session")
def rsa_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def settings():
    return Settings(
        tenant_id=TENANT,
        api_client_id=API_ID,
        api_client_secret="test-secret",
        api_app_id_uri=f"api://{API_ID}",
        public_base_url="https://gw.example.test",
        downstream_mcp_url="https://downstream.example.test/v1/mcp/powerbi",
    )


class FakeJwk:
    def __init__(self, key):
        self.key = key


class FakeJwkClient:
    def __init__(self, public_key):
        self._pub = public_key

    def get_signing_key_from_jwt(self, _token):
        return FakeJwk(self._pub)


@pytest.fixture
def jwk_client(rsa_key):
    return FakeJwkClient(rsa_key.public_key())


@pytest.fixture
def make_token(rsa_key):
    def _make(**overrides):
        now = int(time.time())
        claims = {
            "iss": f"https://login.microsoftonline.com/{TENANT}/v2.0",
            "aud": API_ID,
            "tid": TENANT,
            "oid": overrides.pop("oid", str(uuid.uuid4())),
            "scp": "access_as_user",
            "preferred_username": "user@example.test",
            "iat": now,
            "nbf": now,
            "exp": now + 3600,
        }
        claims.update(overrides)
        claims = {k: v for k, v in claims.items() if v is not None}
        return jwt.encode(claims, rsa_key, algorithm="RS256")
    return _make


class FakeMsal:
    """Mimics MSAL OBO: returns a token for the same user (oid) as the assertion."""

    def __init__(self, fail=False, wrong_user=False, app_only=False):
        self.calls = 0
        self.fail = fail
        self.wrong_user = wrong_user
        self.app_only = app_only

    def acquire_token_on_behalf_of(self, user_assertion, scopes):
        self.calls += 1
        if self.fail:
            return {"error": "invalid_grant", "error_description": "AADSTS65001: consent required"}
        inbound = jwt.decode(user_assertion, options={"verify_signature": False})
        oid = "someone-else" if self.wrong_user else inbound["oid"]
        claims = {"oid": oid, "aud": "https://analysis.windows.net/powerbi/api",
                  "scp": "Dataset.Read.All", "n": self.calls}
        if self.app_only:
            claims["idtyp"] = "app"
        return {"access_token": jwt.encode(claims, "k", algorithm="HS256"), "expires_in": 3600}
