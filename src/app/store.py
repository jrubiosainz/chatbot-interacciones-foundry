"""Persistencia simple por usuario: historial (JSON) y audios de respuesta (MP3).

En Azure usa Blob Storage con identidad administrada; en local, una carpeta.
"""

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path

from .config import settings

log = logging.getLogger("chatbot.store")


class Store:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cache: dict[str, dict] = {}
        self._container = None
        self._local = None
        if settings.storage_blob_endpoint:
            from azure.storage.blob import BlobServiceClient

            from .clients import credential

            service = BlobServiceClient(account_url=settings.storage_blob_endpoint, credential=credential())
            self._container = service.get_container_client(settings.storage_container)
        else:
            self._local = Path(settings.local_data_dir)
            self._local.mkdir(parents=True, exist_ok=True)

    @property
    def backend(self) -> str:
        return "blob" if self._container is not None else "local"

    def ensure_ready(self) -> None:
        if self._container is not None:
            try:
                if not self._container.exists():
                    self._container.create_container()
            except Exception as exc:  # noqa: BLE001
                log.warning("No se pudo comprobar/crear el contenedor: %s", exc)
                raise

    def _read(self, key: str) -> bytes | None:
        if self._container is not None:
            from azure.core.exceptions import ResourceNotFoundError

            try:
                return self._container.download_blob(key).readall()
            except ResourceNotFoundError:
                return None
        path = self._local / key
        return path.read_bytes() if path.exists() else None

    def _write(self, key: str, data: bytes, content_type: str) -> None:
        if self._container is not None:
            from azure.storage.blob import ContentSettings

            self._container.upload_blob(
                key, data, overwrite=True, content_settings=ContentSettings(content_type=content_type)
            )
            return
        path = self._local / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def _delete_prefix(self, prefix: str) -> None:
        if self._container is not None:
            for blob in self._container.list_blobs(name_starts_with=prefix):
                try:
                    self._container.delete_blob(blob.name)
                except Exception:  # noqa: BLE001
                    pass
            return
        folder = self._local / prefix
        if folder.exists():
            for item in folder.glob("**/*"):
                if item.is_file():
                    item.unlink()

    def load_user(self, user_id: str) -> dict:
        with self._lock:
            if user_id in self._cache:
                return self._cache[user_id]
        raw = self._read(f"users/{user_id}.json")
        state = json.loads(raw) if raw else {"conversations": {}, "messages": []}
        state.setdefault("conversations", {})
        state.setdefault("messages", [])
        with self._lock:
            self._cache.setdefault(user_id, state)
            return self._cache[user_id]

    def save_user(self, user_id: str, state: dict) -> None:
        with self._lock:
            self._cache[user_id] = state
        payload = json.dumps(state, ensure_ascii=False).encode("utf-8")
        self._write(f"users/{user_id}.json", payload, "application/json")

    def reset_user(self, user_id: str) -> None:
        # reset_at permite que el cuadro de mando cuente solo la telemetría posterior al reinicio.
        state = {"conversations": {}, "messages": [], "reset_at": datetime.now(timezone.utc).isoformat()}
        self.save_user(user_id, state)
        self._delete_prefix(f"audio/{user_id}/")

    def put_audio(self, user_id: str, message_id: str, data: bytes, content_type: str) -> None:
        self._write(f"audio/{user_id}/{message_id}", data, content_type)

    def get_audio(self, user_id: str, message_id: str) -> bytes | None:
        return self._read(f"audio/{user_id}/{message_id}")


_store: Store | None = None
_store_lock = threading.Lock()


def get_store() -> Store:
    global _store
    with _store_lock:
        if _store is None:
            _store = Store()
        return _store
