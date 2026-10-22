"""Telemetría OpenTelemetry exportada al Application Insights conectado al proyecto Foundry.

- El cuadro de mando cuenta SOLO los spans propios de la app (atributo ``app.kind``):
  ``turn`` (mensaje del usuario), ``model_call`` (llamada a un modelo) y ``tool_call``
  (AI Search / Bing). Siguen las convenciones semánticas GenAI de OpenTelemetry.
- Además se activa ``AIProjectInstrumentor`` (SDK de Foundry) para que las trazas
  oficiales de los agentes aparezcan en el portal de Foundry (Observabilidad > Trazas).
- Un ``SpanProcessor`` estampa ``app.user.id`` en todos los spans del turno.
"""

import contextvars
import logging
import os
import socket
from contextlib import contextmanager

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.trace.sampling import Decision, ParentBased, Sampler, SamplingResult
from opentelemetry.trace import SpanKind, Status, StatusCode

from .config import settings

log = logging.getLogger("chatbot.telemetry")

current_user: contextvars.ContextVar[dict | None] = contextvars.ContextVar("current_user", default=None)

STATE = {
    "exporting": False,
    "source": None,
    "foundry_instrumentor": False,
    "error": None,
}

_provider: TracerProvider | None = None
tracer = trace.get_tracer("chatbot-interacciones")


class UserAttributionProcessor(SpanProcessor):
    def on_start(self, span, parent_context=None):
        user = current_user.get()
        if user:
            span.set_attribute("app.user.id", user["id"])
            span.set_attribute("app.user.name", user["name"])
            span.set_attribute("enduser.id", user["id"])

    def on_end(self, span):
        return None

    def shutdown(self):
        return None

    def force_flush(self, timeout_millis: int = 30000):
        return True


class AppRootSampler(Sampler):
    """Solo conserva trazas cuya raíz es un turno de usuario (evita ruido de llamadas internas del SDK)."""

    def should_sample(self, parent_context, trace_id, name, kind=None, attributes=None, links=None, trace_state=None):
        if name.startswith("turno_usuario"):
            return SamplingResult(Decision.RECORD_AND_SAMPLE, attributes, trace_state)
        return SamplingResult(Decision.DROP, None, trace_state)

    def get_description(self) -> str:
        return "AppRootSampler"


def _resolve_connection_string() -> tuple[str | None, str | None]:
    # 1) La conexión App Insights del propio proyecto Foundry ("telemetría de Foundry").
    if settings.project_endpoint:
        try:
            from .clients import project_client

            cs = project_client().telemetry.get_application_insights_connection_string()
            if cs:
                return cs, "foundry-project-connection"
        except Exception as exc:  # noqa: BLE001
            log.warning("No se pudo leer la conexión App Insights del proyecto Foundry: %s", exc)
            STATE["error"] = f"foundry-connection: {exc.__class__.__name__}"
    # 2) Variable de entorno (mismo recurso, inyectado por Bicep).
    if settings.appinsights_connection_string:
        return settings.appinsights_connection_string, "env"
    return None, None


def setup_telemetry() -> None:
    global _provider
    if _provider is not None:
        return
    resource = Resource.create(
        {
            "service.name": settings.service_name,
            "service.instance.id": socket.gethostname(),
        }
    )
    provider = TracerProvider(resource=resource, sampler=ParentBased(root=AppRootSampler()))
    provider.add_span_processor(UserAttributionProcessor())

    connection_string, source = _resolve_connection_string()
    if connection_string:
        from azure.monitor.opentelemetry.exporter import AzureMonitorTraceExporter

        exporter = AzureMonitorTraceExporter(connection_string=connection_string)
        provider.add_span_processor(
            BatchSpanProcessor(exporter, schedule_delay_millis=2000, max_export_batch_size=256)
        )
        STATE.update(exporting=True, source=source)
        log.info("Telemetría exportando a Application Insights (fuente: %s)", source)
    else:
        log.warning("Sin cadena de conexión de Application Insights: la telemetría no se exporta.")

    trace.set_tracer_provider(provider)
    _provider = provider

    if not settings.foundry_instrumentor:
        return
    try:
        from azure.core.settings import settings as azure_settings

        azure_settings.tracing_implementation = "opentelemetry"
        os.environ.setdefault("AZURE_EXPERIMENTAL_ENABLE_GENAI_TRACING", "true")
        from azure.ai.projects.telemetry import AIProjectInstrumentor

        AIProjectInstrumentor().instrument(enable_content_recording=settings.capture_content)
        STATE["foundry_instrumentor"] = True
    except Exception as exc:  # noqa: BLE001
        log.warning("AIProjectInstrumentor no disponible: %s", exc)


