using './main.bicep'

param location = readEnvironmentVariable('AZURE_LOCATION', 'eastus2')
param environmentName = readEnvironmentVariable('AZURE_ENV_NAME', 'crsai1')
param fabricAdminUpn = readEnvironmentVariable('AZURE_ADMIN_UPN', '')
param realtimeLocation = readEnvironmentVariable('REALTIME_LOCATION', 'eastus2')
param appServiceLocation = readEnvironmentVariable('APP_SERVICE_LOCATION', 'eastus')
param deployFabric = readEnvironmentVariable('DEPLOY_FABRIC', 'true') == 'true'
param existingFabricCapacityName = readEnvironmentVariable('FABRIC_CAPACITY_NAME', '')
param existingFabricCapacityId = readEnvironmentVariable('FABRIC_CAPACITY_ID', '')
