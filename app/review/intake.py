"""审核 Bot 的短任务文件暂存与指令衔接。"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
import re
import shutil
import time
from uuid import uuid4

from app.platform.models import UploadedFile


DEFAULT_MAX_FILES = 5
DEFAULT_MAX_TOTAL_FILE_BYTES = 50 * 1024 * 1024

_FORMAT_REQUEST_RE = re.compile(
    r"(?:(?:审|审核|检查|核对|校对|看看|看下).{0,8}(?:公文)?格式|"
    r"(?:公文)?格式.{0,8}(?:审|审核|检查|核对|校对|看看|看下))"
)
_FORMAT_NEGATION_RE = re.compile(
    r"(?:不审|不用审|不用看|无需|不要|不用管)格式|"
    r"格式(?:不审|不用审|不用看|无需|不要|不用管)"
)
_MULTI_REQUEST_RE = re.compile(
    r"(?:(?:多文件|多个文件|这些文件|这几个文件|正文.{0,4}附件|联合|一起)"
    r".{0,8}(?:审|审核|检查|核对|校对)|"
    r"(?:审|审核|检查|核对|校对).{0,8}"
    r"(?:多文件|多个文件|这些文件|这几个文件|正文.{0,4}附件|联合|一起))"
)
_START_SIGNALS = {"开始", "开始审核", "开始审", "开始处理", "材料齐了", "文件齐了", "就这些"}
_CANCEL_SIGNALS = {"取消", "取消审核", "不要审核了", "不用审了", "清空文件", "重新开始"}
_ATTACHMENT_FILENAME_RE = re.compile(r"(?:^|[-_（(\s])(?:附件|附表)\s*[一二三四五六七八九十0-9]+")
_PRIMARY_FILENAME_HINT_RE = re.compile(r"正文|主文|主文件")


@dataclass(frozen=True)
class ReviewIntakeDecision:
    action: str
    reply: str = ""
    files: tuple[UploadedFile, ...] = ()
    instructions: tuple[str, ...] = ()
    primary_file_index: int | None = None


@dataclass
class ReviewIntakeState:
    mode: str | None = None
    files: list[UploadedFile] = field(default_factory=list)
    recent_file: UploadedFile | None = None
    instructions: list[str] = field(default_factory=list)
    awaiting_primary: bool = False
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)


def _normalize_short_command(text: str) -> str:
    return re.sub(r"[\s，。！？,.!?；;:：]+", "", text.strip())


def is_format_review_request(text: str) -> bool:
    normalized = _normalize_short_command(text)
    if not normalized or len(normalized) > 40:
        return False
    if _FORMAT_NEGATION_RE.search(normalized):
        return False
    return _FORMAT_REQUEST_RE.search(normalized) is not None


def is_multi_file_review_request(text: str) -> bool:
    normalized = _normalize_short_command(text)
    return bool(normalized and len(normalized) <= 50 and _MULTI_REQUEST_RE.search(normalized))


def is_review_start_signal(text: str) -> bool:
    return _normalize_short_command(text) in _START_SIGNALS


def is_review_cancel_signal(text: str) -> bool:
    return _normalize_short_command(text) in _CANCEL_SIGNALS


def infer_primary_file_index(files: list[UploadedFile]) -> int | None:
    """只在文件名证据唯一时识别主文件，不使用发送顺序兜底。"""
    if not files:
        return None
    attachment_indexes = {
        index
        for index, file in enumerate(files)
        if _ATTACHMENT_FILENAME_RE.search(Path(file.filename).stem)
    }
    non_attachment_indexes = [index for index in range(len(files)) if index not in attachment_indexes]
    if len(non_attachment_indexes) == 1:
        return non_attachment_indexes[0]
    hinted = [
        index
        for index in non_attachment_indexes
        if _PRIMARY_FILENAME_HINT_RE.search(Path(files[index].filename).stem)
    ]
    return hinted[0] if len(hinted) == 1 else None


def _parse_primary_selection(text: str, files: list[UploadedFile]) -> int | None:
    normalized = _normalize_short_command(text)
    number_match = re.search(
        r"(?:第)?([1-9]\d*)(?:个|份|号)?(?:是|作为|当作|为)?(?:正文|主文件|主文)|"
        r"(?:正文|主文件|主文)(?:是|为|选)?(?:第)?([1-9]\d*)",
        normalized,
    )
    if number_match:
        raw_number = number_match.group(1) or number_match.group(2)
        index = int(raw_number) - 1
        return index if 0 <= index < len(files) else None
    if normalized.isdigit():
        index = int(normalized) - 1
        return index if 0 <= index < len(files) else None
    filename_matches = [
        index
        for index, file in enumerate(files)
        if file.filename in text or Path(file.filename).stem in text
    ]
    return filename_matches[0] if len(filename_matches) == 1 else None


def _primary_selection_reply(files: list[UploadedFile]) -> str:
    file_lines = "\n".join(f"{index}. {file.filename}" for index, file in enumerate(files, start=1))
    return (
        "无法确定哪一份是主文件，请指定正文：\n"
        f"{file_lines}\n"
        "请回复“第2个是正文”或“主文件是文件名”。"
    )


class ReviewIntakeStore:
    """按入口和用户隔离的审核文件暂存，支持重启恢复。"""

    def __init__(
        self,
        *,
        ttl_seconds: int = 1800,
        max_files: int = DEFAULT_MAX_FILES,
        max_total_file_bytes: int = DEFAULT_MAX_TOTAL_FILE_BYTES,
        storage_dir: str | Path | None = None,
    ) -> None:
        self._ttl_seconds = ttl_seconds
        self._max_files = max_files
        self._max_total_file_bytes = max_total_file_bytes
        self._storage_dir = Path(storage_dir).resolve() if storage_dir else None
        self._states: dict[tuple[str, str], ReviewIntakeState] = {}
        if self._storage_dir is not None:
            self._storage_dir.mkdir(parents=True, exist_ok=True)
            self._storage_dir.chmod(0o700)
            self._cleanup_expired_states()

    def pending_mode(self, *, channel: str, sender_userid: str) -> str | None:
        state = self._get_state((channel, sender_userid))
        return state.mode if state else None

    def handle_text(self, *, channel: str, sender_userid: str, text: str) -> ReviewIntakeDecision:
        key = (channel, sender_userid)
        clean_text = text.strip()
        state = self._get_state(key)

        if is_review_cancel_signal(clean_text):
            if state is None:
                return ReviewIntakeDecision(action="wait", reply="当前没有待处理的审核文件。")
            self.clear(channel=channel, sender_userid=sender_userid)
            return ReviewIntakeDecision(action="wait", reply="已取消本次审核并清空暂存文件。")

        if is_format_review_request(clean_text):
            if state and state.mode == "multi" and len(state.files) > 1:
                return ReviewIntakeDecision(
                    action="wait",
                    reply="当前已有多份文件等待联合审核。请先回复“开始审核”或“取消审核”，格式审核暂不与联合审核混用。",
                )
            if state and state.mode == "multi" and state.files:
                file = state.files[0]
                return self._consume(key, state, action="run_format", files=(file,))
            if state and state.recent_file is not None:
                file = state.recent_file
                return self._consume(key, state, action="run_format", files=(file,))
            state = state or ReviewIntakeState()
            state.mode = "format"
            state.updated_at = time.time()
            self._states[key] = state
            self._persist_state(key, state)
            return ReviewIntakeDecision(
                action="wait",
                reply="收到。请发送需要审核格式的 .docx 文档；本次只检查公文格式，不审核文字内容。",
            )

        if is_multi_file_review_request(clean_text):
            state = state or ReviewIntakeState()
            seeded_recent = state.recent_file is not None
            if state.mode != "multi":
                state.mode = "multi"
                if state.recent_file is not None:
                    state.files = [state.recent_file]
                    state.recent_file = None
            state.updated_at = time.time()
            self._states[key] = state
            self._persist_state(key, state)
            if seeded_recent:
                return ReviewIntakeDecision(
                    action="wait",
                    reply=(
                        "已把刚发的文件纳入联合审核，但不会默认把它认作正文。"
                        "请继续发送其他文件，发完后回复“开始审核”。"
                    ),
                )
            count = len(state.files)
            if count:
                return ReviewIntakeDecision(
                    action="wait",
                    reply=f"当前已收到 {count} 份文件。请继续发送其他文件，发完后回复“开始审核”。",
                )
            return ReviewIntakeDecision(
                action="wait",
                reply="收到，准备联合审核。请发送需要一起核对的文件，发完后回复“开始审核”。",
            )

        if is_review_start_signal(clean_text):
            if state is None or state.mode != "multi":
                return ReviewIntakeDecision(action="wait", reply="当前没有待联合审核的文件。")
            if len(state.files) < 2:
                return ReviewIntakeDecision(
                    action="wait",
                    reply="联合审核至少需要 2 份文件，请至少再发送 1 份文件。",
                )
            primary_file_index = infer_primary_file_index(state.files)
            if primary_file_index is None:
                state.awaiting_primary = True
                state.updated_at = time.time()
                self._persist_state(key, state)
                return ReviewIntakeDecision(
                    action="wait",
                    reply=_primary_selection_reply(state.files),
                )
            return self._consume(
                key,
                state,
                action="run_multi",
                files=tuple(state.files),
                instructions=tuple(state.instructions),
                primary_file_index=primary_file_index,
            )

        if state and state.mode == "multi" and state.awaiting_primary:
            primary_file_index = _parse_primary_selection(clean_text, state.files)
            if primary_file_index is None:
                return ReviewIntakeDecision(
                    action="wait",
                    reply=_primary_selection_reply(state.files),
                )
            return self._consume(
                key,
                state,
                action="run_multi",
                files=tuple(state.files),
                instructions=tuple(state.instructions),
                primary_file_index=primary_file_index,
            )

        if state and state.mode == "multi":
            if clean_text:
                state.instructions.append(clean_text)
                state.updated_at = time.time()
                self._persist_state(key, state)
            return ReviewIntakeDecision(
                action="wait",
                reply="已记录补充要求。可以继续发送文件，发完后回复“开始审核”。",
            )

        return ReviewIntakeDecision(action="bypass")

    def add_file(
        self,
        *,
        channel: str,
        sender_userid: str,
        file: UploadedFile,
    ) -> ReviewIntakeDecision:
        key = (channel, sender_userid)
        state = self._get_state(key) or ReviewIntakeState()

        if state.mode == "multi":
            limit_message = self._file_limit_message(state.files, file.size_bytes)
            if limit_message:
                return ReviewIntakeDecision(action="wait", reply=limit_message)
            stored = self._persist_uploaded_file(key, file)
            state.files.append(stored)
            state.updated_at = time.time()
            self._states[key] = state
            self._persist_state(key, state)
            return ReviewIntakeDecision(
                action="wait",
                reply=(
                    f"已收到第 {len(state.files)} 份文件。可以继续发送，"
                    "发完后回复“开始审核”。"
                ),
            )

        if state.mode == "format":
            stored = self._persist_uploaded_file(key, file)
            state.files = [stored]
            return self._consume(key, state, action="run_format", files=(stored,))

        if state.recent_file is not None:
            self._delete_stored_file(state.recent_file)
        state.recent_file = self._persist_uploaded_file(key, file)
        state.updated_at = time.time()
        self._states[key] = state
        self._persist_state(key, state)
        return ReviewIntakeDecision(action="bypass")

    def clear(self, *, channel: str, sender_userid: str) -> None:
        key = (channel, sender_userid)
        self._states.pop(key, None)
        self._remove_persisted_state(key, preserve_files=False)

    @staticmethod
    def cleanup_files(files: tuple[UploadedFile, ...] | list[UploadedFile]) -> None:
        for file in files:
            if not file.delete_after_read or not file.stored_path:
                continue
            path = Path(file.stored_path)
            path.unlink(missing_ok=True)
            for parent in (path.parent, path.parent.parent):
                try:
                    parent.rmdir()
                except OSError:
                    break

    def _consume(
        self,
        key: tuple[str, str],
        state: ReviewIntakeState,
        *,
        action: str,
        files: tuple[UploadedFile, ...],
        instructions: tuple[str, ...] = (),
        primary_file_index: int | None = None,
    ) -> ReviewIntakeDecision:
        self._states.pop(key, None)
        self._remove_persisted_state(key, preserve_files=True)
        return ReviewIntakeDecision(
            action=action,
            files=files,
            instructions=instructions,
            primary_file_index=primary_file_index,
        )

    def _file_limit_message(self, files: list[UploadedFile], incoming_size: int) -> str:
        if len(files) >= self._max_files:
            return f"每次联合审核最多接收 {self._max_files} 个文件，请先回复“开始审核”。"
        current_size = sum(file.size_bytes for file in files)
        if incoming_size < 0 or current_size + incoming_size > self._max_total_file_bytes:
            limit_mb = self._max_total_file_bytes / 1024 / 1024
            return f"本次联合审核文件总大小不能超过 {limit_mb:g}MB，请减少文件后重试。"
        return ""

    def _get_state(self, key: tuple[str, str]) -> ReviewIntakeState | None:
        state = self._states.get(key)
        if state is None:
            state = self._load_state(key)
            if state is not None:
                self._states[key] = state
        if state is None:
            return None
        if time.time() - state.updated_at > self._ttl_seconds:
            self.clear(channel=key[0], sender_userid=key[1])
            return None
        return state

    def _persist_uploaded_file(self, key: tuple[str, str], file: UploadedFile) -> UploadedFile:
        if self._storage_dir is None:
            return file
        files_dir = self._state_dir(key) / "files"
        files_dir.mkdir(parents=True, exist_ok=True)
        safe_name = _safe_filename(file.filename)
        target = files_dir / f"{uuid4().hex[:12]}-{safe_name}"
        target.write_bytes(file.read_bytes())
        return UploadedFile(
            filename=file.filename,
            content=b"",
            content_type=file.content_type,
            stored_path=str(target),
            delete_after_read=True,
        )

    def _persist_state(self, key: tuple[str, str], state: ReviewIntakeState) -> None:
        if self._storage_dir is None:
            return
        state_dir = self._state_dir(key)
        state_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "channel": key[0],
            "sender_userid": key[1],
            "mode": state.mode,
            "files": [_file_to_dict(file) for file in state.files],
            "recent_file": _file_to_dict(state.recent_file) if state.recent_file else None,
            "instructions": list(state.instructions),
            "awaiting_primary": state.awaiting_primary,
            "created_at": state.created_at,
            "updated_at": state.updated_at,
        }
        temporary = state_dir / "state.json.tmp"
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(state_dir / "state.json")

    def _load_state(self, key: tuple[str, str]) -> ReviewIntakeState | None:
        if self._storage_dir is None:
            return None
        state_path = self._state_dir(key) / "state.json"
        if not state_path.exists():
            return None
        try:
            payload = json.loads(state_path.read_text(encoding="utf-8"))
            if payload.get("channel") != key[0] or payload.get("sender_userid") != key[1]:
                raise ValueError("review intake key mismatch")
            files = [self._file_from_dict(key, item) for item in payload.get("files", [])]
            recent_payload = payload.get("recent_file")
            recent_file = self._file_from_dict(key, recent_payload) if recent_payload else None
            return ReviewIntakeState(
                mode=str(payload.get("mode")) if payload.get("mode") else None,
                files=[file for file in files if file is not None],
                recent_file=recent_file,
                instructions=[str(item) for item in payload.get("instructions", [])],
                awaiting_primary=bool(payload.get("awaiting_primary", False)),
                created_at=float(payload.get("created_at", time.time())),
                updated_at=float(payload.get("updated_at", time.time())),
            )
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            self._remove_persisted_state(key, preserve_files=False)
            return None

    def _file_from_dict(self, key: tuple[str, str], payload: object) -> UploadedFile | None:
        if not isinstance(payload, dict):
            return None
        stored_path = Path(str(payload.get("stored_path", ""))).resolve()
        files_dir = (self._state_dir(key) / "files").resolve()
        if files_dir not in stored_path.parents or not stored_path.is_file():
            return None
        return UploadedFile(
            filename=str(payload.get("filename", "upload.docx")),
            content=b"",
            content_type=str(payload.get("content_type", "")),
            stored_path=str(stored_path),
            delete_after_read=True,
        )

    def _state_dir(self, key: tuple[str, str]) -> Path:
        if self._storage_dir is None:
            raise RuntimeError("review intake persistence is disabled")
        digest = hashlib.sha256(f"{key[0]}\0{key[1]}".encode("utf-8")).hexdigest()[:24]
        return self._storage_dir / digest

    def _remove_persisted_state(self, key: tuple[str, str], *, preserve_files: bool) -> None:
        if self._storage_dir is None:
            return
        state_dir = self._state_dir(key)
        (state_dir / "state.json").unlink(missing_ok=True)
        (state_dir / "state.json.tmp").unlink(missing_ok=True)
        if not preserve_files:
            shutil.rmtree(state_dir, ignore_errors=True)

    def _cleanup_expired_states(self) -> None:
        if self._storage_dir is None:
            return
        now = time.time()
        for state_dir in self._storage_dir.iterdir():
            if not state_dir.is_dir():
                continue
            try:
                payload = json.loads((state_dir / "state.json").read_text(encoding="utf-8"))
                updated_at = float(payload.get("updated_at", 0))
                expired = updated_at <= 0 or now - updated_at > self._ttl_seconds
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                expired = True
            if expired:
                shutil.rmtree(state_dir, ignore_errors=True)

    @staticmethod
    def _delete_stored_file(file: UploadedFile) -> None:
        if file.delete_after_read and file.stored_path:
            Path(file.stored_path).unlink(missing_ok=True)


def _safe_filename(filename: str) -> str:
    path = Path(filename or "upload.docx")
    stem = re.sub(r"[^\w一-鿿\-_]", "_", path.stem or "upload")
    suffix = path.suffix.lower() if re.fullmatch(r"\.[a-z0-9]+", path.suffix.lower()) else ".docx"
    return stem + suffix


def _file_to_dict(file: UploadedFile) -> dict[str, object]:
    return {
        "filename": file.filename,
        "content_type": file.content_type,
        "stored_path": file.stored_path,
        "size_bytes": file.size_bytes,
    }
