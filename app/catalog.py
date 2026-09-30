import httpx
from typing import List, Dict, Any

PBI_API_BASE = "https://api.powerbi.com/v1.0/myorg"

DISCOVERY_TOOLS = [
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
        "description": "Searches for semantic models (datasets) across accessible workspaces. Returns dataset names, dataset IDs (GUIDs), and workspace details so they can be inspected with GetSemanticModelSchema and queried with ExecuteQuery.",
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
