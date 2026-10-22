targetScope = 'resourceGroup'

param storageName string
param siteName string = 'resource-simulator-web'
param storageLocation string = 'japaneast'
param siteLocation string = 'eastasia'
param sourceBranch string = 'codex/stage1-local-worker'
param repositoryUrl string = 'https://github.com/tatsuya01a1/resource-simulator'

var tags = { project: 'resource-simulator', managedBy: 'bicep' }
resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: storageName
  location: storageLocation
  tags: tags
  kind: 'StorageV2'
  sku: { name: 'Standard_LRS' }
  properties: {
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
    allowBlobPublicAccess: false
    allowSharedKeyAccess: true // read-only, entity-scoped SAS is server-side only
    publicNetworkAccess: 'Enabled' // HTTPS API with Entra/SAS, never anonymous
    networkAcls: { defaultAction: 'Allow', bypass: 'None' }
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
  location: siteLocation
  tags: tags
  sku: { name: 'Free', tier: 'Free' }
  properties: {
    repositoryUrl: repositoryUrl
    branch: sourceBranch
    buildProperties: {
      appLocation: 'web'
      apiLocation: 'api'
      appArtifactLocation: 'dist'
      skipGithubActionWorkflowGeneration: true
    }
  }
}
output tableEndpoint string = storage.properties.primaryEndpoints.table
output tableScope string = current.id
output hostname string = site.properties.defaultHostname
output siteId string = site.id
