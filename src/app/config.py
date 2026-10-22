import os
from dataclasses import dataclass


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _flag(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None or not value.strip():
        return default
    return value.strip().lower() in ("1", "true", "yes", "si", "sí", "on")


@dataclass(frozen=True)
class Settings:
    project_endpoint: str = _env("FOUNDRY_PROJECT_ENDPOINT")
    openai_endpoint: str = _env("AZURE_OPENAI_ENDPOINT")
    openai_api_version: str = _env("AZURE_OPENAI_API_VERSION", "2025-04-01-preview")
    text_model_default: str = _env("TEXT_MODEL_DEFAULT", "gpt-5.4")
    text_model_alt: str = _env("TEXT_MODEL_ALT", "gpt-5.4-mini")
    stt_deployment: str = _env("STT_DEPLOYMENT", "gpt-4o-mini-transcribe")
    tts_deployment: str = _env("TTS_DEPLOYMENT", "gpt-audio-mini")
    assistant_voice: str = _env("ASSISTANT_VOICE", "alloy")
    reasoning_effort: str = _env("AGENT_REASONING_EFFORT", "low")
    # Tope de llamadas a herramientas por respuesta: evita bucles de búsquedas que agotan los TPM.
    max_tool_calls: int = int(_env("AGENT_MAX_TOOL_CALLS", "3") or 3)
    agent_prefix: str = _env("AGENT_NAME_PREFIX", "asistente-ciudadano")
    web_search_country: str = _env("WEB_SEARCH_COUNTRY", "ES")
    web_search_city: str = _env("WEB_SEARCH_CITY", "Madrid")
    web_search_region: str = _env("WEB_SEARCH_REGION", "Madrid")
    search_endpoint: str = _env("SEARCH_ENDPOINT")
    search_index: str = _env("SEARCH_INDEX", "servicios-ciudadanos")
    search_connection_name: str = _env("SEARCH_CONNECTION_NAME", "aisearch")
    search_connection_id: str = _env("SEARCH_CONNECTION_ID")
    storage_blob_endpoint: str = _env("STORAGE_BLOB_ENDPOINT")
    storage_container: str = _env("STORAGE_CONTAINER", "chatbot")
    local_data_dir: str = _env("LOCAL_DATA_DIR", ".localdata")
    appinsights_resource_id: str = _env("APPINSIGHTS_RESOURCE_ID")
    appinsights_name: str = _env("APPINSIGHTS_NAME")
    appinsights_connection_string: str = _env("APPLICATIONINSIGHTS_CONNECTION_STRING")
    service_name: str = _env("OTEL_SERVICE_NAME", "chatbot-interacciones")
    capture_content: bool = _flag("OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT", False)
    client_id: str = _env("AZURE_CLIENT_ID")
    tenant_id: str = _env("AZURE_TENANT_ID")
    foundry_instrumentor: bool = _flag("FOUNDRY_INSTRUMENTOR", True)
    simulation_enabled: bool = _flag("SIMULATION_ENABLED", True)
    max_turns_per_hour: int = int(_env("MAX_TURNS_PER_HOUR", "600") or 600)
    max_recording_seconds: int = int(_env("MAX_RECORDING_SECONDS", "60") or 60)
    max_tts_chars: int = int(_env("MAX_TTS_CHARS", "700") or 700)

    @property
    def project_name(self) -> str:
        return self.project_endpoint.rstrip("/").rsplit("/", 1)[-1] if self.project_endpoint else ""

    @property
    def account_name(self) -> str:
        host = self.project_endpoint.split("//", 1)[-1].split(".", 1)[0] if self.project_endpoint else ""
        return host


settings = Settings()

# Modelos de texto seleccionables por pestaña. Cada uno tiene su propio agente en Foundry.
TEXT_MODELS = {
    "principal": {
        "key": "principal",
        "deployment": settings.text_model_default,
        "agent": f"{settings.agent_prefix}-principal",
        "label": f"{settings.text_model_default} (principal)",
    },
    "ligero": {
        "key": "ligero",
        "deployment": settings.text_model_alt,
        "agent": f"{settings.agent_prefix}-ligero",
        "label": f"{settings.text_model_alt} (ligero)",
    },
}
DEFAULT_MODEL_KEY = "principal"
