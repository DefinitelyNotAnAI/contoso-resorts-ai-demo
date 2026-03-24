# hooks/postprovision.ps1 — Post-provision hook for azd
# Orchestrates: Fabric workspace/DB setup → schema DDL → seed data → app config

$ErrorActionPreference = "Stop"

Write-Host "=== Contoso Resorts AI — Post-provision ===" -ForegroundColor Cyan

# ---------------------------------------------------------------------------
# 0. Gather azd outputs
# ---------------------------------------------------------------------------
$rgName   = azd env get-value AZURE_RESOURCE_GROUP
$location = azd env get-value AZURE_LOCATION
$capacityName = azd env get-value FABRIC_CAPACITY_NAME
$appName  = azd env get-value APP_SERVICE_NAME 2>$null

$subId = azd env get-value AZURE_SUBSCRIPTION_ID 2>$null

Write-Host "Resource Group : $rgName"
Write-Host "Fabric Capacity: $capacityName"
if ($appName) { Write-Host "App Service    : $appName" }
else { Write-Host "App Service    : (not deployed — running locally)" }

# ---------------------------------------------------------------------------
# 0.5. Resume Fabric capacity so workspace setup + DDL can connect
#      The capacity may be suspended from a prior session or created suspended.
#      We suspend it again after seeding (Step 2) to keep costs low.
# ---------------------------------------------------------------------------
Write-Host "`n--- Step 0.5: Resume Fabric capacity for provisioning ---" -ForegroundColor Yellow

if ($capacityName -and $subId) {
    $resumeUri = "https://management.azure.com/subscriptions/$subId/resourceGroups/$rgName" +
                 "/providers/Microsoft.Fabric/capacities/$capacityName/resume?api-version=2023-11-01"

    Write-Host "Resuming Fabric capacity '$capacityName'..."
    $resumeResult = az rest --method POST --url $resumeUri 2>&1
    if ($LASTEXITCODE -ne 0 -and $resumeResult -notmatch "already") {
        # A 409 means it's already Active — that's fine
        if ($resumeResult -match "409" -or $resumeResult -match "already") {
            Write-Host "Fabric capacity is already Active — no action needed." -ForegroundColor DarkYellow
        } else {
            Write-Host "WARNING: Could not resume Fabric capacity. Continuing — capacity may already be Active." -ForegroundColor Yellow
            Write-Host $resumeResult
        }
    } else {
        Write-Host "Resume request sent. Waiting 60s for capacity to become Active..." -ForegroundColor Green
        Start-Sleep -Seconds 60
    }
} else {
    Write-Host "SKIP: FABRIC_CAPACITY_NAME or AZURE_SUBSCRIPTION_ID not set" -ForegroundColor DarkYellow
}

# ---------------------------------------------------------------------------
# 1. Run Fabric setup (workspace + SQL Database)
#    Skip if FABRIC_WORKSPACE_ID already set — workspace exists from prior run.
#    Suspended Fabric capacities also cause Fabric API calls to fail; re-running
#    fabric_setup.py when the workspace already exists is a no-op at best.
# ---------------------------------------------------------------------------
Write-Host "`n--- Step 1: Fabric workspace + SQL Database ---" -ForegroundColor Yellow

$existingWorkspaceId = azd env get-value FABRIC_WORKSPACE_ID 2>$null
if ($existingWorkspaceId) {
    Write-Host "SKIP: Fabric workspace already provisioned (ID: $existingWorkspaceId)" -ForegroundColor DarkYellow
    Write-Host "      Delete FABRIC_WORKSPACE_ID from azd env to force re-creation."
} else {
    $fabricSetupScript = Join-Path $PSScriptRoot ".." "database" "fabric_setup.py"
    if (Test-Path $fabricSetupScript) {
        python $fabricSetupScript --capacity-name $capacityName --location $location
        if ($LASTEXITCODE -ne 0) {
            Write-Host "ERROR: fabric_setup.py failed" -ForegroundColor Red
            exit 1
        }
    } else {
        Write-Host "SKIP: fabric_setup.py not found (manual Fabric setup required)" -ForegroundColor DarkYellow
    }
}

