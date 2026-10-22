"""API y frontal del prototipo «Chatbot de interacciones» sobre Microsoft Foundry."""

import logging
import re
import threading
import time
import uuid
from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from openai import RateLimitError
from pydantic import BaseModel, Field

from . import dashboard, foundry, search_setup, telemetry, voice
from .config import DEFAULT_MODEL_KEY, TEXT_MODELS, settings
from .personas import PERSONAS, get_persona
from .store import get_store

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
for noisy in ("azure.core.pipeline.policies.http_logging_policy", "azure.identity", "httpx", "azure.monitor"):
    logging.getLogger(noisy).setLevel(logging.WARNING)
log = logging.getLogger("chatbot")

STATIC_DIR = Path(__file__).parent / "static"
VERSION = str(int(time.time()))
MAX_TEXT = 2000
MAX_AUDIO_BYTES = 15 * 1024 * 1024
MESSAGE_ID = re.compile(r"^[ua]-[0-9a-f]{12}$")
AUDIO_EXTENSIONS = {
    "audio/webm": "webm",
    "video/webm": "webm",
    "audio/ogg": "ogg",
    "audio/mp4": "mp4",
    "audio/x-m4a": "m4a",
    "audio/m4a": "m4a",
    "audio/aac": "m4a",
    "audio/mpeg": "mp3",
    "audio/mp3": "mp3",
    "audio/wav": "wav",
    "audio/x-wav": "wav",
}

BOOT: dict = {
    "ready": False,
    "steps": {"store": "pendiente", "index": "pendiente", "agents": "pendiente"},
    "attempts": 0,
    "error": None,
    "agents": {},
    "documents": None,
    "started_at": None,
    "ready_at": None,
}

_user_locks = {persona.id: threading.Lock() for persona in PERSONAS}
_turn_times: deque = deque()
_turn_lock = threading.Lock()


class TurnError(Exception):
    """Error de entrada del usuario (p. ej. audio sin voz reconocible)."""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _bootstrap() -> None:
    BOOT["started_at"] = now_iso()
    if not settings.project_endpoint:
        BOOT["error"] = "FOUNDRY_PROJECT_ENDPOINT no está configurado (modo local sin Azure)."
        return
    deadline = time.time() + 30 * 60
    delay = 10
    while time.time() < deadline:
        BOOT["attempts"] += 1
        try:
            if BOOT["steps"]["store"] != "ok":
                get_store().ensure_ready()
                BOOT["steps"]["store"] = "ok"
            if BOOT["steps"]["index"] != "ok":
                BOOT["documents"] = search_setup.ensure_index()
                BOOT["steps"]["index"] = "ok"
            if BOOT["steps"]["agents"] != "ok":
                BOOT["agents"] = foundry.ensure_agents()
                BOOT["steps"]["agents"] = "ok"
            BOOT.update(ready=True, error=None, ready_at=now_iso())
            log.info("Entorno listo: índice y agentes de Foundry preparados.")
            return
        except Exception as exc:  # noqa: BLE001
            BOOT["error"] = f"{exc.__class__.__name__}: {str(exc)[:400]}"
            log.warning("Preparación (intento %s) fallida: %s", BOOT["attempts"], BOOT["error"])
            time.sleep(delay)
            delay = min(delay + 5, 30)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    telemetry.setup_telemetry()
    threading.Thread(target=_bootstrap, name="bootstrap", daemon=True).start()
    yield
    telemetry.flush()


app = FastAPI(title="Chatbot de interacciones · Microsoft Foundry", lifespan=lifespan)


@app.middleware("http")
async def security_headers(request, call_next):
    response = await call_next(request)
    # autoplay=() impide cualquier reproducción automática: el audio solo suena tras un clic explícito.
    response.headers.setdefault("Permissions-Policy", "microphone=(self), autoplay=(), camera=(), geolocation=()")
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; img-src 'self' data:; media-src 'self' blob:; "
        "style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'",
    )
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    return response


class ChatIn(BaseModel):
    user_id: str
    text: str = Field(min_length=1, max_length=MAX_TEXT)
    model: str | None = None


class SimulateIn(ChatIn):
    modality: str = "texto"


def _persona(user_id: str):
    persona = get_persona(user_id)
    if persona is None:
        raise HTTPException(404, "Usuario no encontrado.")
    return persona


def _model_key(value: str | None) -> str:
    key = (value or DEFAULT_MODEL_KEY).strip().lower()
    if key not in TEXT_MODELS:
        raise HTTPException(400, f"Modelo no válido. Opciones: {', '.join(TEXT_MODELS)}.")
    return key


def _rate_limit() -> None:
    with _turn_lock:
        now = time.time()
        while _turn_times and now - _turn_times[0] > 3600:
            _turn_times.popleft()
        if len(_turn_times) >= settings.max_turns_per_hour:
            raise HTTPException(429, "Se ha alcanzado el límite de mensajes por hora de esta demo.")
        _turn_times.append(now)


