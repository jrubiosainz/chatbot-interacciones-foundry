"""Crea el índice de Azure AI Search y carga la base de conocimiento de servicios municipales."""

import json
import logging
from pathlib import Path

import httpx

from .clients import credential
from .config import settings

log = logging.getLogger("chatbot.search")

API_VERSION = "2024-07-01"
KB_PATH = Path(__file__).parent / "data" / "kb_servicios.json"


def _headers() -> dict:
    token = credential().get_token("https://search.azure.com/.default").token
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def index_definition() -> dict:
    return {
        "name": settings.search_index,
        "fields": [
            {"name": "id", "type": "Edm.String", "key": True, "filterable": True, "searchable": False},
            {"name": "title", "type": "Edm.String", "searchable": True, "analyzer": "es.microsoft"},
            {"name": "content", "type": "Edm.String", "searchable": True, "analyzer": "es.microsoft"},
            {
                "name": "category",
                "type": "Edm.String",
                "searchable": True,
                "filterable": True,
                "facetable": True,
            },
            {"name": "url", "type": "Edm.String", "searchable": False, "filterable": False},
        ],
    }


def ensure_index() -> int:
    if not settings.search_endpoint:
        raise RuntimeError("SEARCH_ENDPOINT no está configurado.")
    base = settings.search_endpoint.rstrip("/")
    with httpx.Client(timeout=60) as http:
        response = http.put(
            f"{base}/indexes/{settings.search_index}",
            params={"api-version": API_VERSION},
            headers=_headers(),
            json=index_definition(),
        )
        if response.status_code not in (200, 201, 204):
            raise RuntimeError(f"No se pudo crear el índice ({response.status_code}): {response.text[:300]}")

        documents = json.loads(KB_PATH.read_text(encoding="utf-8"))
        batch = {"value": [{"@search.action": "mergeOrUpload", **doc} for doc in documents]}
        response = http.post(
            f"{base}/indexes/{settings.search_index}/docs/index",
            params={"api-version": API_VERSION},
            headers=_headers(),
            json=batch,
        )
        if response.status_code not in (200, 207):
            raise RuntimeError(f"No se pudieron cargar documentos ({response.status_code}): {response.text[:300]}")
    log.info("Índice %s listo con %d documentos.", settings.search_index, len(documents))
    return len(documents)
