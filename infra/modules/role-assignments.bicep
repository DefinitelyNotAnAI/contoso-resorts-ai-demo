// role-assignments.bicep — RBAC for App Service managed identity
// Assigns Cognitive Services User on OpenAI + Content Safety resources

@description('Principal ID of the App Service managed identity')
param principalId string

@description('Name of the primary OpenAI resource')
param openaiName string

@description('Name of the Realtime OpenAI resource')
param openaiRealtimeName string

@description('Name of the Content Safety resource')
param contentSafetyName string

// Cognitive Services User role ID
var cognitiveServicesUserRole = 'a97b65f3-24c7-4388-baec-2e87135dc908'

// Reference existing resources
resource openai 'Microsoft.CognitiveServices/accounts@2024-10-01' existing = {
  name: openaiName
}

resource openaiRealtime 'Microsoft.CognitiveServices/accounts@2024-10-01' existing = {
  name: openaiRealtimeName
}

resource contentSafety 'Microsoft.CognitiveServices/accounts@2024-10-01' existing = {
  name: contentSafetyName
}

// Assign Cognitive Services User on primary OpenAI
resource openaiRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(openai.id, principalId, cognitiveServicesUserRole)
  scope: openai
  properties: {
    principalId: principalId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', cognitiveServicesUserRole)
    principalType: 'ServicePrincipal'
  }
}

// Assign Cognitive Services User on Realtime OpenAI
resource openaiRealtimeRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(openaiRealtime.id, principalId, cognitiveServicesUserRole)
  scope: openaiRealtime
  properties: {
    principalId: principalId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', cognitiveServicesUserRole)
    principalType: 'ServicePrincipal'
  }
}

// Assign Cognitive Services User on Content Safety
resource contentSafetyRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(contentSafety.id, principalId, cognitiveServicesUserRole)
  scope: contentSafety
  properties: {
    principalId: principalId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', cognitiveServicesUserRole)
    principalType: 'ServicePrincipal'
  }
}
