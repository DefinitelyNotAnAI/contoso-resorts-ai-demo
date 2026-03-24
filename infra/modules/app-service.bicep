// app-service.bicep — App Service for backend hosting (Linux, Python 3.11)

@description('App Service name')
param name string

@description('Azure region')
param location string

@description('Azure OpenAI endpoint (primary)')
param openaiEndpoint string

@description('Azure OpenAI model name')
param openaiModel string

@description('Azure OpenAI Realtime endpoint')
param realtimeEndpoint string

@description('Realtime model name')
param realtimeModel string

@description('Content Safety endpoint')
param contentSafetyEndpoint string

@description('Resource tags')
param tags object = {}

var planName = '${name}-plan'

resource appServicePlan 'Microsoft.Web/serverfarms@2023-12-01' = {
  name: planName
  location: location
  tags: tags
  kind: 'linux'
  sku: {
    name: 'P0v3'
    tier: 'PremiumV3'
  }
  properties: {
    reserved: true  // Linux
  }
}

resource appService 'Microsoft.Web/sites@2023-12-01' = {
  name: name
  location: location
  tags: union(tags, {
    'azd-service-name': 'backend'
  })
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    serverFarmId: appServicePlan.id
    httpsOnly: true
    siteConfig: {
      linuxFxVersion: 'PYTHON|3.11'
      alwaysOn: true
      webSocketsEnabled: true
      ftpsState: 'Disabled'
      appCommandLine: 'bash startup.sh'
      appSettings: [
        { name: 'AI_FOUNDRY_ENDPOINT', value: openaiEndpoint }
        { name: 'AI_FOUNDRY_MODEL', value: openaiModel }
        { name: 'REALTIME_ENDPOINT', value: realtimeEndpoint }
        { name: 'REALTIME_MODEL', value: realtimeModel }
        { name: 'REALTIME_API_VERSION', value: '2024-10-01-preview' }
        { name: 'CONTENT_SAFETY_ENDPOINT', value: contentSafetyEndpoint }
        { name: 'USE_DATA_AGENT', value: 'false' }
        // SQL_SERVER and SQL_DATABASE are set by postprovision after Fabric setup
        { name: 'SCM_DO_BUILD_DURING_DEPLOYMENT', value: 'true' }
        { name: 'ENABLE_ORYX_BUILD', value: 'true' }
      ]
    }
  }
}

output name string = appService.name
output url string = 'https://${appService.properties.defaultHostName}'
output principalId string = appService.identity.principalId
output id string = appService.id
