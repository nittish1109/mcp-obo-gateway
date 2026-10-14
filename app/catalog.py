import os
import httpx
from pathlib import Path
from typing import List, Dict, Any

PBI_API_BASE = "https://api.powerbi.com/v1.0/myorg"
LOCAL_ONTOLOGY_PATH = Path(__file__).parent / "Meriton_Master_Ontology.md"
DEFAULT_ONTOLOGY_URL = (
    "https://onelake.dfs.fabric.microsoft.com/"
    "1f7ebbe2-855b-4923-a413-6cc027fa7a1a/"
    "38629310-d3cd-4f45-83d1-ec40bdf59c53/"
    "Files/ontology/Meriton_Master_Ontology.md"
)

DISCOVERY_TOOLS = [
    {
        "name": "get_master_ontology",
        "description": (
            "MANDATORY FIRST STEP: Retrieves the Enterprise Master Business Ontology markdown. "
            "Call this tool before inspecting schemas or generating DAX queries to understand certified "
            "business definitions (Committed Spend, Invoiced Spend, Exception Rates), matching hierarchies, "
            "and natural language synonyms across Meriton operations."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {}
        }
    },
    {
        "name": "list_workspaces",
        "description": "Lists all Power BI / Fabric workspaces accessible to the user.",
        "inputSchema": {
            "type": "object",
            "properties": {}
        }
    },
    {
        "name": "search_semantic_models",
        "description": (
            "Searches for semantic models (datasets) across accessible workspaces. Returns dataset names, dataset IDs (GUIDs), "
            "and workspace details so they can be inspected with GetSemanticModelSchema and queried with ExecuteQuery."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "workspace_name": {
                    "type": "string",
                    "description": "Optional name or partial name of the workspace (e.g. 'Meriton_POC'). If omitted, searches across all accessible workspaces."
                },
                "model_name": {
                    "type": "string",
                    "description": "Optional name or partial name of the semantic model/dataset."
                }
            }
        }
    }
]

async def fetch_master_ontology(token: str | None = None) -> str:
    """Returns the Master Ontology markdown. Reads from local bundled file or OneLake."""
    # 1. Primary: Read bundled ontology file inside the container
    if LOCAL_ONTOLOGY_PATH.exists():
        return LOCAL_ONTOLOGY_PATH.read_text(encoding="utf-8")

    # 2. Fallback: Query OneLake DFS if local file is missing
    url = os.environ.get("ONELAKE_ONTOLOGY_URL", DEFAULT_ONTOLOGY_URL)
    headers = {
        "Authorization": f"Bearer {token}",
        "x-ms-version": "2020-10-02"
    }
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code == 200:
                return resp.text
            return f"Notice: Could not fetch remote ontology from OneLake (HTTP {resp.status_code})."
    except Exception as exc:
        return f"Notice: Could not reach OneLake DFS ({str(exc)})."

async def list_user_workspaces(token: str) -> List[Dict[str, Any]]:
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(f"{PBI_API_BASE}/groups", headers=headers)
            if resp.status_code != 200:
                return [{"error": f"Power BI API returned HTTP {resp.status_code}: {resp.text}"}]
            data = resp.json()
            return [
                {"id": g.get("id"), "name": g.get("name"), "isReadOnly": g.get("isReadOnly", False)}
                for g in data.get("value", [])
            ]
    except Exception as exc:
        return [{"error": f"Failed to list workspaces: {str(exc)}"}]

async def search_datasets(token: str, workspace_name: str | None = None, model_name: str | None = None) -> List[Dict[str, Any]]:
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    workspaces = await list_user_workspaces(token)

    if workspace_name:
        workspaces = [w for w in workspaces if isinstance(w, dict) and workspace_name.lower() in w.get("name", "").lower()]

    results = []
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            for ws in workspaces:
                if not isinstance(ws, dict):
                    continue
                ws_id = ws.get("id")
                ws_title = ws.get("name")
                if not ws_id:
                    continue

                resp = await client.get(f"{PBI_API_BASE}/groups/{ws_id}/datasets", headers=headers)
                if resp.status_code == 200:
                    datasets = resp.json().get("value", [])
                    for d in datasets:
                        d_name = d.get("name", "")
                        if model_name and model_name.lower() not in d_name.lower():
                            continue
                        results.append({
                            "dataset_id": d.get("id"),
                            "dataset_name": d_name,
                            "workspace_id": ws_id,
                            "workspace_name": ws_title,
                            "description": d.get("description", "")
                        })
    except Exception as exc:
        results.append({"error": f"Search encountered an issue: {str(exc)}"})
    return results
