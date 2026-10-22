from functools import lru_cache

from azure.identity import (
    AzureCliCredential,
    AzureDeveloperCliCredential,
    ChainedTokenCredential,
    ManagedIdentityCredential,
    get_bearer_token_provider,
)

from .config import settings

COGNITIVE_SCOPE = "https://cognitiveservices.azure.com/.default"


@lru_cache(maxsize=1)
def credential():
    """Identidad administrada en Azure; Azure CLI / azd en local. Nunca claves."""
    if settings.client_id:
        return ManagedIdentityCredential(client_id=settings.client_id)
    return ChainedTokenCredential(
        AzureCliCredential(process_timeout=30),
        AzureDeveloperCliCredential(process_timeout=30),
    )


@lru_cache(maxsize=1)
def project_client():
    from azure.ai.projects import AIProjectClient

    return AIProjectClient(endpoint=settings.project_endpoint, credential=credential())


@lru_cache(maxsize=1)
def agents_openai():
    """Cliente OpenAI del proyecto Foundry (Responses + Conversations con agentes)."""
    return project_client().get_openai_client(timeout=180.0, max_retries=2)


@lru_cache(maxsize=1)
def models_openai():
    """Cliente Azure OpenAI de la cuenta Foundry para los modelos de voz."""
    from openai import AzureOpenAI

    return AzureOpenAI(
        azure_endpoint=settings.openai_endpoint,
        api_version=settings.openai_api_version,
        azure_ad_token_provider=get_bearer_token_provider(credential(), COGNITIVE_SCOPE),
        timeout=120.0,
        max_retries=3,
    )
