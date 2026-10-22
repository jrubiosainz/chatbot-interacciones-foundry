"""Agentes de Microsoft Foundry y ejecución de un turno de conversación.

Se crean dos agentes (uno por modelo de texto) con las mismas herramientas:
- Azure AI Search sobre el índice de servicios municipales (conexión del proyecto).
- Búsqueda web (Web Search de Foundry, basada en Bing).
"""

import hashlib
import json
import logging
import re
import time
from functools import lru_cache

from .clients import agents_openai, project_client
from .config import TEXT_MODELS, settings
from .search_setup import KB_PATH
from .telemetry import model_span, record_tool_call, set_usage

log = logging.getLogger("chatbot.foundry")

INSTRUCTIONS = """Eres el asistente virtual de atención ciudadana del ayuntamiento de «Ciudad Demo», un municipio ficticio que se usa para demostraciones.

Normas:
- Responde siempre en español de España, con un tono cercano, claro y respetuoso.
- Todo lo municipal (trámites, ayudas, servicios, horarios, tasas, requisitos, oficinas, transporte municipal y actividades del ayuntamiento) se responde SOLO con el índice de servicios municipales (Azure AI Search). Consúltalo siempre para estas preguntas. Ciudad Demo no existe en internet: nunca busques en la web sobre ella.
- Usa la búsqueda web solo para información actual y ajena al ayuntamiento: el tiempo, noticias, eventos o transporte en tiempo real en Madrid, normativa estatal o autonómica e indicadores como el IPREM. Haz como máximo UNA búsqueda web por respuesta y no abras más páginas si los resultados ya bastan.
- Para saludos, agradecimientos o resúmenes de lo ya hablado no uses herramientas.
- Si el índice no tiene la información, dilo con claridad y recomienda el teléfono 010 o la sede electrónica. No inventes datos.
- No pidas ni almacenes datos personales sensibles.
- Sé breve: como máximo unas 120 palabras. Usa viñetas cuando ayuden.
- Si el mensaje empieza por «[voz]», el ciudadano está hablando por voz y tu respuesta se leerá en voz alta: contesta en 2 o 3 frases cortas, sin markdown, sin listas, sin tablas y sin URLs."""

# Marcadores de cita en línea que añade el servicio, p. ej. «【50:0†source】».
_CITATION_MARKER = re.compile(r"[ \t]*【[^】]*】")

AGENT_STATE: dict = {"agents": {}, "error": None}

_TOOL_LABELS = {
    "azure_ai_search": "AI Search",
    "web_search": "Bing (Web Search)",
    "bing_grounding": "Bing (Grounding)",
    "file_search": "File Search",
    "code_interpreter": "Code Interpreter",
}


def _search_connection_id() -> str:
    try:
        connection = project_client().connections.get(settings.search_connection_name)
        if getattr(connection, "id", None):
            return connection.id
    except Exception as exc:  # noqa: BLE001
        log.warning("No se pudo leer la conexión '%s': %s", settings.search_connection_name, exc)
    if settings.search_connection_id:
        return settings.search_connection_id
    raise RuntimeError("No se encuentra la conexión de Azure AI Search en el proyecto Foundry.")


def _tools(connection_id: str) -> list:
    from azure.ai.projects.models import (
        AISearchIndexResource,
        AzureAISearchTool,
        AzureAISearchToolResource,
        WebSearchApproximateLocation,
        WebSearchTool,
    )

    return [
        AzureAISearchTool(
            azure_ai_search=AzureAISearchToolResource(
                indexes=[
                    AISearchIndexResource(
                        project_connection_id=connection_id,
                        index_name=settings.search_index,
                        query_type="simple",
                        top_k=4,
                    )
                ]
            )
        ),
        WebSearchTool(
            user_location=WebSearchApproximateLocation(
                country=settings.web_search_country,
                city=settings.web_search_city,
                region=settings.web_search_region,
            ),
            search_context_size="low",
        ),
    ]


