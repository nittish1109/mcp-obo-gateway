# Entra ID & Power BI setup

All steps happen in **one** Entra tenant: the tenant that hosts your Fabric / Power BI
environment. You need rights to register apps and (for step 4) grant admin consent.

## 1. Confidential API app (this gateway)

1. Entra admin center → **App registrations → New registration**
   - Name: `MCP OBO Gateway API`
   - Supported account types: **Single tenant**
   - Redirect URI: none
2. Copy the **Application (client) ID** → `API_CLIENT_ID`, and the **Directory (tenant) ID** → `TENANT_ID`.
3. **Expose an API**
   - Set **Application ID URI** to `api://<API_CLIENT_ID>` → `API_APP_ID_URI`.
   - **Add a scope**: name `access_as_user`, who can consent: *Admins and users*, fill the display texts, state *Enabled*.
4. **Manifest**: set the access-token version to 2
   (`"requestedAccessTokenVersion": 2` under `api` in the current manifest format;
   `"accessTokenAcceptedVersion": 2` in the legacy format). The gateway validates the
   v2 issuer only; without this, tokens arrive as v1 and are rejected.
5. **Certificates & secrets → New client secret** → store it in Key Vault (the deploy
   script does this). This is the only secret in the system.
6. **API permissions → Add a permission → Power BI Service → Delegated**, add:
   - `Dataset.Read.All`
   - `Workspace.Read.All`
   - `MLModel.Execute.All`
   (the set Microsoft lists for the Power BI Consumption MCP server). **Grant admin consent.**

   These are **delegated** permissions only. Do not add application permissions and do
   not add this app to any Power BI workspace: the gateway must never be able to read
   data as itself.

## 2. Public client app (Claude Desktop)

1. **New registration**: `MCP OBO Gateway - Claude Desktop`, **Single tenant**.
2. **Authentication → Add a platform → Mobile and desktop applications** → redirect URI
   `https://claude.ai/api/mcp/auth_callback`.
   Leave **Allow public client flows = No**.
3. **API permissions → Add a permission → My APIs → MCP OBO Gateway API → Delegated →
   `access_as_user`** → Grant admin consent.
4. Copy its **Application (client) ID**: this is the OAuth Client ID you enter in Claude.

Optional, removes a consent prompt: in the API app, **Expose an API → Add a client
application** → the Claude app's client ID, authorised for `access_as_user`.

## 3. Power BI / Fabric admin settings

- Admin portal → Tenant settings → enable
  **"Users can use the Power BI Model Context Protocol server endpoint (preview)"**.

## 4. RLS test data

The downstream server needs a semantic model. For the RLS proof:

1. Create a small semantic model with a column that splits the rows (e.g. `Region`).
2. Define an RLS role, e.g. `East` with filter `[Region] = "East"`, and a second role
   `West`. On macOS use the Power BI web modelling experience (Power BI Desktop is
   Windows-only).
3. Two test users in the tenant, **userA** in role `East`, **userB** in role `West`.
4. Give both users the **Viewer** workspace role plus **Build** permission on the model.
   **Do not** make them Admin, Member or Contributor: those roles bypass RLS, so the
   test would wrongly show all rows.

## 5. Connect Claude Desktop

Customize → Connectors → **+** → Add custom connector:
- Name: `Meriton Fabric (OBO gateway)`
- URL: `https://<container-app-fqdn>/mcp`
- Advanced settings → **OAuth Client ID**: the public client ID from step 2.

Claude's free plan allows one custom connector. Connections come from Anthropic's cloud,
so the container app must have public ingress.
