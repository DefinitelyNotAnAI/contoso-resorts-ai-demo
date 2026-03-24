<#
.SYNOPSIS
    Suspends the Contoso Resorts Fabric F64 capacity.

.DESCRIPTION
    Runs nightly at 05:00 UTC (midnight ET) via Azure Automation.
    Authenticates using the Automation Account system-assigned managed identity.
    Calls the Azure Resource Manager Fabric suspend REST API.
    Safe to run if the capacity is already suspended — exits cleanly with HTTP 409.

    Costs prevented: ~$167/day when F64 is left running (ADR-021).

.PARAMETER SubscriptionId
    Azure subscription containing the Fabric capacity.

.PARAMETER ResourceGroupName
    Resource group containing the Fabric capacity.

.PARAMETER CapacityName
    Name of the Fabric capacity resource (e.g. fc<token>).
#>

param (
    [Parameter(Mandatory = $true)]
    [string]$SubscriptionId,

    [Parameter(Mandatory = $true)]
    [string]$ResourceGroupName,

    [Parameter(Mandatory = $true)]
    [string]$CapacityName
)

# ── Authenticate via system-assigned managed identity ─────────────────────────
Write-Output "Authenticating via managed identity..."
Connect-AzAccount -Identity | Out-Null
Write-Output "Authentication successful."

# ── Build suspend URI ─────────────────────────────────────────────────────────
$uri = "https://management.azure.com/subscriptions/$SubscriptionId" +
       "/resourceGroups/$ResourceGroupName" +
       "/providers/Microsoft.Fabric/capacities/$CapacityName" +
       "/suspend?api-version=2023-11-01"

Write-Output "Suspending Fabric capacity '$CapacityName' in '$ResourceGroupName'..."

# ── Call Fabric suspend REST API ──────────────────────────────────────────────
try {
    $token   = (Get-AzAccessToken -ResourceUrl "https://management.azure.com/").Token
    $headers = @{
        "Authorization" = "Bearer $token"
        "Content-Type"  = "application/json"
    }

    Invoke-RestMethod -Method POST -Uri $uri -Headers $headers | Out-Null
    Write-Output "SUCCESS: Fabric capacity '$CapacityName' has been suspended."

} catch {
    $statusCode = $null
    if ($_.Exception.Response) {
        $statusCode = [int]$_.Exception.Response.StatusCode
    }

    if ($statusCode -eq 409) {
        # 409 Conflict = capacity is already in the target state (suspended)
        Write-Output "INFO: Fabric capacity '$CapacityName' is already suspended — no action needed."
    } else {
        Write-Error "FAILED to suspend Fabric capacity '$CapacityName'. HTTP $statusCode. Error: $_"
        throw
    }
}
