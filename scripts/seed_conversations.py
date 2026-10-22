#!/usr/bin/env python3
"""Puebla el chatbot con las conversaciones de demostración (data/conversaciones_demo.json).

Cada mensaje se envía a POST /api/simulate. Los mensajes de voz se sintetizan, se transcriben y
la respuesta se convierte a audio en el servidor: este script no reproduce ningún sonido.

Ejemplos:
    python3 scripts/seed_conversations.py --url https://<tu-app>.azurecontainerapps.io --wait-ready
    python3 scripts/seed_conversations.py --url ... --users ana,carmen --reset
    python3 scripts/seed_conversations.py --url ... --resume          # continúa donde se quedó

Solo usa la biblioteca estándar de Python 3.9+.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_FILE = ROOT / "data" / "conversaciones_demo.json"
RETRYABLE = {409, 429, 500, 502, 503, 504}
_print_lock = threading.Lock()


def say(message: str) -> None:
    with _print_lock:
        print(time.strftime("%H:%M:%S"), message, flush=True)


class ApiError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(f"HTTP {status}: {detail}")
        self.status = status
        self.detail = detail


def request(base: str, method: str, path: str, body: dict | None = None, timeout: float = 30) -> dict:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        base + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            detail = json.loads(raw).get("detail", raw)
        except ValueError:
            detail = raw
        raise ApiError(exc.code, str(detail)[:300]) from None


def user_counts(base: str) -> dict:
    return {u["id"]: u["messages"] for u in request(base, "GET", "/api/users/summary")["users"]}


def wait_ready(base: str, max_minutes: float) -> None:
    deadline = time.time() + max_minutes * 60
    while True:
        try:
            config = request(base, "GET", "/api/config", timeout=20)
            boot = config.get("bootstrap") or {}
            if boot.get("ready"):
                say(f"Servicio listo (agentes: {', '.join((boot.get('agents') or {}).keys()) or '—'}).")
                if not config.get("simulation_enabled", True):
                    sys.exit("La simulación está desactivada en el servidor (SIMULATION_ENABLED=false).")
                return
            say(f"Esperando a que el servicio esté listo… pasos={boot.get('steps')} error={boot.get('error')}")
        except (urllib.error.URLError, ApiError, TimeoutError, OSError) as exc:
            say(f"Esperando al servicio… ({exc})")
        if time.time() > deadline:
            sys.exit("El servicio no ha quedado listo a tiempo.")
        time.sleep(15)


def describe(result: dict) -> str:
    assistant = result.get("assistant_message") or {}
    usage = assistant.get("usage") or {}
    tools = ", ".join(f"{t['label']}×{t['count']}" for t in assistant.get("tools") or []) or "sin herramientas"
    tokens = f"{usage.get('input_tokens') or 0}→{usage.get('output_tokens') or 0} tokens"
    latency = assistant.get("latency_ms")
    return f"{assistant.get('deployment', '?')} · {tokens} · {tools}" + (f" · {latency} ms" if latency else "")


def saved_on_server(base: str, user_id: str, expected: int, wait_seconds: float) -> bool:
    """Comprueba (esperando hasta wait_seconds) si el servidor ya tiene guardado el mensaje."""
    deadline = time.time() + wait_seconds
    while True:
        try:
            if user_counts(base).get(user_id, 0) >= expected:
                return True
        except Exception:  # noqa: BLE001
            pass
        if time.time() >= deadline:
            return False
        time.sleep(10)


def send_with_retry(base: str, user_id: str, message: dict, expected: int, args) -> tuple[bool, str]:
    """Envía un turno sin duplicarlo: tras un fallo comprueba si el servidor llegó a guardarlo."""
    payload = {
        "user_id": user_id,
        "text": message["text"],
        "modality": message.get("modality", "texto"),
        "model": message.get("model", "principal"),
    }
    delay = 10.0
    last_error = ""
    for attempt in range(1, args.retries + 2):
        wait_seconds = 0.0
        try:
            return True, describe(request(base, "POST", "/api/simulate", payload, timeout=args.timeout))
        except ApiError as exc:
            last_error = str(exc)
            if exc.status not in RETRYABLE:
                return False, last_error
            if exc.status == 409:
                wait_seconds = args.timeout
            elif exc.status == 429:
                # Límite de tokens por minuto del modelo: se deja recuperar la ventana.
                delay = max(delay, 30.0)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            # El servidor puede seguir procesando el turno: se espera antes de reenviarlo.
            last_error = f"{exc.__class__.__name__}: {exc}"
            wait_seconds = args.timeout
        if saved_on_server(base, user_id, expected, wait_seconds):
            return True, "guardado en el servidor pese al error de red"
        if attempt <= args.retries:
            say(f"  [{user_id}] reintento {attempt}/{args.retries} en {delay:.0f} s ({last_error[:120]})")
            time.sleep(delay)
            delay = min(delay * 2, 90)
    return False, last_error


def seed_user(base: str, user_id: str, messages: list, start: int, args) -> tuple[int, int]:
    ok = failed = 0
    baseline = 0 if args.dry_run else user_counts(base).get(user_id, 0)
    for index, message in enumerate(messages[start:], start=start + 1):
        label = f"[{user_id} {index:>2}/{len(messages)}] {message.get('modality', 'texto'):5} {message.get('model', 'principal'):9}"
        if args.dry_run:
            say(f"{label} {message['text'][:70]}")
            continue
        success, info = send_with_retry(base, user_id, message, baseline + ok + 1, args)
        if success:
            ok += 1
            say(f"{label} ✓ {info}")
        else:
            failed += 1
            say(f"{label} ✗ {info}")
            if not args.continue_on_error:
                # Parar evita huecos en la conversación; --resume continúa desde este mensaje.
                say(f"  [{user_id}] se detiene en el mensaje {index}; relanza con --resume para continuar.")
                break
        time.sleep(args.delay)
    return ok, failed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default=os.environ.get("SERVICE_WEB_URI", "http://127.0.0.1:8000"),
                        help="URL base de la app (por defecto $SERVICE_WEB_URI).")
    parser.add_argument("--file", default=str(DEFAULT_FILE), help="Fichero JSON con las conversaciones.")
    parser.add_argument("--users", help="Usuarios a poblar, separados por comas (por defecto, todos).")
    parser.add_argument("--reset", action="store_true", help="Borra antes el historial de cada usuario.")
    parser.add_argument("--resume", action="store_true",
                        help="Salta los mensajes que el usuario ya tiene enviados (continúa una carga anterior).")
    parser.add_argument("--limit", type=int, help="Máximo de mensajes por usuario.")
    parser.add_argument("--wait-ready", action="store_true", help="Espera a que la app termine de arrancar.")
    parser.add_argument("--wait-minutes", type=float, default=30, help="Tiempo máximo de espera (minutos).")
    parser.add_argument("--delay", type=float, default=1.0, help="Pausa entre mensajes de un mismo usuario (s).")
    parser.add_argument("--timeout", type=float, default=180, help="Timeout por mensaje (s).")
    parser.add_argument("--retries", type=int, default=6, help="Reintentos ante errores transitorios.")
    parser.add_argument("--parallel", type=int, default=2,
                        help="Usuarios en paralelo (más paralelismo puede agotar los tokens por minuto).")
    parser.add_argument("--continue-on-error", action="store_true",
                        help="Sigue con el siguiente mensaje tras un error (deja huecos; por defecto se detiene).")
    parser.add_argument("--dry-run", action="store_true", help="Muestra lo que se enviaría sin llamar a la API.")
    args = parser.parse_args()

    if args.reset and args.resume:
        parser.error("--reset y --resume no se pueden combinar.")
    base = args.url.rstrip("/")
    data = json.loads(Path(args.file).read_text(encoding="utf-8"))["users"]
    selected = [u.strip().lower() for u in args.users.split(",")] if args.users else list(data)
    unknown = [u for u in selected if u not in data]
    if unknown:
        parser.error(f"Usuarios desconocidos: {', '.join(unknown)}. Disponibles: {', '.join(data)}")

    plan = {u: data[u][: args.limit] if args.limit else data[u] for u in selected}
    total = sum(len(m) for m in plan.values())
    voice = sum(1 for msgs in plan.values() for m in msgs if m.get("modality") == "voz")
    say(f"{total} mensajes ({voice} por voz) para {len(plan)} usuarios → {base}")

    if not args.dry_run:
        if args.wait_ready:
            wait_ready(base, args.wait_minutes)
        if args.reset:
            for user_id in plan:
                request(base, "POST", f"/api/users/{user_id}/reset")
            say("Historiales borrados.")

    starts = {u: 0 for u in plan}
    if args.resume and not args.dry_run:
        counts = user_counts(base)
        starts = {u: min(counts.get(u, 0), len(plan[u])) for u in plan}
        say("Reanudando desde: " + ", ".join(f"{u}={s}" for u, s in starts.items()))

    started = time.time()
    with ThreadPoolExecutor(max_workers=max(1, args.parallel)) as pool:
        futures = {u: pool.submit(seed_user, base, u, plan[u], starts[u], args) for u in plan}
        results = {u: f.result() for u, f in futures.items()}

    ok = sum(r[0] for r in results.values())
    failed = sum(r[1] for r in results.values())
    say(f"Terminado en {time.time() - started:.0f} s: {ok} correctos, {failed} con error.")
    if failed:
        say("Vuelve a lanzar el script con --resume para completar los mensajes pendientes.")
        sys.exit(1)


if __name__ == "__main__":
    main()
