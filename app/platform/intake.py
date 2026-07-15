from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import shutil
import time
from typing import Mapping
from uuid import uuid4

from app.platform.models import UploadedFile


IntakeKey = tuple[str, str]


@dataclass(frozen=True)
class IntakeLimitViolation:
    code: str
    max_files: int
    max_total_file_bytes: int


def check_intake_file_limits(
    existing_files: tuple[UploadedFile, ...] | list[UploadedFile],
    *,
    incoming_size: int | None,
    max_files: int,
    max_total_file_bytes: int,
) -> IntakeLimitViolation | None:
    if len(existing_files) >= max_files:
        return IntakeLimitViolation(
            code="too_many_files",
            max_files=max_files,
            max_total_file_bytes=max_total_file_bytes,
        )
    if incoming_size is None:
        return None
    current_size = sum(file.size_bytes for file in existing_files)
    if incoming_size < 0 or current_size + incoming_size > max_total_file_bytes:
        return IntakeLimitViolation(
            code="total_size_exceeded",
            max_files=max_files,
            max_total_file_bytes=max_total_file_bytes,
        )
    return None


class IntakePersistence:
    """多消息任务组装共用的受限持久化层，不包含具体业务判断。"""

    def __init__(
        self,
        *,
        storage_dir: str | Path | None,
        state_filename: str,
        ttl_seconds: int,
    ) -> None:
        if Path(state_filename).name != state_filename or not state_filename.endswith(".json"):
            raise ValueError("状态文件名必须是当前目录下的 .json 文件")
        self._storage_dir = Path(storage_dir).resolve() if storage_dir else None
        self._state_filename = state_filename
        self._temporary_filename = f".{state_filename}.tmp"
        self._ttl_seconds = ttl_seconds
        if self._storage_dir is not None:
            self._storage_dir.mkdir(parents=True, exist_ok=True)
            self._storage_dir.chmod(0o700)
            self.cleanup_expired()

    @property
    def enabled(self) -> bool:
        return self._storage_dir is not None

    def persist_file(self, key: IntakeKey, file: UploadedFile) -> UploadedFile:
        if self._storage_dir is None:
            return file
        files_dir = self._session_dir(key) / "files"
        files_dir.mkdir(parents=True, exist_ok=True)
        target = files_dir / f"{uuid4().hex[:12]}-{_safe_filename(file.filename)}"
        target.write_bytes(file.read_bytes())
        return UploadedFile(
            filename=file.filename,
            content=b"",
            content_type=file.content_type,
            stored_path=str(target),
            delete_after_read=True,
        )

    @staticmethod
    def file_payload(file: UploadedFile) -> dict[str, object]:
        return {
            "filename": file.filename,
            "content_type": file.content_type,
            "stored_path": file.stored_path,
            "size_bytes": file.size_bytes,
        }

    def restore_file(
        self,
        key: IntakeKey,
        payload: object,
        *,
        default_filename: str = "upload.bin",
    ) -> UploadedFile | None:
        if self._storage_dir is None or not isinstance(payload, Mapping):
            return None
        raw_path = str(payload.get("stored_path", "") or "").strip()
        if not raw_path:
            return None
        stored_path = Path(raw_path).resolve()
        files_dir = (self._session_dir(key) / "files").resolve()
        if files_dir not in stored_path.parents or not stored_path.is_file():
            return None
        return UploadedFile(
            filename=str(payload.get("filename", default_filename) or default_filename),
            content=b"",
            content_type=str(payload.get("content_type", "") or ""),
            stored_path=str(stored_path),
            delete_after_read=True,
        )

    def save_state(self, key: IntakeKey, payload: Mapping[str, object]) -> None:
        if self._storage_dir is None:
            return
        session_dir = self._session_dir(key)
        session_dir.mkdir(parents=True, exist_ok=True)
        stored_payload = dict(payload)
        stored_payload["channel"] = key[0]
        stored_payload["sender_userid"] = key[1]
        temporary = session_dir / self._temporary_filename
        temporary.write_text(
            json.dumps(stored_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(session_dir / self._state_filename)

    def load_state(self, key: IntakeKey) -> dict[str, object] | None:
        if self._storage_dir is None:
            return None
        state_path = self._session_dir(key) / self._state_filename
        if not state_path.is_file():
            return None
        try:
            payload = json.loads(state_path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("intake state must be an object")
            if payload.get("channel") != key[0] or payload.get("sender_userid") != key[1]:
                raise ValueError("intake state key mismatch")
            updated_at = float(payload.get("updated_at", 0))
            if updated_at <= 0 or time.time() - updated_at > self._ttl_seconds:
                self.clear(key, preserve_files=False)
                return None
            return payload
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            self.clear(key, preserve_files=False)
            return None

    def clear(self, key: IntakeKey, *, preserve_files: bool) -> None:
        if self._storage_dir is None:
            return
        session_dir = self._session_dir(key)
        (session_dir / self._state_filename).unlink(missing_ok=True)
        (session_dir / self._temporary_filename).unlink(missing_ok=True)
        if not preserve_files:
            shutil.rmtree(session_dir, ignore_errors=True)
            return
        try:
            session_dir.rmdir()
        except OSError:
            pass

    def delete_file(self, file: UploadedFile) -> None:
        if self._storage_dir is None or not file.delete_after_read or not file.stored_path:
            return
        path = Path(file.stored_path).resolve()
        if self._storage_dir != path and self._storage_dir not in path.parents:
            return
        path.unlink(missing_ok=True)
        for parent in (path.parent, path.parent.parent):
            if parent == self._storage_dir:
                break
            try:
                parent.rmdir()
            except OSError:
                break

    def cleanup_expired(self) -> None:
        if self._storage_dir is None:
            return
        now = time.time()
        for session_dir in self._storage_dir.iterdir():
            if not session_dir.is_dir():
                continue
            state_path = session_dir / self._state_filename
            try:
                payload = json.loads(state_path.read_text(encoding="utf-8"))
                updated_at = float(payload.get("updated_at", 0))
                expired = updated_at <= 0 or now - updated_at > self._ttl_seconds
            except (OSError, ValueError, TypeError, json.JSONDecodeError, AttributeError):
                expired = True
            if expired:
                shutil.rmtree(session_dir, ignore_errors=True)

    def _session_dir(self, key: IntakeKey) -> Path:
        if self._storage_dir is None:
            raise RuntimeError("intake persistence is disabled")
        digest = hashlib.sha256(f"{key[0]}\0{key[1]}".encode("utf-8")).hexdigest()[:24]
        return self._storage_dir / digest


def _safe_filename(filename: str) -> str:
    candidate = Path(filename or "").name.strip() or "upload.bin"
    cleaned = re.sub(r"[^\w.\-\u4e00-\u9fff]+", "_", candidate)
    cleaned = cleaned.strip("._") or "upload.bin"
    suffix = Path(cleaned).suffix
    if not re.fullmatch(r"\.[A-Za-z0-9]{1,16}", suffix):
        suffix = ".bin"
    stem = Path(cleaned).stem[:160] or "upload"
    return f"{stem}{suffix.lower()}"
