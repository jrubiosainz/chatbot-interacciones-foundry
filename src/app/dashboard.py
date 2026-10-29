"""Cuadro de mando alimentado por la telemetría del proyecto Foundry.

Consulta con KQL el Application Insights conectado al proyecto (API de Azure Monitor Logs,
consulta centrada en el recurso) y agrega por usuario: mensajes (texto/voz), llamadas por
modelo y tipo de modelo, tokens y llamadas a herramientas (AI Search / Bing).
"""

import logging
import re
import threading
import time
from datetime import datetime, timedelta, timezone

import httpx

from .clients import credential
from .config import TEXT_MODELS, settings
from .personas import PERSONAS

log = logging.getLogger("chatbot.dashboard")

RANGES = {
    "1h": ("PT1H", timedelta(hours=1)),
    "24h": ("P1D", timedelta(days=1)),
    "7d": ("P7D", timedelta(days=7)),
    "30d": ("P30D", timedelta(days=30)),
}
DEFAULT_RANGE = "30d"
CACHE_SECONDS = 5
SAFE_ID = re.compile(r"^[a-z0-9_-]{1,40}$")

# Ojo: «kind» y «user» son palabras reservadas de KQL; por eso spanKind y userId.
QUERY_TEMPLATE = """{table}
| where {role} == "{service}"
| extend spanKind = tostring({props}["app.kind"]), userId = tostring({props}["app.user.id"])
| where spanKind in ("turn", "model_call", "tool_call") and isnotempty(userId){reset_filter}
| summarize calls = count(),
    tokensIn = sum(toint({props}["gen_ai.usage.input_tokens"])),
    tokensOut = sum(toint({props}["gen_ai.usage.output_tokens"])),
    lastSeen = max({time})
    by userId, spanKind,
    status = tostring({props}["app.turn.status"]),
    modality = tostring({props}["app.modality"]),
    model = tostring({props}["gen_ai.request.model"]),
    modelType = tostring({props}["app.model.type"]),
    operation = tostring({props}["gen_ai.operation.name"]),
    tool = tostring({props}["app.tool.label"])"""

SCHEMAS = {
    # Esquema clásico de Application Insights (dependencies / customDimensions).
    "classic": {"table": "dependencies", "role": "cloud_RoleName", "props": "customDimensions", "time": "timestamp"},
    # Esquema del área de trabajo de Log Analytics (AppDependencies / Properties).
    "workspace": {"table": "AppDependencies", "role": "AppRoleName", "props": "Properties", "time": "TimeGenerated"},
}

_lock = threading.Lock()
_cache: dict[tuple, tuple[float, dict]] = {}
_schema: dict[str, str | None] = {"name": None}


class QueryError(Exception):
    def __init__(self, status: int, text: str):
        super().__init__(f"{status}: {text[:300]}")
        self.status = status
        self.text = text


def _reset_filter(time_column: str, resets: dict[str, datetime] | None) -> str:
    """Cada usuario reiniciado solo cuenta la telemetría posterior a su último reinicio."""
    cases = [
        f'userId == "{user_id}", datetime({moment.astimezone(timezone.utc):%Y-%m-%dT%H:%M:%S.%fZ})'
        for user_id, moment in sorted((resets or {}).items())
        if SAFE_ID.match(user_id)
    ]
    if not cases:
        return ""
    return f"\n| where {time_column} >= case({', '.join(cases)}, datetime(2000-01-01))"


def build_query(schema: str = "classic", resets: dict[str, datetime] | None = None) -> str:
    fields = SCHEMAS[schema]
    return QUERY_TEMPLATE.format(
        service=settings.service_name, reset_filter=_reset_filter(fields["time"], resets), **fields
    )


