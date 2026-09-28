# MCP OBO Gateway

A hosted, authenticated **pass-through MCP server**. It lets MCP clients (e.g. Claude
Desktop) reach the Microsoft-hosted **Power BI MCP server** over Streamable HTTP while
preserving the **signed-in user's identity end to end**, so OneLake permissions and
Fabric **row-level security (RLS)** apply per user. No shared service account is used
anywhere on the data path.

It contains **no business logic**: no database, no vector store, no tool definitions of
its own. It authenticates, swaps the token, and relays bytes.

## Flow

```
Claude Desktop ──(1) user signs in to Entra, gets token for OUR API──▶
   MCP OBO Gateway (Azure Container Apps)
     (2) validate token: tenant, issuer, audience, scope, delegated-user only
     (3) OBO: exchange it for a Power BI-scoped token FOR THE SAME USER
         grant_type = urn:ietf:params:oauth:grant-type:jwt-bearer
     (4) forward the MCP request unchanged, Authorization = user's OBO token
   ──▶ Power BI MCP server  https://api.fabric.microsoft.com/v1/mcp/powerbi
         runs DAX as that user ⇒ that user's permissions and RLS apply
   ◀── response (JSON or SSE) streamed back unchanged
```

## Security properties (and the tests that prove them)

| Property | Where | Test |
|---|---|---|
| Only delegated user tokens from our tenant, for our API, with `access_as_user` | `app/auth.py` | `test_bad_tokens_rejected` |
| App-only / service-principal tokens rejected | `app/auth.py` | `…[app-only token]` |
| Downstream receives the **OBO** token, never the inbound token | `app/proxy.py` | `test_forwards_with_obo_token_not_inbound_token` |
| OBO token must belong to the same user (`oid` check) | `app/obo.py` | `test_obo_failures_raise_no_fallback` |
| **No fallback** to any service identity if OBO fails | `app/obo.py`, `app/main.py` | `test_obo_failure_blocks_request` |
| OBO cache isolated per user | `app/obo.py` | `test_obo_cache_is_isolated_per_user` |
| One user can't ride another user's MCP session | `app/proxy.py` | `test_session_cannot_be_used_by_another_user` |
| Cookies/other headers not forwarded or leaked | `app/proxy.py` | `test_forwards_with_obo_token_not_inbound_token` |
| Clients discover auth via RFC 9728 metadata + `WWW-Authenticate` | `app/main.py` | `test_no_token_gets_401_with_discovery_header` |
| Tokens are never logged; audit logs record user `oid`/UPN + MCP method | `app/main.py` | — |

## Layout

```
app/config.py   settings (env only)
app/auth.py     inbound token validation
app/obo.py      On-Behalf-Of exchange + per-user cache
app/proxy.py    streaming pass-through + session guard
app/main.py     routes: /health, /.well-known/oauth-protected-resource, /mcp
deploy/deploy_aca.sh   step-by-step Azure Container Apps deploy (Key Vault secret)
docs/ENTRA_SETUP.md    the two app registrations, Power BI settings, RLS test data
```

## Run locally

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q                                   # 21 tests, no network needed
cp .env.example .env                        # fill in, then:
set -a; source .env; set +a
uvicorn app.main:build --factory --port 8000
```

Local runs can serve `/health` and the metadata, but a real end-to-end test needs a
public HTTPS URL (Claude connects from Anthropic's cloud), so test end to end on Azure.

## Deploy

See `docs/ENTRA_SETUP.md` first, then run `deploy/deploy_aca.sh` block by block.

## Test plan

1. **Discovery**: `curl https://<fqdn>/.well-known/oauth-protected-resource` shows the tenant;
   `POST /mcp` without a token returns 401 with `WWW-Authenticate`.
2. **MCP Inspector** (`npx @modelcontextprotocol/inspector`): connect to `https://<fqdn>/mcp`
   with the public client ID; complete sign-in; list tools (these come from the downstream
   Power BI server).
3. **Claude Desktop**: add the custom connector (see docs), ask
   `What tables are in semantic model <model-id>?`.
4. **RLS proof (the deliverable)**: sign in as **userA**, ask for row counts by `Region`;
   sign out, sign in as **userB**, ask the same. Expected: each sees only their region.
   Check the gateway logs show different user `oid`s for the two runs.

## Known risks to watch during first integration

- **`resource` parameter vs Entra v2.** MCP clients send an RFC 8707 `resource`
  parameter during sign-in; Entra's v2 endpoint has been reported to reject requests
  where `resource` conflicts with the requested scope (AADSTS9010010). If sign-in fails
  with that code, set `RESOURCE_IDENTIFIER` to the API's App ID URI (`api://<id>`) and
  retest. Inspector will show the exact request.
- **Authorization-server discovery.** Entra publishes OpenID Connect discovery; MCP clients
  are expected to fall back to it. Inspector will confirm.
- **Power BI MCP server is preview.** Microsoft now recommends the Fabric IQ MCP server for
  semantic-model consumption. Switching is a config change (`DOWNSTREAM_MCP_URL`, and
  `DOWNSTREAM_SCOPE` if it differs).
- **Multiple replicas.** The session guard is per replica; enable session affinity or keep
  one replica for now.
