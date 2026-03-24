// automation-account.bicep — Azure Automation Account for nightly Fabric capacity suspend
// Provisions the account and managed identity only.
// The runbook script, schedule, and job schedule link are created by hooks/postprovision.ps1
// so that the schedule start time can be computed correctly at deploy time.
//
// Auth: system-assigned managed identity is granted Contributor on the Fabric capacity
// resource, which is the minimum role required to call suspend/resume actions.
// See ADR-021 in docs/decisions.md.

@description('Automation Account resource name')
param name string

@description('Azure region')
param location string

@description('Name of the Fabric capacity resource in this resource group')
param fabricCapacityName string

@description('Resource tags')
param tags object = {}

// Contributor role — minimum built-in role with Microsoft.Fabric/capacities/suspend/action
var contributorRole = 'b24988ac-6180-42a0-ab88-20f7382dd24c'

resource automationAccount 'Microsoft.Automation/automationAccounts@2023-11-01' = {
  name: name
  location: location
  tags: tags
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    sku: {
      name: 'Basic'
    }
    publicNetworkAccess: true
    disableLocalAuth: false
  }
}

// Reference the existing Fabric capacity to scope the role assignment
resource fabricCapacity 'Microsoft.Fabric/capacities@2023-11-01' existing = {
  name: fabricCapacityName
}

// Grant Contributor to the Automation Account's managed identity on the Fabric capacity.
// Contributor is required to call suspend/resume on Microsoft.Fabric/capacities.
// The managed identity has no access to SQL, OpenAI, Content Safety, or application data.
// Role assignment name uses `name` (the automation account name) as the deterministic seed —
// principalId is a runtime value and cannot be used here (BCP120).
resource fabricContributorRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(fabricCapacity.id, name, contributorRole)
  scope: fabricCapacity
  properties: {
    principalId: automationAccount.identity.principalId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', contributorRole)
    principalType: 'ServicePrincipal'
  }
}

output name string = automationAccount.name
output id string = automationAccount.id
output principalId string = automationAccount.identity.principalId