def ensure_agents() -> dict:
    """Crea o actualiza (solo si cambió la definición) un agente por modelo de texto."""
    from azure.ai.projects.models import PromptAgentDefinition, Reasoning
    from azure.core.exceptions import HttpResponseError, ResourceNotFoundError

    client = project_client()
    connection_id = _search_connection_id()
    result = {}
    for key, model in TEXT_MODELS.items():
        spec = {
            "model": model["deployment"],
            "instructions": INSTRUCTIONS,
            "search": [connection_id, settings.search_index, "simple", 4],
            "web": [settings.web_search_country, settings.web_search_city, settings.web_search_region, "low"],
            "effort": settings.reasoning_effort,
        }
        def_hash = hashlib.sha256(json.dumps(spec, sort_keys=True).encode("utf-8")).hexdigest()[:16]
        try:
            latest = client.agents.get(model["agent"]).versions.latest
            if (latest.metadata or {}).get("def_hash") == def_hash:
                result[key] = {"name": model["agent"], "version": latest.version, "model": model["deployment"]}
                continue
        except ResourceNotFoundError:
            pass

        metadata = {"def_hash": def_hash, "app": "chatbot-interacciones", "model_key": key}
        description = f"Asistente ciudadano de demostración ({model['deployment']})"
        kwargs = {"model": model["deployment"], "instructions": INSTRUCTIONS, "tools": _tools(connection_id)}
        try:
            definition = PromptAgentDefinition(
                **kwargs, reasoning=Reasoning(effort=settings.reasoning_effort) if settings.reasoning_effort else None
            )
            version = client.agents.create_version(
                agent_name=model["agent"], definition=definition, metadata=metadata, description=description
            )
        except HttpResponseError as exc:
            if "reasoning" not in str(exc).lower():
                raise
            log.warning("El modelo %s no acepta 'reasoning'; se crea el agente sin él.", model["deployment"])
            version = client.agents.create_version(
                agent_name=model["agent"],
                definition=PromptAgentDefinition(**kwargs),
                metadata=metadata,
                description=description,
            )
        log.info("Agente %s versión %s", model["agent"], version.version)
        result[key] = {"name": model["agent"], "version": version.version, "model": model["deployment"]}
    AGENT_STATE.update(agents=result, error=None)
    return result


def _classify_tool(item_type: str) -> tuple[str, str]:
    base = item_type[: -len("_call")] if item_type.endswith("_call") else item_type
    if "azure_ai_search" in base:
        base = "azure_ai_search"
    elif "web_search" in base:
        base = "web_search"
    elif "bing" in base:
        base = "bing_grounding"
    return base, _TOOL_LABELS.get(base, base)


def _tool_query(item: dict) -> str | None:
    action = item.get("action")
    if isinstance(action, dict) and action.get("query"):
        return str(action["query"])
    arguments = item.get("arguments")
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except ValueError:
            return arguments[:200]
    if isinstance(arguments, dict):
        return str(arguments.get("query") or arguments.get("search") or "") or None
    return None


def _norm(text) -> str:
    return " ".join(str(text or "").lower().split())


