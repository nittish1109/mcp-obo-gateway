"""Configuration, loaded only from environment variables (never hard-coded)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _req(name: str) -> str:
    val = os.getenv(name, "").strip()
    if not val:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return val


@dataclass(frozen=True)
class Settings:
    # --- Entra ID ---
    tenant_id: str
    api_client_id: str          # the Confidential API app registration (this server)
    api_client_secret: str      # its client secret (from Key Vault in Azure)
    api_app_id_uri: str         # e.g. api://<api_client_id>
    required_scope: str = "access_as_user"

    # --- Downstream (Microsoft-hosted Power BI MCP server) ---
    downstream_mcp_url: str = "https://api.fabric.microsoft.com/v1/mcp/powerbi"
    downstream_scope: str = "https://analysis.windows.net/powerbi/api/.default"

    # --- This server ---
    public_base_url: str = "http://localhost:8000"
    resource_identifier: str = ""  # defaults to <public_base_url>/mcp
    downstream_timeout_s: float = 300.0

    # derived
    authority: str = field(init=False)
    issuer: str = field(init=False)
    jwks_uri: str = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "authority", f"https://login.microsoftonline.com/{self.tenant_id}")
        object.__setattr__(self, "issuer", f"https://login.microsoftonline.com/{self.tenant_id}/v2.0")
        object.__setattr__(
            self, "jwks_uri",
            f"https://login.microsoftonline.com/{self.tenant_id}/discovery/v2.0/keys",
        )
        if not self.resource_identifier:
            object.__setattr__(self, "resource_identifier", f"{self.public_base_url.rstrip('/')}/mcp")

    @property
    def full_scope(self) -> str:
        return f"{self.api_app_id_uri.rstrip('/')}/{self.required_scope}"

    @property
    def accepted_audiences(self) -> list[str]:
        # v2 access tokens carry the client-id GUID as aud; v1 carry the App ID URI.
        return [self.api_client_id, self.api_app_id_uri]

    @classmethod
    def from_env(cls) -> "Settings":
        api_client_id = _req("API_CLIENT_ID")
        return cls(
            tenant_id=_req("TENANT_ID"),
            api_client_id=api_client_id,
            api_client_secret=_req("API_CLIENT_SECRET"),
            api_app_id_uri=os.getenv("API_APP_ID_URI", f"api://{api_client_id}"),
            required_scope=os.getenv("REQUIRED_SCOPE", "access_as_user"),
            downstream_mcp_url=os.getenv(
                "DOWNSTREAM_MCP_URL", "https://api.fabric.microsoft.com/v1/mcp/powerbi"
            ),
            downstream_scope=os.getenv(
                "DOWNSTREAM_SCOPE", "https://analysis.windows.net/powerbi/api/.default"
            ),
            public_base_url=os.getenv("PUBLIC_BASE_URL", "http://localhost:8000"),
            resource_identifier=os.getenv("RESOURCE_IDENTIFIER", ""),
            downstream_timeout_s=float(os.getenv("DOWNSTREAM_TIMEOUT_S", "300")),
        )