def _post(kql: str, timespan: str) -> list[dict]:
    token = credential().get_token("https://api.loganalytics.io/.default").token
    url = f"https://api.loganalytics.io/v1{settings.appinsights_resource_id}/query"
    response = httpx.post(
        url,
        json={"query": kql, "timespan": timespan},
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    if response.status_code >= 400:
        raise QueryError(response.status_code, response.text)
    body = response.json()
    tables = body.get("tables") or []
    if not tables:
        return []
    columns = [column["name"] for column in tables[0]["columns"]]
    return [dict(zip(columns, row)) for row in tables[0]["rows"]]


def _run(timespan: str, resets: dict[str, datetime] | None = None) -> tuple[str, str, list[dict]]:
    order = [_schema["name"]] if _schema["name"] else ["classic", "workspace"]
    last_error: QueryError | None = None
    for name in order:
        kql = build_query(name, resets)
        try:
            rows = _post(kql, timespan)
            _schema["name"] = name
            return name, kql, rows
        except QueryError as exc:
            last_error = exc
            lowered = exc.text.lower()
            if exc.status == 400 and ("resolve" in lowered or "semantic" in lowered):
                continue
            raise
    raise last_error or QueryError(500, "Consulta sin resultado")


def model_catalog() -> dict[str, dict]:
    catalog = {}
    for model in TEXT_MODELS.values():
        catalog[model["deployment"]] = {"model": model["deployment"], "label": model["label"], "type": "texto"}
    catalog[settings.stt_deployment] = {
        "model": settings.stt_deployment,
        "label": f"{settings.stt_deployment} (voz→texto)",
        "type": "voz",
    }
    catalog[settings.tts_deployment] = {
        "model": settings.tts_deployment,
        "label": f"{settings.tts_deployment} (texto→voz)",
        "type": "voz",
    }
    return catalog


def portal_url() -> str | None:
    if not settings.appinsights_resource_id:
        return None
    tenant = f"@{settings.tenant_id}" if settings.tenant_id else ""
    return f"https://portal.azure.com/#{tenant}/resource{settings.appinsights_resource_id}/overview"


def _empty_user(persona) -> dict:
    return {
        "id": persona.id,
        "name": persona.name,
        "initials": persona.initials,
        "color": persona.color,
        "messages": 0,
        "messages_text": 0,
        "messages_voice": 0,
        "errors": 0,
        "model_calls": 0,
        "tokens_in": 0,
        "tokens_out": 0,
        "ai_search": 0,
        "bing": 0,
        "other_tools": 0,
        "last_seen": None,
        "models": {},
    }


def _aggregate(rows: list[dict], catalog: dict[str, dict]) -> tuple[list[dict], dict]:
    users = {persona.id: _empty_user(persona) for persona in PERSONAS}
    for row in rows:
        user = users.get(row.get("userId"))
        if user is None:
            continue
        calls = int(row.get("calls") or 0)
        tokens_in = int(row.get("tokensIn") or 0)
        tokens_out = int(row.get("tokensOut") or 0)
        kind = row.get("spanKind")
        if kind == "turn":
            if row.get("status") == "ok":
                user["messages"] += calls
                if row.get("modality") == "voz":
                    user["messages_voice"] += calls
                else:
                    user["messages_text"] += calls
            elif row.get("status") == "error":
                user["errors"] += calls
        elif kind == "model_call":
            model = row.get("model") or "desconocido"
            entry = user["models"].setdefault(
                model,
                {
                    "model": model,
                    "label": catalog.get(model, {}).get("label", model),
                    "type": row.get("modelType") or catalog.get(model, {}).get("type", "texto"),
                    "calls": 0,
                    "tokens_in": 0,
                    "tokens_out": 0,
                },
            )
            entry["calls"] += calls
            entry["tokens_in"] += tokens_in
            entry["tokens_out"] += tokens_out
            user["model_calls"] += calls
            user["tokens_in"] += tokens_in
            user["tokens_out"] += tokens_out
        elif kind == "tool_call":
            label = row.get("tool") or ""
            if label.startswith("AI Search"):
                user["ai_search"] += calls
            elif label.startswith("Bing"):
                user["bing"] += calls
            else:
                user["other_tools"] += calls
        last_seen = row.get("lastSeen")
        if last_seen and (user["last_seen"] is None or last_seen > user["last_seen"]):
            user["last_seen"] = last_seen

    ordered_models = list(catalog.values())
    known = {model["model"] for model in ordered_models}
    for user in users.values():
        for model, entry in user["models"].items():
            if model not in known:
                known.add(model)
                ordered_models.append({"model": model, "label": entry["label"], "type": entry["type"]})
    result = []
    for user in users.values():
        user["models"] = sorted(user["models"].values(), key=lambda entry: (-entry["calls"], entry["model"]))
        result.append(user)
    totals = {
        "messages": sum(u["messages"] for u in result),
        "messages_voice": sum(u["messages_voice"] for u in result),
        "messages_text": sum(u["messages_text"] for u in result),
        "model_calls": sum(u["model_calls"] for u in result),
        "tokens": sum(u["tokens_in"] + u["tokens_out"] for u in result),
        "tokens_in": sum(u["tokens_in"] for u in result),
        "tokens_out": sum(u["tokens_out"] for u in result),
        "ai_search": sum(u["ai_search"] for u in result),
        "bing": sum(u["bing"] for u in result),
        "errors": sum(u["errors"] for u in result),
        "active_users": sum(1 for u in result if u["messages"]),
    }
    return result, {"totals": totals, "models": ordered_models}


def get_dashboard(
    range_key: str, app_counts: dict[str, int], resets: dict[str, datetime] | None = None
) -> dict:
    range_key = range_key if range_key in RANGES else DEFAULT_RANGE
    timespan, _ = RANGES[range_key]
    resets = resets or {}
    cache_key = (range_key, tuple(sorted((user_id, moment.isoformat()) for user_id, moment in resets.items())))
    with _lock:
        cached = _cache.get(cache_key)
        if cached and time.time() - cached[0] < CACHE_SECONDS:
            data = cached[1]
        else:
            data = _fetch(timespan, range_key, resets)
            for stale in [key for key in _cache if key[0] == range_key]:
                del _cache[stale]
            _cache[cache_key] = (time.time(), data)
    result = dict(data)
    users = []
    for user in data.get("users", []):
        user = dict(user)
        user["reset_at"] = resets[user["id"]].isoformat() if user["id"] in resets else None
        user["app_messages"] = app_counts.get(user["id"], 0)
        user["pending"] = max(0, user["app_messages"] - user["messages"] - user["errors"])
        users.append(user)
    result["users"] = users
    result["pending_total"] = sum(user["pending"] for user in users)
    return result


def _fetch(timespan: str, range_key: str, resets: dict[str, datetime] | None = None) -> dict:
    catalog = model_catalog()
    base = {
        "range": range_key,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": {
            "type": "Application Insights conectado al proyecto Foundry",
            "resource": settings.appinsights_name,
            "project": settings.project_name,
            "service": settings.service_name,
            "schema": _schema["name"],
            "portal_url": portal_url(),
        },
        "kql": build_query(_schema["name"] or "classic", resets),
    }
    if not settings.appinsights_resource_id:
        users, extra = _aggregate([], catalog)
        return {**base, "status": "not_configured", "error": "APPINSIGHTS_RESOURCE_ID no configurado.",
                "users": users, **extra}
    try:
        schema, kql, rows = _run(timespan, resets)
        users, extra = _aggregate(rows, catalog)
        base["source"]["schema"] = schema
        return {**base, "kql": kql, "status": "ok", "error": None, "users": users, **extra}
    except QueryError as exc:
        status = "forbidden" if exc.status in (401, 403) else "error"
        message = (
            "La identidad de la app aún no tiene permisos de lectura (propagación de RBAC en curso)."
            if status == "forbidden"
            else f"Error consultando la telemetría: {exc}"
        )
        log.warning("Consulta de telemetría fallida: %s", exc)
    except Exception as exc:  # noqa: BLE001
        status, message = "error", f"Error consultando la telemetría: {exc.__class__.__name__}: {exc}"
        log.warning(message)
    users, extra = _aggregate([], catalog)
    return {**base, "status": status, "error": message[:400], "users": users, **extra}
