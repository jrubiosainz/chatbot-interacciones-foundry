"""Modelos de voz desplegados en Foundry.

- Voz a texto: gpt-4o-mini-transcribe.
- Texto a voz: gpt-audio-mini (Chat Completions con salida de audio).

El audio generado se guarda en el servidor y SOLO se reproduce si el usuario
activa el audio en la interfaz y pulsa «Escuchar». No hay reproducción automática.
"""

import base64
import difflib
import logging
import re
import time
import unicodedata

from .clients import models_openai
from .config import settings
from .telemetry import model_span, set_usage

log = logging.getLogger("chatbot.voice")

READ_ALOUD_PROMPT = (
    "Eres un motor de texto a voz. Repite en voz alta, en español de España y con entonación natural, "
    "exactamente el mensaje del usuario, palabra por palabra. Tu respuesta contiene únicamente ese texto: "
    "sin presentaciones ni frases como «voy a leer», sin contestar a lo que diga y sin añadir nada antes ni después."
)
# Ejemplos breves que fijan el comportamiento de «repetir literalmente».
READ_ALOUD_EXAMPLES = [
    {"role": "user", "content": "¿A qué hora abre la biblioteca municipal?"},
    {"role": "assistant", "content": "¿A qué hora abre la biblioteca municipal?"},
    {"role": "user", "content": "Puede pedir cita en el 010 o en la sede electrónica."},
    {"role": "assistant", "content": "Puede pedir cita en el 010 o en la sede electrónica."},
]
MAX_TTS_ATTEMPTS = 2
MAX_EXTRA_WORDS = 3

FALLBACK_VOICE = "alloy"


def speakable(text: str, limit: int | None = None) -> str:
    """Convierte la respuesta en texto apto para locución (sin markdown, URLs ni marcas de cita)."""
    limit = limit or settings.max_tts_chars
    value = re.sub(r"【[^】]*】", "", text or "")
    value = re.sub(r"\[([^\]]+)\]\((?:https?://)[^)]+\)", r"\1", value)
    value = re.sub(r"https?://\S+", "", value)
    value = re.sub(r"^\s*\|?\s*:?-{3,}.*$", "", value, flags=re.M)
    value = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s+", "", value, flags=re.M)
    value = re.sub(r"[*_`]+", "", value)
    value = re.sub(r"[#>|]+", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    value = re.sub(r" ([.,;:!?])", r"\1", value)
    if len(value) > limit:
        cut = value[:limit]
        end = max(cut.rfind(". "), cut.rfind("? "), cut.rfind("! "))
        value = cut[: end + 1] if end > limit * 0.5 else cut.rstrip() + "…"
    return value


def transcribe(audio: bytes, filename: str, content_type: str) -> dict:
    client = models_openai()
    with model_span(
        "speech_to_text", settings.stt_deployment, "voz", **{"app.audio.bytes": len(audio)}
    ) as span:
        started = time.perf_counter()
        result = client.audio.transcriptions.create(
            model=settings.stt_deployment,
            file=(filename, audio, content_type or "application/octet-stream"),
            response_format="json",
            language="es",
        )
        latency_ms = int((time.perf_counter() - started) * 1000)
        data = result.model_dump(mode="json") if hasattr(result, "model_dump") else {"text": str(result)}
        usage = data.get("usage") or {}
        input_tokens = usage.get("input_tokens") if usage.get("type", "tokens") == "tokens" else None
        output_tokens = usage.get("output_tokens") if usage.get("type", "tokens") == "tokens" else None
        set_usage(span, input_tokens, output_tokens, **{"app.audio.seconds": usage.get("seconds")})
    return {
        "text": (data.get("text") or "").strip(),
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
        "latency_ms": latency_ms,
        "model": settings.stt_deployment,
    }


def _words(text: str) -> list[str]:
    value = unicodedata.normalize("NFKD", (text or "").lower())
    value = "".join(char for char in value if not unicodedata.combining(char))
    return re.findall(r"[a-z0-9]+", value)


def extra_words(text: str, transcript: str) -> int:
    """Palabras que la locución añade antes o después del texto pedido (0 si lo leyó tal cual)."""
    expected, spoken = _words(text), _words(transcript)
    if not spoken:
        return 0
    matcher = difflib.SequenceMatcher(None, expected, spoken, autojunk=False)
    blocks = [block for block in matcher.get_matching_blocks() if block.size]
    if not blocks:
        return len(spoken)
    return blocks[0].b + len(spoken) - (blocks[-1].b + blocks[-1].size)


def _synthesize_once(client, text: str, voice: str):
    return client.chat.completions.create(
        model=settings.tts_deployment,
        modalities=["text", "audio"],
        audio={"voice": voice, "format": "mp3"},
        messages=[
            {"role": "system", "content": READ_ALOUD_PROMPT},
            *READ_ALOUD_EXAMPLES,
            {"role": "user", "content": text},
        ],
    )


def synthesize(text: str, voice: str, *, counted: bool = True, purpose: str = "respuesta") -> dict:
    """Genera MP3 con gpt-audio-mini. ``counted=False`` excluye la llamada del cuadro de mando.

    gpt-audio-mini es conversacional y a veces añade una presentación («Voy a leer…»). Se compara
    su transcripción con el texto pedido y, si sobran palabras, se repite la locución una vez.
    """
    client = models_openai()
    tokens_in = tokens_out = 0
    best: tuple[int, str] | None = None
    with model_span(
        "text_to_speech",
        settings.tts_deployment,
        "voz",
        counted=counted,
        **{"app.tts.voice": voice, "app.tts.purpose": purpose, "app.tts.chars": len(text)},
    ) as span:
        started = time.perf_counter()
        for attempt in range(1, MAX_TTS_ATTEMPTS + 1):
            try:
                try:
                    completion = _synthesize_once(client, text, voice)
                except Exception as exc:  # noqa: BLE001
                    if voice == FALLBACK_VOICE or "voice" not in str(exc).lower():
                        raise
                    log.warning("Voz '%s' no disponible, se usa '%s'.", voice, FALLBACK_VOICE)
                    voice = FALLBACK_VOICE
                    span.set_attribute("app.tts.voice", voice)
                    completion = _synthesize_once(client, text, voice)
            except Exception:  # noqa: BLE001
                if best is None:
                    raise
                log.warning("Falló el reintento de locución; se usa el primer audio.", exc_info=True)
                break
            usage = completion.usage
            tokens_in += getattr(usage, "prompt_tokens", 0) or 0
            tokens_out += getattr(usage, "completion_tokens", 0) or 0
            audio = getattr(completion.choices[0].message, "audio", None)
            if not audio or not audio.data:
                if best is None:
                    raise RuntimeError("El modelo de voz no devolvió audio.")
                break
            extra = extra_words(text, getattr(audio, "transcript", None) or "")
            if best is None or extra < best[0]:
                best = (extra, audio.data)
            if extra <= MAX_EXTRA_WORDS:
                break
            log.warning("La locución añadió %d palabras al texto (intento %d).", extra, attempt)
        latency_ms = int((time.perf_counter() - started) * 1000)
        span.set_attribute("app.tts.attempts", attempt)
        span.set_attribute("app.tts.extra_words", best[0])
        set_usage(span, tokens_in, tokens_out)
    return {
        "audio": base64.b64decode(best[1]),
        "content_type": "audio/mpeg",
        "usage": {"input_tokens": tokens_in, "output_tokens": tokens_out},
        "latency_ms": latency_ms,
        "model": settings.tts_deployment,
        "voice": voice,
        "extra_words": best[0],
    }