# ---------------------------------------------------------------------------
# 2. Run schema DDL + seed data
# ---------------------------------------------------------------------------
Write-Host "`n--- Step 2: Schema DDL + seed data ---" -ForegroundColor Yellow

$postprovisionPy = Join-Path $PSScriptRoot ".." "database" "postprovision.py"

if (Test-Path $postprovisionPy) {
    python $postprovisionPy
    if ($LASTEXITCODE -ne 0) {
        Write-Host "ERROR: postprovision.py failed" -ForegroundColor Red
        exit 1
    }
} else {
    Write-Host "SKIP: postprovision.py not found" -ForegroundColor DarkYellow
}

# ---------------------------------------------------------------------------
# 2.5. Re-suspend Fabric capacity now that seeding is complete
#      Keeps costs low (~$167/day) until next demo. The Automation Account
#      will also suspend nightly as a safety net (ADR-021).
# ---------------------------------------------------------------------------
Write-Host "`n--- Step 2.5: Re-suspend Fabric capacity ---" -ForegroundColor Yellow

if ($capacityName -and $subId) {
    $suspendUri = "https://management.azure.com/subscriptions/$subId/resourceGroups/$rgName" +
                  "/providers/Microsoft.Fabric/capacities/$capacityName/suspend?api-version=2023-11-01"

    Write-Host "Suspending Fabric capacity '$capacityName'..."
    $suspendResult = az rest --method POST --url $suspendUri 2>&1
    if ($LASTEXITCODE -ne 0) {
        if ($suspendResult -match "409") {
            Write-Host "Fabric capacity is already Suspended — no action needed." -ForegroundColor DarkYellow
        } else {
            Write-Host "WARNING: Could not suspend Fabric capacity. Suspend manually to avoid cost." -ForegroundColor Yellow
            Write-Host $suspendResult
        }
    } else {
        Write-Host "Fabric capacity suspended. Resume before next demo." -ForegroundColor Green
    }
} else {
    Write-Host "SKIP: FABRIC_CAPACITY_NAME or AZURE_SUBSCRIPTION_ID not set" -ForegroundColor DarkYellow
}

# ---------------------------------------------------------------------------
# 3. Update App Service configuration with SQL connection info
# ---------------------------------------------------------------------------
Write-Host "`n--- Step 3: Configure App Service ---" -ForegroundColor Yellow

$sqlServer   = azd env get-value SQL_SERVER 2>$null
$sqlDatabase = azd env get-value SQL_DATABASE 2>$null

