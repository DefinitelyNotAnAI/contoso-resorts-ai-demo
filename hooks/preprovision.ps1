# hooks/preprovision.ps1 — Pre-provision hook for azd
# Captures the admin UPN for Fabric capacity administration and validates prerequisites.

$ErrorActionPreference = "Stop"

Write-Host "=== Contoso Resorts AI — Pre-provision ===" -ForegroundColor Cyan

# ---------------------------------------------------------------------------
# 1. Capture admin UPN (required for Fabric capacity admin)
# ---------------------------------------------------------------------------
$upn = $null
$rawUpn = azd env get-value AZURE_ADMIN_UPN 2>$null
if ($LASTEXITCODE -eq 0 -and $rawUpn -and $rawUpn -notmatch "^ERROR:") {
    $upn = $rawUpn
}
if (-not $upn) {
    # Try to get the UPN from the current Azure CLI login
    $account = az account show --query "user.name" -o tsv 2>$null
    if ($account -and $account -match "@") {
        $upn = $account
        Write-Host "Using Azure CLI UPN: $upn" -ForegroundColor Green
    } else {
        $upn = Read-Host "Enter your Entra ID UPN (e.g., user@tenant.onmicrosoft.com)"
    }
    azd env set AZURE_ADMIN_UPN $upn
}
Write-Host "Fabric admin UPN: $upn"

# ---------------------------------------------------------------------------
# 2. Validate Azure CLI is logged in
# ---------------------------------------------------------------------------
$sub = az account show --query "id" -o tsv 2>$null
if (-not $sub) {
    Write-Host "ERROR: Not logged into Azure CLI. Run 'az login' first." -ForegroundColor Red
    exit 1
}
Write-Host "Azure subscription: $sub"

# ---------------------------------------------------------------------------
# 3. Set default location if not set
# ---------------------------------------------------------------------------
$location = azd env get-value AZURE_LOCATION 2>$null
if (-not $location) {
    azd env set AZURE_LOCATION "eastus2"
    Write-Host "Set default location: eastus2"
}

# ---------------------------------------------------------------------------
# 4. Auto-disable Fabric re-deployment if capacity already exists
#    Suspended Fabric capacities reject ARM PUT updates — skip re-deploy
# ---------------------------------------------------------------------------
$fabricId = $null
$rawFabricId = azd env get-value FABRIC_CAPACITY_ID 2>$null
if ($LASTEXITCODE -eq 0 -and $rawFabricId -and $rawFabricId -notmatch "^ERROR:") {
    $fabricId = $rawFabricId
}
if ($fabricId) {
    azd env set DEPLOY_FABRIC "false"
    Write-Host "Fabric capacity already provisioned — skipping re-deploy (DEPLOY_FABRIC=false)" -ForegroundColor Yellow
} else {
    azd env set DEPLOY_FABRIC "true"
    Write-Host "Fabric capacity not yet provisioned — will deploy (DEPLOY_FABRIC=true)"
}

Write-Host "=== Pre-provision complete ===" -ForegroundColor Green
