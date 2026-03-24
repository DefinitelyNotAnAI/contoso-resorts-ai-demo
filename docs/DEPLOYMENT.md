# Deployment Guide — Contoso Resorts AI Demo

This guide walks through deploying Contoso Resorts AI to your Azure subscription using the Azure Developer CLI (`azd`). Total time: approximately 15–20 minutes.

---

## Prerequisites

Before you start, make sure you have:

| Requirement | Install / Verify |
|-------------|-----------------|
| Azure subscription | [Create one free](https://azure.microsoft.com/free/) |
| Azure Developer CLI | `winget install Microsoft.Azd` or [install guide](https://aka.ms/azd) |
| Azure CLI | `winget install Microsoft.AzureCLI` or [install guide](https://aka.ms/azcliinstall) |
| Python 3.11+ | `winget install Python.Python.3.11` |
| ODBC Driver 18 for SQL Server | [Download](https://learn.microsoft.com/sql/connect/odbc/download-odbc-driver-for-sql-server) |
| Entra ID UPN | Your `user@tenant.onmicrosoft.com` — used to administer Fabric capacity |

**Permissions required:**
- Azure subscription **Contributor** or **Owner** (to create resource groups and assign RBAC roles)
- Microsoft Fabric [capacity admin](https://learn.microsoft.com/fabric/admin/capacity-settings) (can be the same user)

---

## Step 1: Clone and Log In

```powershell
git clone https://github.com/microsoft/contoso-resorts-ai-demo
cd contoso-resorts-ai-demo

# Log into both Azure CLI and azd
az login
azd auth login
```

---

## Step 2: Deploy

```powershell
azd up
```

You will be prompted for:
- **Environment name** — a short name (e.g., `crsai1`) used to prefix all resource names
- **Azure subscription** — select from the list
- **Azure region** — `eastus2` is recommended (required for Fabric)
- **Admin UPN** *(if not auto-detected)* — your Entra ID UPN for Fabric capacity administration

`azd up` then:
1. Creates a resource group `rg-{env-name}`
2. Provisions all Azure resources (Fabric F64, Azure OpenAI ×2, Content Safety, App Service, Automation Account)
3. Sets up RBAC — grants the App Service managed identity access to all AI resources
4. Resumes Fabric capacity, creates the Fabric workspace and SQL Database
5. Runs schema DDL and loads ~25K rows of seed data (guests, bookings, experiences, surveys)
6. Configures App Service environment variables
7. Sets up an Automation Account runbook to suspend Fabric nightly at midnight ET (cost control)
8. Deploys the FastAPI backend to App Service
9. **Re-suspends Fabric capacity**

The deployed URL is printed at the end.

---

## Step 3: Verify

Open the URL printed by `azd up`. You should see the Contoso Resorts CRM:

1. Select a persona (Dana, Anne, or Victor)
2. Click **Analyze Guest** — the AI pipeline runs and returns recommendations
3. Click **Start Call** — a voice conversation opens using GPT-4o Realtime

If the page loads but **Analyze Guest** fails, the most common cause is Fabric capacity being suspended. See [Troubleshooting](#troubleshooting) below.

---

## Running the Demo

**Three personas, three story arcs:**

| Persona | Loyalty | Start here |
|---------|---------|-----------|
| Dana Lakehouse | Platinum | "I want to book a trip this weekend" |
| Anne Thropic | Gold | Show her daughter Emma's upcoming birthday, ask AI what to do |
| Victor Storr | Silver | "I need to change my booking" — the AI recommends experiences when guest has to travel to a location they do not like |

Full walk-through: [demo-script.md](demo-script.md)

---

## Running Locally (After `azd up`)

After deployment, you can run the backend locally against the same Azure resources:

```powershell
# Install Python dependencies
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# Copy env vars from azd
azd env get-values | ForEach-Object { $_ } | Out-File .env -Encoding utf8

# Run
uvicorn api:app --reload --port 8000
# Open http://localhost:8000
```

---

## Cost Management

**Fabric capacity (F64) is the only significant cost — ~$7/hour when active.**

The deployment automatically:
- Creates the Fabric capacity in a **suspended** state
- Resumes it only during `azd up` (for database setup)
- Re-suspends it after setup completes
- Schedules an Automation Account runbook to suspend it nightly at 05:00 UTC (midnight ET)

**Before running a demo session:**
```powershell
# Resume Fabric capacity (takes ~1-2 minutes to become Active)
$sub  = azd env get-value AZURE_SUBSCRIPTION_ID
$rg   = azd env get-value AZURE_RESOURCE_GROUP
$name = azd env get-value FABRIC_CAPACITY_NAME

az rest --method POST --url "https://management.azure.com/subscriptions/$sub/resourceGroups/$rg/providers/Microsoft.Fabric/capacities/$name/resume?api-version=2023-11-01"
```

**After your demo session:**
```powershell
# Suspend Fabric capacity to stop billing
az rest --method POST --url "https://management.azure.com/subscriptions/$sub/resourceGroups/$rg/providers/Microsoft.Fabric/capacities/$name/suspend?api-version=2023-11-01"
```

You can also resume/suspend from the [Azure Portal](https://portal.azure.com) → Fabric Capacity resource → **Resume / Pause**.

---

## Re-deploying

If you need to update the app code after initial deployment:

```powershell
# Re-deploy only the app (skips infrastructure provisioning)
azd deploy
```

If you need to update infrastructure:

```powershell
# Full re-provision + deploy (automatically skips Fabric re-creation)
azd up
```

> `azd up` is idempotent for Fabric: once the workspace is created, it skips Fabric setup on subsequent runs.

---

## Troubleshooting

### "Analyze Guest" returns an error

The Fabric SQL Database is unreachable. Most likely cause: Fabric capacity is suspended.

1. Resume Fabric capacity (see [Cost Management](#cost-management) above)
2. Wait 1-2 minutes for it to become Active
3. Try again

### `azd up` fails during Fabric setup

Fabric capacity must be Active before workspace setup. The postprovision script resumes it automatically, but if it times out:

```powershell
# Check Fabric capacity state
az resource show \
  --resource-group $(azd env get-value AZURE_RESOURCE_GROUP) \
  --resource-type "Microsoft.Fabric/capacities" \
  --name $(azd env get-value FABRIC_CAPACITY_NAME) \
  --query "properties.state"

# If not Active, resume manually then re-run postprovision
azd env set FABRIC_CAPACITY_NAME <your-capacity-name>
azd provision   # re-runs hooks including postprovision
```

### Voice call doesn't connect

The GPT-4o Realtime model requires the dedicated Realtime Azure OpenAI resource. Check:

1. `REALTIME_ENDPOINT` is set in App Settings (`azd env get-values | grep REALTIME`)
2. The App Service managed identity has `Cognitive Services User` role on the Realtime resource

```powershell
# Check App Service environment variables
az webapp config appsettings list \
  --name $(azd env get-value APP_SERVICE_NAME) \
  --resource-group $(azd env get-value AZURE_RESOURCE_GROUP) \
  --query "[].{name:name, value:value}" -o table
```

### Can't log in to Fabric API during setup

Make sure the UPN used for `AZURE_ADMIN_UPN` has been added as a capacity admin in the [Fabric Admin Portal](https://app.fabric.microsoft.com/admin/capacities).

---

## Teardown

To remove all Azure resources:

```powershell
azd down
```

This deletes the resource group and all resources inside it. The Fabric workspace and SQL Database are deleted along with the capacity. This action cannot be undone.

---

## Environment Variables Reference

All variables are set automatically by `azd up`. Reference only if running locally or troubleshooting:

| Variable | Description | Set by |
|----------|-------------|--------|
| `AI_FOUNDRY_ENDPOINT` | Azure OpenAI endpoint (gpt-4o-mini) | `azd up` → Bicep output |
| `AI_FOUNDRY_MODEL` | Model deployment name | `azd up` |
| `REALTIME_ENDPOINT` | Azure OpenAI Realtime endpoint | `azd up` → Bicep output |
| `REALTIME_MODEL` | Realtime model deployment name | `azd up` |
| `CONTENT_SAFETY_ENDPOINT` | Content Safety API endpoint | `azd up` → Bicep output |
| `SQL_SERVER` | Fabric SQL Database server hostname | `azd up` → postprovision |
| `SQL_DATABASE` | Fabric SQL Database name | `azd up` → postprovision |
| `FABRIC_CAPACITY_SUBSCRIPTION_ID` | Subscription containing Fabric capacity | `azd up` → postprovision |
| `FABRIC_CAPACITY_RESOURCE_GROUP` | Resource group of Fabric capacity | `azd up` → postprovision |
| `FABRIC_CAPACITY_NAME` | Fabric capacity name (for resume/suspend UI) | `azd up` → postprovision |
