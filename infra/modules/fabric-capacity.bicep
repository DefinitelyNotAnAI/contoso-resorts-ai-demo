// fabric-capacity.bicep — Microsoft Fabric Capacity (F64)
// Created in SUSPENDED state to minimize cost. Resumed by postprovision hook.

@description('Capacity resource name')
param name string

@description('Azure region')
param location string

@description('UPN of the capacity admin (must be native Entra ID account)')
param adminUpn string

@description('Fabric SKU (F2, F4, F8, F16, F32, F64)')
param sku string = 'F64'

@description('Resource tags')
param tags object = {}

resource fabricCapacity 'Microsoft.Fabric/capacities@2023-11-01' = {
  name: name
  location: location
  tags: tags
  sku: {
    name: sku
    tier: 'Fabric'
  }
  properties: {
    administration: {
      members: [
        adminUpn
      ]
    }
  }
}

// NOTE: Fabric capacities are created in Active state by default.
// The postprovision hook will handle suspend/resume as needed.

output name string = fabricCapacity.name
output id string = fabricCapacity.id