def _process_turn(persona, model_key: str, modality: str, *, text: str | None = None,
                  audio: bytes | None = None, audio_name: str = "voz.webm",
                  audio_type: str = "audio/webm", simulated: bool = False) -> dict:
    if not BOOT["ready"]:
        raise HTTPException(503, "El entorno se está preparando (índice de búsqueda y agentes). Prueba en unos segundos.")
    _rate_limit()
    lock = _user_locks[persona.id]
    if not lock.acquire(timeout=300):
        raise HTTPException(409, "Hay otro mensaje de este usuario en curso.")
    try:
        store = get_store()
        state = store.load_user(persona.id)
        turn_id = uuid.uuid4().hex[:12]
        started = time.perf_counter()
        user_audio = reply_audio = stt = tts = None

        with telemetry.user_context(persona), telemetry.turn_span(
            persona, modality, model_key, turn_id, simulated
        ) as span:
            if modality == "voz":
                if audio is None:
                    # Simulación: la voz del propio usuario se sintetiza (no cuenta en el cuadro).
                    synthetic = voice.synthesize(text, persona.voice, counted=False, purpose="usuario_simulado")
                    audio, audio_type, audio_name = synthetic["audio"], synthetic["content_type"], "voz.mp3"
                stt = voice.transcribe(audio, audio_name, audio_type)
                if not stt["text"]:
                    raise TurnError("No se ha reconocido ninguna frase en el audio. Prueba a hablar más cerca del micrófono.")
                user_text = stt["text"]
                user_audio = (audio, audio_type)
            else:
                user_text = text

            result = foundry.run_agent(persona, state, user_text, model_key, modality)
            span.set_attribute("gen_ai.conversation.id", result["conversation_id"])
            if modality == "voz":
                try:
                    tts = voice.synthesize(voice.speakable(result["text"]), settings.assistant_voice)
                    reply_audio = (tts["audio"], tts["content_type"])
                except Exception as exc:  # noqa: BLE001
                    log.warning("No se pudo generar la voz de la respuesta: %s", exc)
                    span.set_attribute("app.tts.error", exc.__class__.__name__)

        user_msg = {
            "id": f"u-{turn_id}",
            "role": "user",
            "text": user_text,
            "modality": modality,
            "model": model_key,
            "ts": now_iso(),
            "turn_id": turn_id,
            "simulated": simulated,
            "has_audio": False,
            "audio_type": None,
        }
        if stt:
            user_msg["stt"] = {"model": stt["model"], "latency_ms": stt["latency_ms"], "usage": stt["usage"]}
        bot_msg = {
            "id": f"a-{turn_id}",
            "role": "assistant",
            "text": result["text"],
            "modality": modality,
            "model": model_key,
            "model_label": TEXT_MODELS[model_key]["label"],
            "deployment": result["deployment"],
            "agent": result["agent"],
            "ts": now_iso(),
            "turn_id": turn_id,
            "usage": result["usage"],
            "tools": result["tools"],
            "citations": result["citations"],
            "agent_latency_ms": result["latency_ms"],
            "latency_ms": int((time.perf_counter() - started) * 1000),
            "has_audio": False,
            "audio_type": None,
        }
        if tts:
            bot_msg["tts"] = {"model": tts["model"], "voice": tts["voice"], "usage": tts["usage"]}
        for message, payload in ((user_msg, user_audio), (bot_msg, reply_audio)):
            if payload:
                try:
                    store.put_audio(persona.id, message["id"], payload[0], payload[1])
                    message.update(has_audio=True, audio_type=payload[1])
                except Exception as exc:  # noqa: BLE001
                    log.warning("No se pudo guardar el audio %s: %s", message["id"], exc)
        state["messages"].extend([user_msg, bot_msg])
        store.save_user(persona.id, state)
        return {"user_message": user_msg, "assistant_message": bot_msg}
    finally:
        lock.release()


def _run_turn(*args, **kwargs) -> dict:
    try:
        return _process_turn(*args, **kwargs)
    except HTTPException:
        raise
    except TurnError as exc:
        raise HTTPException(422, str(exc)) from exc
    except RateLimitError as exc:
        log.warning("Límite de capacidad del modelo alcanzado: %s", str(exc)[:300])
        raise HTTPException(
            429, "El modelo ha alcanzado su límite de capacidad (tokens por minuto). Espera unos segundos y vuelve a intentarlo."
        ) from exc
    except Exception as exc:  # noqa: BLE001
        log.exception("Error procesando el turno")
        raise HTTPException(502, f"Error al procesar el mensaje: {exc.__class__.__name__}: {str(exc)[:300]}") from exc


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8").replace("__V__", VERSION)
    return HTMLResponse(html, headers={"Cache-Control": "no-cache"})


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok", "ready": BOOT["ready"]}


@app.get("/api/config")
def get_config() -> dict:
    return {
        "users": [persona.public() for persona in PERSONAS],
        "models": [
            {"key": key, "label": model["label"], "deployment": model["deployment"], "agent": model["agent"]}
            for key, model in TEXT_MODELS.items()
        ],
        "default_model": DEFAULT_MODEL_KEY,
        "voice_models": {"stt": settings.stt_deployment, "tts": settings.tts_deployment},
        "simulation_enabled": settings.simulation_enabled,
        "max_recording_seconds": settings.max_recording_seconds,
        "bootstrap": BOOT,
        "telemetry": telemetry.STATE,
        "foundry": {"project": settings.project_name, "account": settings.account_name},
        "links": {"appinsights": dashboard.portal_url()},
        "store": get_store().backend,
        "ranges": list(dashboard.RANGES),
        "default_range": dashboard.DEFAULT_RANGE,
    }


