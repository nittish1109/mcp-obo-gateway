"""MCP OBO gateway: an authenticated, identity-preserving pass-through MCP server.

Client (e.g. Claude Desktop) --[Entra user token for OUR API]--> this gateway
  -> validate token (delegated user only)
  -> OBO exchange for a Power BI-scoped USER token
  -> forward MCP Streamable HTTP traffic to the downstream Power BI MCP server
  <- stream the response back unchanged

Routes:
  GET  /health                                   liveness (no auth)
  GET  /.well-known/oauth-protected-resource[/mcp] RFC 9728 metadata (no auth)
  POST|GET|DELETE /mcp                           authenticated pass-through
"""

from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager

import httpx
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from app.auth import AuthError, TokenValidator
from app.config import Settings
from app.obo import OboError, OboExchanger
from app.proxy import SessionGuard, forward, to_client_response

log = logging.getLogger("mcp_obo_gateway")


def _rpc_method(body: bytes) -> str:
    """Best-effort JSON-RPC method name for audit logs (never alters the request)."""
    try:
        data = json.loads(body or b"{}")
    except ValueError:
        return "?"
    if isinstance(data, list):
        return ",".join(str(m.get("method", "?")) for m in data if isinstance(m, dict))
    if isinstance(data, dict):
        method = str(data.get("method", "?"))
        if method == "tools/call":
            method += f":{(data.get('params') or {}).get('name', '?')}"
        return method
    return "?"


def create_app(settings: Settings, validator: TokenValidator | None = None,
               obo: OboExchanger | None = None,
               downstream_transport: httpx.AsyncBaseTransport | None = None) -> Starlette:
    validator = validator or TokenValidator(settings)
    obo = obo or OboExchanger(settings)
    guard = SessionGuard()
    client = httpx.AsyncClient(
        transport=downstream_transport,
        timeout=httpx.Timeout(settings.downstream_timeout_s, connect=10.0),
    )
    prm_url = f"{settings.public_base_url.rstrip('/')}/.well-known/oauth-protected-resource"

    def unauthorized(reason: str) -> Response:
        return JSONResponse(
            {"error": "unauthorized", "detail": reason},
            status_code=401,
            headers={"WWW-Authenticate":
                     f'Bearer resource_metadata="{prm_url}", error="invalid_token"'},
        )

    async def health(_: Request) -> Response:
        return JSONResponse({"status": "ok"})

    async def protected_resource_metadata(_: Request) -> Response:
        return JSONResponse({
            "resource": settings.resource_identifier,
            "authorization_servers": [settings.issuer],
            "scopes_supported": [settings.full_scope],
            "bearer_methods_supported": ["header"],
        })

    async def mcp(request: Request) -> Response:
        try:
            user = validator.validate(request.headers.get("authorization"))
        except AuthError as exc:
            log.info("auth rejected: %s", exc)
            return unauthorized(str(exc))

        session_id = request.headers.get("mcp-session-id")
        if not guard.check(session_id, user.oid):
            log.warning("session hijack blocked: user=%s session=%s", user.oid, session_id)
            return JSONResponse({"error": "forbidden", "detail": "session belongs to another user"},
                                status_code=403)

        try:
            obo_token = await obo.exchange(user)
        except OboError as exc:
            log.warning("OBO failed for user=%s: %s", user.oid, exc)
            # Deliberately NO fallback to any service identity.
            return JSONResponse({"error": "obo_failed", "detail": str(exc)}, status_code=403)

        body = await request.body()
        log.info("forward user=%s upn=%s %s %s", user.oid, user.upn, request.method,
                 _rpc_method(body) if request.method == "POST" else "")

        try:
            upstream = await forward(request, client, settings.downstream_mcp_url, obo_token)
        except httpx.HTTPError as exc:
            log.error("downstream unreachable: %s", exc)
            return JSONResponse({"error": "downstream_unavailable"}, status_code=502)

        if upstream.status_code == 401:
            # Downstream rejected the delegated token. Surfacing 401 would send the client
            # into a re-auth loop against OUR server, so report it as a gateway error.
            text = (await upstream.aread()).decode("utf-8", "replace")[:500]
            await upstream.aclose()
            log.error("downstream rejected OBO token for user=%s: %s", user.oid, text)
            return JSONResponse({"error": "downstream_rejected_token", "detail": text},
                                status_code=502)

        new_session = upstream.headers.get("mcp-session-id")
        guard.bind(new_session or session_id, user.oid)
        if request.method == "DELETE":
            guard.release(session_id)
        return to_client_response(upstream)

    @asynccontextmanager
    async def lifespan(_app):
        yield
        await client.aclose()

    return Starlette(
        routes=[
            Route("/health", health, methods=["GET"]),
            Route("/.well-known/oauth-protected-resource", protected_resource_metadata,
                  methods=["GET"]),
            Route("/.well-known/oauth-protected-resource/mcp", protected_resource_metadata,
                  methods=["GET"]),
            Route("/mcp", mcp, methods=["POST", "GET", "DELETE"]),
        ],
        lifespan=lifespan,
    )


def build() -> Starlette:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    return create_app(Settings.from_env())
