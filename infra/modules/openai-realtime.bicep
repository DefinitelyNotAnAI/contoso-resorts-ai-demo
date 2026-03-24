// openai-realtime.bicep — Azure OpenAI for GPT Realtime (eastus2, GlobalStandard SKU)
// ADR-015: separate resource required because GlobalStandard not available in eastus

@description('Resource name')
param name string

@description('Azure region (must support GlobalStandard)')
param location string

@description('Realtime model name')
param modelName string

@description('Model version')
param modelVersion string

@description('Resource tags')
param tags object = {}

resource openaiRealtime 'Microsoft.CognitiveServices/accounts@2024-10-01' = {
  name: name
  location: location
  tags: tags
  kind: 'OpenAI'
  sku: {
    name: 'S0'
  }
  properties: {
    customSubDomainName: name
    publicNetworkAccess: 'Enabled'
    disableLocalAuth: true
  }
}

resource deployment 'Microsoft.CognitiveServices/accounts/deployments@2024-10-01' = {
  parent: openaiRealtime
  name: modelName
  sku: {
    name: 'GlobalStandard'
    capacity: 1
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: modelName
      version: modelVersion
    }
  }
}

output name string = openaiRealtime.name
output endpoint string = 'https://${openaiRealtime.name}.openai.azure.com'
output id string = openaiRealtime.id
