"""Pure pass-through of MCP Streamable HTTP traffic to the downstream MCP server.

No MCP logic lives here: JSON-RPC bodies are forwarded byte-for-byte, and responses
(plain JSON or SSE streams) are streamed back unchanged. The only thing that changes
is the Authorization header, which is replaced with the user's OBO token.
"""

from __future__ import annotations

import threading

import httpx
from starlette.background import BackgroundTask
from starlette.requests import Request
from starlette.responses import StreamingResponse

# Only these request headers are forwarded. Everything else (inbound Authorization,
# cookies, host, forwarding headers) is dropped.
FORWARD_REQUEST_HEADERS = {
    "content-type", "accept", "mcp-session-id", "mcp-protocol-version", "last-event-id",
}
# Response headers passed back to the client.
FORWARD_RESPONSE_HEADERS = {
    "content-type", "mcp-session-id", "mcp-protocol-version", "cache-control",
}


class SessionGuard:
    """Binds a downstream MCP session id to the user who created it, so one user
    cannot ride another user's session by presenting its Mcp-Session-Id.

    In-memory, per replica. With several replicas an unknown session id is allowed
    (the downstream still receives the caller's own token); enable session affinity
    on Container Apps if you want the binding enforced across replicas.
    """

    def __init__(self) -> None:
        self._owners: dict[str, str] = {}
        self._lock = threading.Lock()

    def check(self, session_id: str | None, oid: str) -> bool:
        if not session_id:
            return True
        with self._lock:
            owner = self._owners.get(session_id)
        return owner is None or owner == oid

    def bind(self, session_id: str | None, oid: str) -> None:
        if session_id:
            with self._lock:
                self._owners.setdefault(session_id, oid)

    def release(self, session_id: str | None) -> None:
        if session_id:
            with self._lock:
                self._owners.pop(session_id, None)


def build_upstream_headers(request: Request, obo_token: str) -> dict[str, str]:
    headers = {k: v for k, v in request.headers.items() if k.lower() in FORWARD_REQUEST_HEADERS}
    headers["authorization"] = f"Bearer {obo_token}"
    return headers


async def forward(request: Request, client: httpx.AsyncClient, url: str,
                  obo_token: str) -> httpx.Response:
    body = await request.body()
    upstream_req = client.build_request(
        request.method, url,
        headers=build_upstream_headers(request, obo_token),
        content=body if body else None,
        params=dict(request.query_params),
    )
    return await client.send(upstream_req, stream=True)


def to_client_response(upstream: httpx.Response, status_override: int | None = None,
                       ) -> StreamingResponse:
    headers = {k: v for k, v in upstream.headers.items()
               if k.lower() in FORWARD_RESPONSE_HEADERS}
    return StreamingResponse(
        upstream.aiter_bytes(),
        status_code=status_override or upstream.status_code,
        headers=headers,
        background=BackgroundTask(upstream.aclose),
    )