@lru_cache(maxsize=1)
def _kb_urls() -> dict[str, str]:
    try:
        documents = json.loads(KB_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {_norm(doc.get("title")): doc["url"] for doc in documents if doc.get("title") and doc.get("url")}


def _citation(annotation: dict) -> dict | None:
    """Las citas de AI Search llegan con la URL del servicio de búsqueda: se sustituye por la del documento."""
    url = annotation.get("url") or ""
    title = annotation.get("title") or ""
    kb_url = _kb_urls().get(_norm(title))
    if kb_url:
        return {"title": title, "url": kb_url, "source": "kb"}
    if ".search.windows.net" in url or not url.startswith(("http://", "https://")):
        return None
    return {"title": title or url, "url": url, "source": "web"}


def parse_response(data: dict) -> tuple[str, list[dict], list[dict]]:
    texts: list[str] = []
    tools: list[dict] = []
    citations: list[dict] = []
    seen_urls: set[str] = set()
    for item in data.get("output") or []:
        item_type = item.get("type") or ""
        if item_type == "message":
            for part in item.get("content") or []:
                if part.get("type") not in ("output_text", "text"):
                    continue
                texts.append(_CITATION_MARKER.sub("", part.get("text") or ""))
                for annotation in part.get("annotations") or []:
                    if annotation.get("type") != "url_citation":
                        continue
                    citation = _citation(annotation)
                    if citation and citation["url"] not in seen_urls:
                        seen_urls.add(citation["url"])
                        citations.append(citation)
        elif item_type.endswith("_call") and item_type not in ("function_call", "custom_tool_call"):
            tool_type, label = _classify_tool(item_type)
            tools.append(
                {"type": tool_type, "label": label, "status": item.get("status"), "query": _tool_query(item)}
            )
    return "\n".join(t for t in texts if t).strip(), tools, citations


def run_agent(persona, state: dict, text: str, model_key: str, modality: str) -> dict:
    """Envía el mensaje al agente del modelo elegido dentro de la conversación del usuario."""
    from openai import NotFoundError

    model = TEXT_MODELS[model_key]
    client = agents_openai()
    conversations = state.setdefault("conversations", {})
    agent_input = f"[voz] {text}" if modality == "voz" else text

    for attempt in range(2):
        conversation_id = conversations.get(model_key)
        if not conversation_id:
            conversation = client.conversations.create(
                metadata={"app": "chatbot-interacciones", "user_id": persona.id, "model": model_key}
            )
            conversation_id = conversation.id
            conversations[model_key] = conversation_id
        try:
            with model_span(
                "chat",
                model["deployment"],
                "texto",
                **{
                    "gen_ai.agent.name": model["agent"],
                    "gen_ai.conversation.id": conversation_id,
                    "app.model.key": model_key,
                },
            ) as span:
                started = time.perf_counter()
                extra_body = {"agent_reference": {"name": model["agent"], "type": "agent_reference"}}
                if settings.max_tool_calls > 0:
                    extra_body["max_tool_calls"] = settings.max_tool_calls
                response = client.responses.create(conversation=conversation_id, input=agent_input, extra_body=extra_body)
                latency_ms = int((time.perf_counter() - started) * 1000)
                data = response.model_dump(mode="json")
                usage = data.get("usage") or {}
                set_usage(
                    span,
                    usage.get("input_tokens"),
                    usage.get("output_tokens"),
                    **{"gen_ai.response.id": data.get("id"), "gen_ai.response.model": data.get("model")},
                )
                if data.get("status") == "failed":
                    raise RuntimeError(f"El agente devolvió un error: {data.get('error')}")
                reply, tools, citations = parse_response(data)
                for tool in tools:
                    record_tool_call(tool["type"], tool["label"], tool["status"], tool["query"], model["agent"])
            break
        except NotFoundError:
            if attempt == 0:
                log.warning("Conversación %s no encontrada; se crea una nueva.", conversation_id)
                conversations.pop(model_key, None)
                continue
            raise

    tool_counts: dict[str, int] = {}
    for tool in tools:
        tool_counts[tool["label"]] = tool_counts.get(tool["label"], 0) + 1
    details = usage.get("output_tokens_details") or {}
    return {
        "text": reply or "(sin respuesta)",
        "usage": {
            "input_tokens": usage.get("input_tokens"),
            "output_tokens": usage.get("output_tokens"),
            "reasoning_tokens": details.get("reasoning_tokens"),
        },
        "tools": [{"label": label, "count": count} for label, count in tool_counts.items()],
        "citations": citations[:6],
        "response_id": data.get("id"),
        "conversation_id": conversation_id,
        "agent": model["agent"],
        "deployment": model["deployment"],
        "latency_ms": latency_ms,
    }
