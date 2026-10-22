targetScope = 'subscription'

@minLength(1)
@maxLength(64)
@description('Nombre del entorno de azd (se usa para generar nombres únicos).')
param environmentName string

@minLength(1)
@description('Región de Azure. Probado en swedencentral (todos los modelos disponibles con GlobalStandard).')
@metadata({
  azd: {
    type: 'location'
  }
})
param location string

@description('Nombre del grupo de recursos que se crea.')
param resourceGroupName string = 'RG-chatbot-interacciones'

@description('Región de Azure AI Search. Va aparte porque Search se queda a veces sin capacidad en swedencentral (InsufficientResourcesAvailable).')
param searchLocation string = 'spaincentral'

@description('Object ID de quien despliega (azd lo rellena). Recibe permisos para desarrollo local.')
param principalId string = ''

@allowed([
  'User'
  'ServicePrincipal'
  'Group'
])
param principalType string = 'User'

@description('Imagen del contenedor web (azd la rellena tras el primer despliegue).')
param webImageName string = ''

@description('Modelo de texto principal del agente (capacidad en miles de tokens por minuto).')
param textModelName string = 'gpt-5.4'
param textModelVersion string = '2026-03-05'
param textModelCapacity int = 200

@description('Modelo de texto alternativo (más ligero y barato) seleccionable por pestaña.')
param altModelName string = 'gpt-5.4-mini'
param altModelVersion string = '2026-03-17'
param altModelCapacity int = 200

@description('Modelo de voz a texto (transcripción).')
param sttModelName string = 'gpt-4o-mini-transcribe'
param sttModelVersion string = '2025-12-15'
param sttModelCapacity int = 50

@description('Modelo de texto a voz (salida de audio).')
param ttsModelName string = 'gpt-audio-mini'
param ttsModelVersion string = '2025-12-15'
param ttsModelCapacity int = 20

@description('Habilita /api/simulate para sembrar conversaciones de demo.')
param simulationEnabled bool = true

@description('Graba el contenido de los mensajes en las trazas de Foundry (desactivado por privacidad).')
param captureMessageContent bool = false

var tags = {
  'azd-env-name': environmentName
  project: 'chatbot-interacciones'
  SecurityControl: 'Ignore'
}

resource rg 'Microsoft.Resources/resourceGroups@2024-03-01' = {
  name: resourceGroupName
  location: location
  tags: tags
}

module resources 'resources.bicep' = {
  name: 'chatbot-interacciones-resources'
  scope: rg
  params: {
    location: location
    searchLocation: empty(searchLocation) ? location : searchLocation
    tags: tags
    environmentName: environmentName
    principalId: principalId
    principalType: principalType
    webImageName: webImageName
    textModelName: textModelName
    textModelVersion: textModelVersion
    textModelCapacity: textModelCapacity
    altModelName: altModelName
    altModelVersion: altModelVersion
    altModelCapacity: altModelCapacity
    sttModelName: sttModelName
    sttModelVersion: sttModelVersion
    sttModelCapacity: sttModelCapacity
    ttsModelName: ttsModelName
    ttsModelVersion: ttsModelVersion
    ttsModelCapacity: ttsModelCapacity
    simulationEnabled: simulationEnabled
    captureMessageContent: captureMessageContent
  }
}

output AZURE_LOCATION string = location
output AZURE_TENANT_ID string = tenant().tenantId
output AZURE_RESOURCE_GROUP string = rg.name
output AZURE_CONTAINER_REGISTRY_ENDPOINT string = resources.outputs.registryLoginServer
output AZURE_CONTAINER_REGISTRY_NAME string = resources.outputs.registryName
output AZURE_CONTAINER_ENVIRONMENT_NAME string = resources.outputs.containerEnvironmentName
output SERVICE_WEB_NAME string = resources.outputs.webAppName
output SERVICE_WEB_URI string = resources.outputs.webUri
output FOUNDRY_ACCOUNT_NAME string = resources.outputs.foundryAccountName
output FOUNDRY_PROJECT_NAME string = resources.outputs.foundryProjectName
output FOUNDRY_PROJECT_ENDPOINT string = resources.outputs.foundryProjectEndpoint
output AZURE_OPENAI_ENDPOINT string = resources.outputs.openAIEndpoint
output TEXT_MODEL_DEFAULT string = textModelName
output TEXT_MODEL_ALT string = altModelName
output STT_DEPLOYMENT string = sttModelName
output TTS_DEPLOYMENT string = ttsModelName
output SEARCH_ENDPOINT string = resources.outputs.searchEndpoint
output SEARCH_INDEX string = resources.outputs.searchIndexName
output SEARCH_CONNECTION_NAME string = resources.outputs.searchConnectionName
output SEARCH_CONNECTION_ID string = resources.outputs.searchConnectionId
output STORAGE_BLOB_ENDPOINT string = resources.outputs.storageBlobEndpoint
output STORAGE_CONTAINER string = resources.outputs.storageContainerName
output APPINSIGHTS_RESOURCE_ID string = resources.outputs.appInsightsId
output APPINSIGHTS_NAME string = resources.outputs.appInsightsName
