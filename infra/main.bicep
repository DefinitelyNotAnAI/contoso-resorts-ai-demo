// main.bicep — Contoso Resorts AI Infrastructure Orchestrator
// Provisions: Fabric Capacity (F64), Azure OpenAI (x2), Content Safety, App Service
// Usage: azd up (orchestrated via azure.yaml)

targetScope = 'subscription'

// =============================================================================
// Parameters
// =============================================================================
@description('Primary Azure region for all resources')
param location string

@description('Environment name (e.g., crsai1)')
param environmentName string

@description('UPN of the Fabric capacity admin (must be a native Entra ID account)')
param fabricAdminUpn string

@description('Azure OpenAI model for text/SQL generation')
param openaiModel string = 'gpt-4o-mini'

@description('Azure OpenAI model for realtime voice')
param realtimeModel string = 'gpt-realtime'

@description('Realtime resource region (GlobalStandard SKU availability)')
param realtimeLocation string = 'eastus2'

@description('App Service region — P0v3 quota may differ by region from primary location')
param appServiceLocation string = 'eastus'

@description('Set to false on re-runs when Fabric capacity already exists (suspended capacity rejects ARM updates)')
param deployFabric bool = true

@description('Existing Fabric capacity name — used when deployFabric=false. Read from cached azd env.')
param existingFabricCapacityName string = ''

@description('Existing Fabric capacity resource ID — used when deployFabric=false. Read from cached azd env.')
param existingFabricCapacityId string = ''

// =============================================================================
// Variables
// =============================================================================
var abbrs = loadJsonContent('abbreviations.json')
var resourceToken = toLower(uniqueString(subscription().id, environmentName, location))
var tags = {
  'azd-env-name': environmentName
  project: 'contoso-resorts-ai'
}
// True when a Fabric capacity is available — either just provisioned or pre-existing.
// Used to gate resources that depend on the capacity (e.g. Automation Account).
var hasFabricCapacity = deployFabric || existingFabricCapacityName != ''
var effectiveFabricCapacityName = deployFabric ? fabricCapacity.outputs.name : existingFabricCapacityName

// =============================================================================
// Resource Group
// =============================================================================
resource rg 'Microsoft.Resources/resourceGroups@2024-03-01' = {
  name: '${abbrs.resourceGroup}${environmentName}'
  location: location
  tags: tags
}

// =============================================================================
// Fabric Capacity (F64) — created in SUSPENDED state to minimize cost
// Deploy only on first run. On re-runs (deployFabric=false), the suspended
// capacity cannot accept ARM updates — use cached name/ID from azd env instead.
// =============================================================================
module fabricCapacity 'modules/fabric-capacity.bicep' = if (deployFabric) {
  name: 'fabric-capacity'
  scope: rg
  params: {
    name: '${abbrs.fabricCapacity}${resourceToken}'
    location: location
    adminUpn: fabricAdminUpn
    sku: 'F64'
    tags: tags
  }
}

// =============================================================================
// Azure OpenAI — Primary (gpt-4o-mini for text/SQL)
// =============================================================================
module openai 'modules/openai.bicep' = {
  name: 'openai-primary'
  scope: rg
  params: {
    name: '${abbrs.openai}${resourceToken}'
    location: location
    modelName: openaiModel
    modelVersion: '2024-07-18'
    deploymentCapacity: 30
    tags: tags
  }
}

// =============================================================================
// Azure OpenAI — Realtime (gpt-4o-realtime in eastus2)
// =============================================================================
module openaiRealtime 'modules/openai-realtime.bicep' = {
  name: 'openai-realtime'
  scope: rg
  params: {
    name: '${abbrs.openai}rt-${resourceToken}'
    location: realtimeLocation
    modelName: realtimeModel
    modelVersion: '2025-08-28'
    tags: tags
  }
}

// =============================================================================
// Azure Content Safety
// =============================================================================
module contentSafety 'modules/content-safety.bicep' = {
  name: 'content-safety'
  scope: rg
  params: {
    name: '${abbrs.contentSafety}${resourceToken}'
    location: location
    tags: tags
  }
}

// =============================================================================
// App Service — Python backend (Premium V3 — P0v3)
// See ADR-020 in docs/decisions.md (supersedes ADR-019)
// =============================================================================
module appService 'modules/app-service.bicep' = {
  name: 'app-service'
  scope: rg
  params: {
    name: '${abbrs.appService}${resourceToken}'
    location: appServiceLocation
    openaiEndpoint: openai.outputs.endpoint
    openaiModel: openaiModel
    realtimeEndpoint: openaiRealtime.outputs.endpoint
    realtimeModel: realtimeModel
    contentSafetyEndpoint: contentSafety.outputs.endpoint
    tags: tags
  }
}

module roleAssignments 'modules/role-assignments.bicep' = {
  name: 'role-assignments'
  scope: rg
  params: {
    principalId: appService.outputs.principalId
    openaiName: openai.outputs.name
    openaiRealtimeName: openaiRealtime.outputs.name
    contentSafetyName: contentSafety.outputs.name
  }
}

// =============================================================================
// Azure Automation Account — nightly Fabric capacity suspend (ADR-021)
// Deployed whenever a Fabric capacity exists (fresh or pre-existing).
// On first run deployFabric=true; on re-runs deployFabric=false but
// existingFabricCapacityName is set — hasFabricCapacity covers both cases.
// Runbook script, schedule, and job schedule link are wired by postprovision.ps1.
// =============================================================================
module automationAccount 'modules/automation-account.bicep' = if (hasFabricCapacity) {
  name: 'automation-account'
  scope: rg
  params: {
    name: '${abbrs.automationAccount}${resourceToken}'
    location: location
    fabricCapacityName: effectiveFabricCapacityName
    tags: tags
  }
}

// =============================================================================
// Outputs (consumed by azd + postprovision hooks)
// =============================================================================
output AZURE_RESOURCE_GROUP string = rg.name
output AZURE_LOCATION string = location
output FABRIC_CAPACITY_NAME string = deployFabric ? fabricCapacity.outputs.name : existingFabricCapacityName
output FABRIC_CAPACITY_ID string = deployFabric ? fabricCapacity.outputs.id : existingFabricCapacityId
output AI_FOUNDRY_ENDPOINT string = openai.outputs.endpoint
output AI_FOUNDRY_MODEL string = openaiModel
output REALTIME_ENDPOINT string = openaiRealtime.outputs.endpoint
output REALTIME_MODEL string = realtimeModel
output CONTENT_SAFETY_ENDPOINT string = contentSafety.outputs.endpoint
output APP_SERVICE_NAME string = appService.outputs.name
output APP_SERVICE_URL string = appService.outputs.url
output APP_SERVICE_PRINCIPAL_ID string = appService.outputs.principalId
output AUTOMATION_ACCOUNT_NAME string = hasFabricCapacity ? automationAccount.outputs.name : ''