@app.get("/api/users/summary")
def users_summary() -> dict:
    store = get_store()
    users = []
    for persona in PERSONAS:
        try:
            messages = store.load_user(persona.id)["messages"]
        except Exception:  # noqa: BLE001
            messages = []
        sent = [message for message in messages if message.get("role") == "user"]
        users.append(
            {
                "id": persona.id,
                "messages": len(sent),
                "voice": sum(1 for message in sent if message.get("modality") == "voz"),
                "last_ts": messages[-1].get("ts") if messages else None,
            }
        )
    return {"users": users}


@app.get("/api/users/{user_id}/messages")
def get_messages(user_id: str) -> dict:
    persona = _persona(user_id)
    return {"user_id": persona.id, "messages": get_store().load_user(persona.id)["messages"]}


@app.post("/api/chat")
def chat(body: ChatIn) -> dict:
    persona = _persona(body.user_id)
    text = body.text.strip()
    if not text:
        raise HTTPException(400, "El mensaje está vacío.")
    return _run_turn(persona, _model_key(body.model), "texto", text=text)


@app.post("/api/voice")
def voice_turn(user_id: str = Form(...), model: str | None = Form(None), audio: UploadFile = File(...)) -> dict:
    persona = _persona(user_id)
    model_key = _model_key(model)
    data = audio.file.read(MAX_AUDIO_BYTES + 1)
    if len(data) > MAX_AUDIO_BYTES:
        raise HTTPException(413, "El audio es demasiado grande.")
    if len(data) < 1000:
        raise HTTPException(422, "El audio es demasiado corto.")
    content_type = (audio.content_type or "audio/webm").split(";")[0].strip().lower()
    extension = AUDIO_EXTENSIONS.get(content_type)
    if extension is None:
        raise HTTPException(415, f"Formato de audio no admitido: {content_type}.")
    return _run_turn(
        persona, model_key, "voz", audio=data, audio_name=f"voz.{extension}", audio_type=content_type
    )


@app.post("/api/simulate")
def simulate(body: SimulateIn) -> dict:
    if not settings.simulation_enabled:
        raise HTTPException(403, "La simulación está desactivada (SIMULATION_ENABLED=false).")
    persona = _persona(body.user_id)
    modality = body.modality.strip().lower()
    if modality not in ("texto", "voz"):
        raise HTTPException(400, "La modalidad debe ser 'texto' o 'voz'.")
    return _run_turn(persona, _model_key(body.model), modality, text=body.text.strip(), simulated=True)


@app.post("/api/users/{user_id}/reset")
def reset_user(user_id: str) -> dict:
    persona = _persona(user_id)
    lock = _user_locks[persona.id]
    if not lock.acquire(timeout=120):
        raise HTTPException(409, "Hay un mensaje en curso para este usuario.")
    try:
        get_store().reset_user(persona.id)
    finally:
        lock.release()
    return {"user_id": persona.id, "reset": True}


@app.get("/api/audio/{user_id}/{message_id}")
def get_audio(user_id: str, message_id: str) -> Response:
    persona = _persona(user_id)
    if not MESSAGE_ID.match(message_id):
        raise HTTPException(404, "Audio no encontrado.")
    state = get_store().load_user(persona.id)
    message = next((m for m in state["messages"] if m["id"] == message_id and m.get("has_audio")), None)
    data = get_store().get_audio(persona.id, message_id) if message else None
    if data is None:
        raise HTTPException(404, "Audio no encontrado.")
    return Response(
        content=data,
        media_type=message.get("audio_type") or "audio/mpeg",
        headers={"Cache-Control": "private, max-age=86400"},
    )


def _parse_ts(value: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(value) if value else None
    except ValueError:
        return None


@app.get("/api/dashboard")
def get_dashboard(range_key: str = Query(dashboard.DEFAULT_RANGE, alias="range")) -> dict:
    range_key = range_key if range_key in dashboard.RANGES else dashboard.DEFAULT_RANGE
    since = datetime.now(timezone.utc) - dashboard.RANGES[range_key][1]
    app_counts = {}
    resets = {}
    store = get_store()
    for persona in PERSONAS:
        try:
            state = store.load_user(persona.id)
        except Exception:  # noqa: BLE001
            state = {}
        messages = state.get("messages") or []
        app_counts[persona.id] = sum(
            1
            for message in messages
            if message.get("role") == "user" and (_parse_ts(message.get("ts")) or since) >= since
        )
        reset_at = _parse_ts(state.get("reset_at"))
        if reset_at and reset_at.tzinfo and reset_at > since:
            resets[persona.id] = reset_at
    return dashboard.get_dashboard(range_key, app_counts, resets)