if ($sqlServer -and $sqlDatabase -and $appName) {
    Write-Host "Setting SQL_SERVER=$sqlServer, SQL_DATABASE=$sqlDatabase on $appName"
    az webapp config appsettings set `
        --resource-group $rgName `
        --name $appName `
        --settings "SQL_SERVER=$sqlServer" "SQL_DATABASE=$sqlDatabase" `
        --output none

    # Set Fabric capacity info for CRM UI resume/suspend
    if ($subId) {
        az webapp config appsettings set `
            --resource-group $rgName `
            --name $appName `
            --settings `
                "FABRIC_CAPACITY_SUBSCRIPTION_ID=$subId" `
                "FABRIC_CAPACITY_RESOURCE_GROUP=$rgName" `
                "FABRIC_CAPACITY_NAME=$capacityName" `
            --output none
    }

    Write-Host "App Service configuration updated" -ForegroundColor Green
} else {
    Write-Host "SKIP: SQL_SERVER or APP_SERVICE_NAME not set" -ForegroundColor DarkYellow
}

# ---------------------------------------------------------------------------
# 4. Configure Azure Automation Account (nightly Fabric capacity suspend)
#    Creates the runbook from infra/scripts/suspend-fabric.ps1, publishes it,
#    creates a daily schedule at 05:00 UTC (midnight ET), and links them.
#    Idempotent — skips each step if already configured from a prior run.
#    See ADR-021 in docs/decisions.md.
# ---------------------------------------------------------------------------
Write-Host "`n--- Step 4: Configure Automation Account (nightly Fabric suspend) ---" -ForegroundColor Yellow

$aaName     = azd env get-value AUTOMATION_ACCOUNT_NAME 2>$null
$scriptPath = Join-Path $PSScriptRoot ".." "infra" "scripts" "suspend-fabric.ps1"

# ── Ensure the automation CLI extension is installed non-interactively ────────
Write-Host "Ensuring 'automation' CLI extension is installed..."
az extension add --name automation --yes --only-show-errors 2>$null
Write-Host "Extension ready."

# On re-runs (deployFabric=false), the Bicep output is empty but the account
# already exists in the resource group — look it up via az resource list
# (no extension required) and cache it.
if (-not $aaName) {
    Write-Host "AUTOMATION_ACCOUNT_NAME not in azd env — looking up in resource group '$rgName'..."
    $aaName = az resource list `
        --resource-group $rgName `
        --resource-type "Microsoft.Automation/automationAccounts" `
        --query "[0].name" --output tsv 2>$null
    if ($aaName) {
        Write-Host "Found existing Automation Account: $aaName" -ForegroundColor Green
        azd env set AUTOMATION_ACCOUNT_NAME $aaName
    }
}

if (-not $aaName) {
    Write-Host "SKIP: No Automation Account found in '$rgName' — Fabric auto-suspend not configured." -ForegroundColor DarkYellow
} elseif (-not (Test-Path $scriptPath)) {
    Write-Host "SKIP: suspend-fabric.ps1 not found at $scriptPath" -ForegroundColor DarkYellow
} else {
    $runbookName  = "suspend-fabric"
    $scheduleName = "nightly-midnight-et"

    # ── Runbook ────────────────────────────────────────────────────────────────
    $runbookExists = az automation runbook show `
        --automation-account-name $aaName `
        --resource-group $rgName `
        --name $runbookName `
        --query name --output tsv 2>$null

    if ($runbookExists) {
        Write-Host "Runbook '$runbookName' already exists — skipping creation."
    } else {
        Write-Host "Creating runbook '$runbookName'..."
        # Create the runbook shell (--runbook-file is not supported by this CLI extension version)
        az automation runbook create `
            --automation-account-name $aaName `
            --resource-group $rgName `
            --name $runbookName `
            --type PowerShell `
            --description "Suspends Fabric F64 capacity nightly at 05:00 UTC (midnight ET) to prevent ~`$167/day cost overrun. See ADR-021." `
            --output none
        if ($LASTEXITCODE -ne 0) {
            Write-Host "ERROR: Failed to create runbook '$runbookName'" -ForegroundColor Red; exit 1
        }

        # Upload the draft content via REST (az automation runbook replace-content not available)
        # Use @file syntax — passing content as inline --body string mangles PowerShell comment blocks (<# #>)
        Write-Host "Uploading runbook content from $scriptPath..."
        $draftUri = "https://management.azure.com/subscriptions/$subId" +
                    "/resourceGroups/$rgName" +
                    "/providers/Microsoft.Automation/automationAccounts/$aaName" +
                    "/runbooks/$runbookName/draft/content?api-version=2023-11-01"
        az rest --method PUT --url $draftUri `
            --body "@$scriptPath" `
            --headers "Content-Type=text/powershell" `
            --output none
        if ($LASTEXITCODE -ne 0) {
            Write-Host "ERROR: Failed to upload runbook content" -ForegroundColor Red; exit 1
        }

        Write-Host "Publishing runbook '$runbookName'..."
        az automation runbook publish `
            --automation-account-name $aaName `
            --resource-group $rgName `
            --name $runbookName `
            --output none
        if ($LASTEXITCODE -ne 0) {
            Write-Host "ERROR: Failed to publish runbook '$runbookName'" -ForegroundColor Red; exit 1
        }
        Write-Host "Runbook created and published." -ForegroundColor Green
    }

    # ── Schedule ───────────────────────────────────────────────────────────────
    # Compute next occurrence of 05:00 UTC (midnight ET)
    $now      = [DateTime]::UtcNow
    $nextRun  = [DateTime]::new($now.Year, $now.Month, $now.Day, 5, 0, 0, [DateTimeKind]::Utc)
    if ($now -ge $nextRun) { $nextRun = $nextRun.AddDays(1) }
    $startTime = $nextRun.ToString("yyyy-MM-ddTHH:mm:ssZ")

    $scheduleExists = az automation schedule show `
        --automation-account-name $aaName `
        --resource-group $rgName `
        --name $scheduleName `
        --query name --output tsv 2>$null

    if ($scheduleExists) {
        Write-Host "Schedule '$scheduleName' already exists — skipping creation."
    } else {
        Write-Host "Creating daily schedule '$scheduleName' (starts $startTime)..."
        az automation schedule create `
            --automation-account-name $aaName `
            --resource-group $rgName `
            --name $scheduleName `
            --frequency Day `
            --interval 1 `
            --start-time $startTime `
            --time-zone UTC `
            --description "Daily at 05:00 UTC (midnight ET) — suspend Fabric F64 capacity" `
            --output none
        if ($LASTEXITCODE -ne 0) {
            Write-Host "ERROR: Failed to create schedule '$scheduleName'" -ForegroundColor Red; exit 1
        }
        Write-Host "Schedule created." -ForegroundColor Green
    }

    # ── Job Schedule (link runbook to schedule) ────────────────────────────────
    # az automation job-schedule is not supported by this CLI extension version; use REST API directly
    $jsListUri = "https://management.azure.com/subscriptions/$subId/resourceGroups/$rgName" +
                 "/providers/Microsoft.Automation/automationAccounts/$aaName" +
                 "/jobSchedules?api-version=2023-11-01"
    $jobScheduleExists = az rest --method GET --url $jsListUri `
        --query "value[?properties.runbook.name=='$runbookName' && properties.schedule.name=='$scheduleName'].properties.jobScheduleId | [0]" `
        --output tsv 2>$null

    if ($jobScheduleExists) {
        Write-Host "Runbook '$runbookName' is already linked to schedule '$scheduleName' — skipping."
    } else {
        Write-Host "Linking runbook '$runbookName' to schedule '$scheduleName'..."
        $jobScheduleId = [System.Guid]::NewGuid().ToString()
        $jsCreateUri = "https://management.azure.com/subscriptions/$subId/resourceGroups/$rgName" +
                       "/providers/Microsoft.Automation/automationAccounts/$aaName" +
                       "/jobSchedules/$($jobScheduleId)?api-version=2023-11-01"
        # Write body to a temp file — avoids PowerShell/shell escaping issues with az rest --body
        $jsTempFile = [System.IO.Path]::GetTempFileName()
        @{
            properties = @{
                runbook    = @{ name = $runbookName }
                schedule   = @{ name = $scheduleName }
                parameters = @{
                    SubscriptionId    = $subId
                    ResourceGroupName = $rgName
                    CapacityName      = $capacityName
                }
            }
        } | ConvertTo-Json -Depth 5 -Compress | Set-Content -Path $jsTempFile -Encoding UTF8
        az rest --method PUT --url $jsCreateUri --body "@$jsTempFile" --output none
        Remove-Item $jsTempFile -ErrorAction SilentlyContinue
        if ($LASTEXITCODE -ne 0) {
            Write-Host "ERROR: Failed to link runbook to schedule" -ForegroundColor Red; exit 1
        }
        Write-Host "Runbook linked to schedule. Fabric capacity will suspend nightly at midnight ET." -ForegroundColor Green
    }
}

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------
Write-Host "`n=== Post-provision complete ===" -ForegroundColor Green

$appUrl = azd env get-value APP_SERVICE_URL 2>$null
if ($appUrl) {
    Write-Host "App URL: $appUrl" -ForegroundColor Cyan
}
