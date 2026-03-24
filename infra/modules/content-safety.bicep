// content-safety.bicep — Azure Content Safety (ADR-004)

@description('Resource name')
param name string

@description('Azure region')
param location string

@description('Resource tags')
param tags object = {}

resource contentSafety 'Microsoft.CognitiveServices/accounts@2024-10-01' = {
  name: name
  location: location
  tags: tags
  kind: 'ContentSafety'
  sku: {
    name: 'S0'
  }
  properties: {
    customSubDomainName: name
    publicNetworkAccess: 'Enabled'
    disableLocalAuth: true
  }
}

output name string = contentSafety.name
output endpoint string = 'https://${contentSafety.name}.cognitiveservices.azure.com'
output id string = contentSafety.id