def flush() -> None:
    if _provider is not None:
        try:
            _provider.force_flush(10000)
        except Exception:  # noqa: BLE001
            pass


@contextmanager
def user_context(persona):
    token = current_user.set({"id": persona.id, "name": persona.name})
    try:
        yield
    finally:
        current_user.reset(token)


@contextmanager
def turn_span(persona, modality: str, model_key: str, turn_id: str, simulated: bool):
    with tracer.start_as_current_span(f"turno_usuario {modality}", kind=SpanKind.INTERNAL) as span:
        span.set_attribute("app.kind", "turn")
        span.set_attribute("app.turn.id", turn_id)
        span.set_attribute("app.modality", modality)
        span.set_attribute("app.model.selected", model_key)
        span.set_attribute("app.simulated", simulated)
        span.set_attribute("app.turn.status", "running")
        try:
            yield span
            span.set_attribute("app.turn.status", "ok")
        except Exception as exc:
            span.set_attribute("app.turn.status", "error")
            span.record_exception(exc)
            span.set_status(Status(StatusCode.ERROR, str(exc)[:200]))
            raise


@contextmanager
def model_span(operation: str, deployment: str, model_type: str, *, counted: bool = True, **attributes):
    """Span de llamada a modelo (convenciones GenAI). ``counted=False`` lo excluye del cuadro."""
    with tracer.start_as_current_span(f"{operation} {deployment}", kind=SpanKind.CLIENT) as span:
        span.set_attribute("app.kind", "model_call" if counted else "synthetic")
        span.set_attribute("gen_ai.operation.name", operation)
        span.set_attribute("gen_ai.system", "azure.ai.openai")
        span.set_attribute("gen_ai.provider.name", "azure.ai.openai")
        span.set_attribute("gen_ai.request.model", deployment)
        span.set_attribute("app.model.type", model_type)
        for key, value in attributes.items():
            if value is not None:
                span.set_attribute(key, value)
        try:
            yield span
        except Exception as exc:
            span.record_exception(exc)
            span.set_status(Status(StatusCode.ERROR, str(exc)[:200]))
            raise


def set_usage(span, input_tokens, output_tokens, **extra) -> None:
    if input_tokens is not None:
        span.set_attribute("gen_ai.usage.input_tokens", int(input_tokens))
    if output_tokens is not None:
        span.set_attribute("gen_ai.usage.output_tokens", int(output_tokens))
    for key, value in extra.items():
        if value is not None:
            span.set_attribute(key, value)


def record_tool_call(tool_type: str, label: str, status: str | None, query: str | None, agent: str) -> None:
    with tracer.start_as_current_span(f"execute_tool {tool_type}", kind=SpanKind.INTERNAL) as span:
        span.set_attribute("app.kind", "tool_call")
        span.set_attribute("gen_ai.operation.name", "execute_tool")
        span.set_attribute("gen_ai.tool.name", tool_type)
        span.set_attribute("gen_ai.tool.type", "extension")
        span.set_attribute("gen_ai.agent.name", agent)
        span.set_attribute("app.tool.label", label)
        span.set_attribute("app.tool.status", status or "unknown")
        if query and settings.capture_content:
            span.set_attribute("app.tool.query", query[:300])
