targetScope = 'resourceGroup'
param storageName string
param siteName string = 'resource-simulator-history-test'
var tags = { project: 'resource-simulator', purpose: 'history-validation' }
resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: storageName
  location: 'japaneast'
  tags: tags
  kind: 'StorageV2'
  sku: { name: 'Standard_LRS' }
  properties: {
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
    allowBlobPublicAccess: false
    allowSharedKeyAccess: true
  }
}
resource tables 'Microsoft.Storage/storageAccounts/tableServices@2023-05-01' = {
  parent: storage
  name: 'default'
  properties: {}
}
resource current 'Microsoft.Storage/storageAccounts/tableServices/tables@2023-05-01' = {
  parent: tables
  name: 'CurrentState'
  properties: {}
}
resource site 'Microsoft.Web/staticSites@2023-12-01' = {
  name: siteName
  location: 'eastasia'
  tags: tags
  sku: { name: 'Free', tier: 'Free' }
  properties: {}
}
output tableEndpoint string = storage.properties.primaryEndpoints.table
output tableScope string = current.id
output hostname string = site.properties.defaultHostname
output siteId string = site.id
