import time

import httpx
import jwt
import pytest
from starlette.testclient import TestClient

from app.auth import AuthError, TokenValidator
from app.main import create_app
from app.obo import OboError, OboExchanger
from tests.conftest import FakeMsal

# ---------------------------------------------------------------- token validation


def test_valid_user_token_accepted(settings, jwk_client, make_token):
    user = TokenValidator(settings, jwk_client).validate(f"Bearer {make_token(oid='u1')}")
    assert user.oid == "u1"


@pytest.mark.parametrize("overrides,why", [
    ({"aud": "some-other-api"}, "wrong audience"),
    ({"iss": "https://login.microsoftonline.com/other/v2.0"}, "wrong issuer"),
    ({"exp": int(time.time()) - 3600}, "expired"),
    ({"tid": "other-tenant"}, "wrong tenant"),
    ({"scp": "User.Read"}, "missing required scope"),
    ({"scp": None, "roles": ["Admin"], "idtyp": "app"}, "app-only token"),
    ({"oid": None}, "no oid"),
])
def test_bad_tokens_rejected(settings, jwk_client, make_token, overrides, why):
    token = make_token(**overrides)
    with pytest.raises(AuthError):
        TokenValidator(settings, jwk_client).validate(f"Bearer {token}")


def test_missing_header_rejected(settings, jwk_client):
    with pytest.raises(AuthError):
        TokenValidator(settings, jwk_client).validate(None)


# ---------------------------------------------------------------- OBO


def _user(settings, jwk_client, make_token, oid):
    return TokenValidator(settings, jwk_client).validate(f"Bearer {make_token(oid=oid)}")


def test_obo_returns_token_for_same_user_and_caches(settings, jwk_client, make_token):
    msal = FakeMsal()
    obo = OboExchanger(settings, msal_app=msal)
    alice = _user(settings, jwk_client, make_token, "alice")
    t1 = obo.exchange_sync(alice)
    t2 = obo.exchange_sync(alice)
    assert t1 == t2 and msal.calls == 1
    assert jwt.decode(t1, options={"verify_signature": False})["oid"] == "alice"


def test_obo_cache_is_isolated_per_user(settings, jwk_client, make_token):
    obo = OboExchanger(settings, msal_app=FakeMsal())
    a = obo.exchange_sync(_user(settings, jwk_client, make_token, "alice"))
    b = obo.exchange_sync(_user(settings, jwk_client, make_token, "bob"))
    assert jwt.decode(a, options={"verify_signature": False})["oid"] == "alice"
    assert jwt.decode(b, options={"verify_signature": False})["oid"] == "bob"


@pytest.mark.parametrize("fake", [FakeMsal(fail=True), FakeMsal(wrong_user=True),
                                  FakeMsal(app_only=True)])
def test_obo_failures_raise_no_fallback(settings, jwk_client, make_token, fake):
    obo = OboExchanger(settings, msal_app=fake)
    with pytest.raises(OboError):
        obo.exchange_sync(_user(settings, jwk_client, make_token, "alice"))


# ---------------------------------------------------------------- end-to-end proxy


class Downstream:
    """Fake downstream MCP server recording what it received."""

    def __init__(self, status=200, sse=False):
        self.requests = []
        self.status = status
        self.sse = sse

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.sse:
            return httpx.Response(self.status, headers={"content-type": "text/event-stream",
                                                        "mcp-session-id": "sess-1"},
                                  content=b"event: message\ndata: {\"jsonrpc\":\"2.0\",\"id\":1}\n\n")
        return httpx.Response(self.status, headers={"content-type": "application/json",
                                                    "mcp-session-id": "sess-1",
                                                    "set-cookie": "x=1"},
                              json={"jsonrpc": "2.0", "id": 1, "result": {"ok": True}})


def _client(settings, jwk_client, downstream, msal=None):
    app = create_app(settings, validator=TokenValidator(settings, jwk_client),
                     obo=OboExchanger(settings, msal_app=msal or FakeMsal()),
                     downstream_transport=httpx.MockTransport(downstream.handler))
    return TestClient(app)


INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}


def test_health_and_metadata_are_public(settings, jwk_client):
    c = _client(settings, jwk_client, Downstream())
    assert c.get("/health").json() == {"status": "ok"}
    prm = c.get("/.well-known/oauth-protected-resource").json()
    assert prm["authorization_servers"] == [settings.issuer]
    assert prm["scopes_supported"] == [f"api://{settings.api_client_id}/access_as_user"]
    assert prm["resource"] == "https://gw.example.test/mcp"


def test_no_token_gets_401_with_discovery_header(settings, jwk_client):
    down = Downstream()
    r = _client(settings, jwk_client, down).post("/mcp", json=INIT)
    assert r.status_code == 401
    assert "resource_metadata=" in r.headers["www-authenticate"]
    assert down.requests == []  # nothing reached downstream


def test_forwards_with_obo_token_not_inbound_token(settings, jwk_client, make_token):
    down = Downstream()
    inbound = make_token(oid="alice")
    r = _client(settings, jwk_client, down).post(
        "/mcp", json=INIT,
        headers={"Authorization": f"Bearer {inbound}", "Cookie": "secret=1",
                 "Accept": "application/json, text/event-stream"})
    assert r.status_code == 200 and r.json()["result"] == {"ok": True}
    sent = down.requests[0]
    fwd_token = sent.headers["authorization"].split(" ", 1)[1]
    assert fwd_token != inbound                                   # swapped
    assert jwt.decode(fwd_token, options={"verify_signature": False})["oid"] == "alice"
    assert "cookie" not in sent.headers                           # dropped
    assert r.headers["mcp-session-id"] == "sess-1"                # passed back
    assert "set-cookie" not in r.headers                          # not leaked


def test_sse_is_streamed_through(settings, jwk_client, make_token):
    down = Downstream(sse=True)
    r = _client(settings, jwk_client, down).post(
        "/mcp", json=INIT, headers={"Authorization": f"Bearer {make_token()}"})
    assert r.headers["content-type"].startswith("text/event-stream")
    assert b"event: message" in r.content


def test_session_cannot_be_used_by_another_user(settings, jwk_client, make_token):
    down = Downstream()
    c = _client(settings, jwk_client, down)
    r1 = c.post("/mcp", json=INIT, headers={"Authorization": f"Bearer {make_token(oid='alice')}"})
    sid = r1.headers["mcp-session-id"]
    r2 = c.post("/mcp", json=INIT, headers={"Authorization": f"Bearer {make_token(oid='bob')}",
                                            "Mcp-Session-Id": sid})
    assert r2.status_code == 403
    assert len(down.requests) == 1  # bob's request never reached downstream


def test_obo_failure_blocks_request(settings, jwk_client, make_token):
    down = Downstream()
    r = _client(settings, jwk_client, down, msal=FakeMsal(fail=True)).post(
        "/mcp", json=INIT, headers={"Authorization": f"Bearer {make_token()}"})
    assert r.status_code == 403 and r.json()["error"] == "obo_failed"
    assert down.requests == []


def test_downstream_401_becomes_502_not_reauth_loop(settings, jwk_client, make_token):
    r = _client(settings, jwk_client, Downstream(status=401)).post(
        "/mcp", json=INIT, headers={"Authorization": f"Bearer {make_token()}"})
    assert r.status_code == 502 and r.json()["error"] == "downstream_rejected_token"


def test_app_starts_without_contacting_entra(settings):
    # No validator/obo injected: real MSAL + JWKS clients, but both are lazy,
    # so startup must not depend on Entra being reachable.
    c = TestClient(create_app(settings))
    assert c.get("/health").status_code == 200
