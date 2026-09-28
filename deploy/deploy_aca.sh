#!/usr/bin/env bash
# Deploy the MCP OBO gateway to Azure Container Apps, one step at a time.
# Run each block, check its output, then continue. Needs: az CLI, logged in (az login),
# an Azure SUBSCRIPTION (a Fabric trial alone is not an Azure subscription).
set -euo pipefail

# ---- 0. Fill these in -------------------------------------------------------
RG="rg-mcp-obo"
LOCATION="eastus"
ACA_ENV="aca-env-mcp-obo"
APP="mcp-obo-gateway"
KV="kv-mcp-obo-$RANDOM"          # must be globally unique; note the value printed below
TENANT_ID="<tenant-id>"
API_CLIENT_ID="<api-app-client-id>"

echo "Key Vault name: $KV  (save this)"

# ---- 1. Resource group + Container Apps extension ---------------------------
az extension add --name containerapp --upgrade -y
az provider register --namespace Microsoft.App --wait
az provider register --namespace Microsoft.OperationalInsights --wait
az group create -n "$RG" -l "$LOCATION" -o table
# CHECK: provisioningState = Succeeded

# ---- 2. Key Vault holding the API app's client secret -----------------------
az keyvault create -n "$KV" -g "$RG" -l "$LOCATION" --enable-rbac-authorization true -o table
ME=$(az ad signed-in-user show --query id -o tsv)
az role assignment create --role "Key Vault Secrets Officer" --assignee "$ME" \
  --scope "$(az keyvault show -n "$KV" --query id -o tsv)" -o table
read -r -s -p "Paste the API app client secret (input hidden): " SECRET; echo
az keyvault secret set --vault-name "$KV" -n api-client-secret --value "$SECRET" -o none
unset SECRET
# CHECK: az keyvault secret show --vault-name "$KV" -n api-client-secret --query id -o tsv

# ---- 3. Build + deploy (builds the image in Azure; creates ACR + env) ---------
# First deploy uses a placeholder PUBLIC_BASE_URL; step 5 fixes it once we know the FQDN.
az containerapp up -n "$APP" -g "$RG" -l "$LOCATION" --environment "$ACA_ENV" \
  --source . --ingress external --target-port 8000 \
  --env-vars TENANT_ID="$TENANT_ID" API_CLIENT_ID="$API_CLIENT_ID" \
             API_APP_ID_URI="api://$API_CLIENT_ID" PUBLIC_BASE_URL="https://placeholder" \
             API_CLIENT_SECRET="pending-keyvault"
FQDN=$(az containerapp show -n "$APP" -g "$RG" --query properties.configuration.ingress.fqdn -o tsv)
echo "FQDN: $FQDN"
# CHECK: curl -s https://$FQDN/health   -> {"status":"ok"}  (app may crash-loop until step 4)

# ---- 4. Managed identity -> Key Vault secret reference ------------------------
az containerapp identity assign -n "$APP" -g "$RG" --system-assigned -o table
PRINCIPAL=$(az containerapp show -n "$APP" -g "$RG" --query identity.principalId -o tsv)
az role assignment create --role "Key Vault Secrets User" --assignee-object-id "$PRINCIPAL" \
  --assignee-principal-type ServicePrincipal \
  --scope "$(az keyvault show -n "$KV" --query id -o tsv)" -o table
SECRET_URI=$(az keyvault secret show --vault-name "$KV" -n api-client-secret --query id -o tsv)
az containerapp secret set -n "$APP" -g "$RG" \
  --secrets "api-client-secret=keyvaultref:$SECRET_URI,identityref:system"

# ---- 5. Point env at the secret + real public URL ----------------------------
az containerapp update -n "$APP" -g "$RG" \
  --set-env-vars API_CLIENT_SECRET=secretref:api-client-secret PUBLIC_BASE_URL="https://$FQDN" \
  -o table
# NOTE: `az containerapp update --set-env-vars` MERGES (unlike gcloud --set-env-vars).

# ---- 6. Verify ---------------------------------------------------------------
curl -s "https://$FQDN/health"; echo
curl -s "https://$FQDN/.well-known/oauth-protected-resource"; echo
curl -s -i -X POST "https://$FQDN/mcp" -H 'content-type: application/json' -d '{}' | head -5
# CHECK: health ok; metadata shows your tenant; /mcp returns 401 with WWW-Authenticate.
echo "MCP endpoint for clients: https://$FQDN/mcp"

# Logs:  az containerapp logs show -n "$APP" -g "$RG" --follow
