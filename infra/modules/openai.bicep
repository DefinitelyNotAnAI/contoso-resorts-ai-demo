// openai.bicep — Azure OpenAI (primary: gpt-4o-mini for text/SQL)

@description('Resource name')
param name string

@description('Azure region')
param location string

@description('Model name to deploy')
param modelName string

@description('Model version')
param modelVersion string

@description('Deployment capacity in thousands of tokens per minute')
param deploymentCapacity int = 30

@description('Resource tags')
param tags object = {}

resource openai 'Microsoft.CognitiveServices/accounts@2024-10-01' = {
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
  parent: openai
  name: modelName
  sku: {
    name: 'Standard'
    capacity: deploymentCapacity
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: modelName
      version: modelVersion
    }
  }
}

output name string = openai.name
output endpoint string = 'https://${openai.name}.openai.azure.com'
output id string = openai.id
