"""审核 Bot 的短任务文件暂存与指令衔接。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re
import time

from app.platform.intake import (
    IntakeMaterialRef,
    IntakeOutcome,
    IntakePersistence,
    IntakeTaskSubmission,
    check_intake_file_limits,
)
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
_ATTACHMENT_NUMBER_RE = re.compile(r"(?:附件|附表)\s*([一二三四五六七八九十0-9]+)")
_ATTACHMENT_HEADER_RE = re.compile(r"^\s*(?:附件|附表)\s*([一二三四五六七八九十0-9]+)(?:\s*[:：]|\s|$)")
_REFERENCE_CONTEXT_RE = re.compile(r"(?:详见|参见|见|请填写|请参阅|随文).{0,10}(?:附件|附表)")


@dataclass(frozen=True)
class ReviewIntakeDecision:
    action: str
    reply: str = ""
    files: tuple[UploadedFile, ...] = ()
    instructions: tuple[str, ...] = ()
    primary_file_index: int | None = None
    revision: int = 0
    cancelled: bool = False

    def to_platform_outcome(self, *, channel: str, sender_userid: str) -> IntakeOutcome:
        if self.cancelled or self.action == "cancel":
            return IntakeOutcome.cancelled(self.reply)
        if self.action in {"wait", "wait_auto"}:
            return IntakeOutcome.wait(self.reply)
        if self.action in {"bypass", "stale"}:
            return IntakeOutcome.bypass()
        task_types = {
            "run_single": "review_single",
            "run_multi": "review_multi",
            "run_format": "review_format",
        }
        task_type = task_types.get(self.action)
        if task_type is None:
            raise ValueError(f"未知的审核组装动作：{self.action}")
        return IntakeOutcome.submit(
            IntakeTaskSubmission(
                channel=channel,
                sender_userid=sender_userid,
                task_type=task_type,
                instructions=tuple(item.strip() for item in self.instructions if item.strip()),
                materials=tuple(IntakeMaterialRef.file(item) for item in self.files),
                metadata={
                    "source": "review_intake",
                    "revision": self.revision,
                    "primary_file_index": self.primary_file_index,
                },
            ),
            reply=self.reply,
        )


@dataclass
class ReviewIntakeState:
    mode: str | None = None
    files: list[UploadedFile] = field(default_factory=list)
    recent_file: UploadedFile | None = None
    instructions: list[str] = field(default_factory=list)
    awaiting_primary: bool = False
    revision: int = 0
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


def infer_primary_file_index(
    files: list[UploadedFile] | tuple[UploadedFile, ...],
    *,
    file_texts: tuple[str, ...] = (),
) -> int | None:
    """综合文件名和正文引用识别主文件，不使用发送顺序兜底。"""
    if not files:
        return None
    attachment_indexes = {
        index
        for index, file in enumerate(files)
        if _ATTACHMENT_FILENAME_RE.search(Path(file.filename).stem)
    }
    non_attachment_indexes = [index for index in range(len(files)) if index not in attachment_indexes]
    hinted = [
        index
        for index in non_attachment_indexes
        if _PRIMARY_FILENAME_HINT_RE.search(Path(files[index].filename).stem)
    ]
    if len(hinted) == 1:
        return hinted[0]
    if attachment_indexes and len(non_attachment_indexes) == 1:
        return non_attachment_indexes[0]

    if len(file_texts) != len(files):
        return None

    content_attachment_indexes: set[int] = set()
    attachment_numbers: set[str] = set()
    own_numbers: dict[int, set[str]] = {}
    for index, (file, text) in enumerate(zip(files, file_texts)):
        numbers = set(_ATTACHMENT_NUMBER_RE.findall(Path(file.filename).stem))
        header_match = _ATTACHMENT_HEADER_RE.search(text[:300])
        if header_match:
            numbers.add(header_match.group(1))
            content_attachment_indexes.add(index)
        if index in attachment_indexes:
            content_attachment_indexes.add(index)
        own_numbers[index] = numbers
        attachment_numbers.update(numbers)

    scores: dict[int, int] = {}
    for index, text in enumerate(file_texts):
        if index in content_attachment_indexes:
            continue
        references = set(_ATTACHMENT_NUMBER_RE.findall(text)) - own_numbers[index]
        matching_references = references & attachment_numbers
        score = len(matching_references) * 3
        if references:
            score += 1
        if _REFERENCE_CONTEXT_RE.search(text):
            score += 3
        if _PRIMARY_FILENAME_HINT_RE.search(Path(files[index].filename).stem):
            score += 4
        if score > 0:
            scores[index] = score

    if not scores:
        return None
    best_score = max(scores.values())
    best_indexes = [index for index, score in scores.items() if score == best_score]
    return best_indexes[0] if len(best_indexes) == 1 else None


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
        self._persistence = IntakePersistence(
            storage_dir=storage_dir,
            state_filename="state.json",
            ttl_seconds=ttl_seconds,
        )
        self._states: dict[tuple[str, str], ReviewIntakeState] = {}

    def pending_mode(self, *, channel: str, sender_userid: str) -> str | None:
        state = self._get_state((channel, sender_userid))
        return state.mode if state else None

    def handle_text(self, *, channel: str, sender_userid: str, text: str) -> ReviewIntakeDecision:
        key = (channel, sender_userid)
        clean_text = text.strip()
        state = self._get_state(key)

        if is_review_cancel_signal(clean_text):
            if state is None:
                return ReviewIntakeDecision(
                    action="wait",
                    reply="当前没有待处理的审核文件。",
                    cancelled=True,
                )
            self.clear(channel=channel, sender_userid=sender_userid)
            return ReviewIntakeDecision(
                action="wait",
                reply="已取消本次审核并清空暂存文件。",
                cancelled=True,
            )

        if is_format_review_request(clean_text):
            if state and state.mode in {"auto", "multi"} and len(state.files) > 1:
                return ReviewIntakeDecision(
                    action="wait",
                    reply=(
                        "当前多份文件会自动进入联合审核，格式审核暂不与联合审核混用。"
                        "如需改做格式审核，请先取消本次审核后重新发送。"
                    ),
                )
            if state and state.mode in {"auto", "multi"} and state.files:
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
            seeded_recent = state.recent_file is not None or bool(state.files)
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
                        "请继续发送其他文件，系统会自动开始。"
                    ),
                )
            count = len(state.files)
            if count:
                return ReviewIntakeDecision(
                    action="wait",
                    reply=f"当前已收到 {count} 份文件。请继续发送其他文件，系统会自动开始。",
                )
            return ReviewIntakeDecision(
                action="wait",
                reply="收到，准备联合审核。请发送需要一起核对的文件，系统会自动开始。",
            )

        if is_review_start_signal(clean_text):
            if state is None or state.mode not in {"auto", "multi"}:
                return ReviewIntakeDecision(action="wait", reply="当前没有待联合审核的文件。")
            if len(state.files) < 2:
                if state.mode == "auto":
                    return ReviewIntakeDecision(
                        action="wait",
                        reply="文件已收到，正在审核，请稍等。",
                    )
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
                reply="已记录补充要求。可以继续发送文件，系统会自动开始。",
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

        if state.mode == "format":
            stored = self._persist_uploaded_file(key, file)
            state.files = [stored]
            return self._consume(key, state, action="run_format", files=(stored,))

        if state.mode in {None, "auto", "multi"}:
            if state.mode is None:
                if state.recent_file is not None:
                    self._delete_stored_file(state.recent_file)
                    state.recent_file = None
                state.mode = "auto"
            elif state.recent_file is not None:
                state.files.append(state.recent_file)
                state.recent_file = None
            limit_message = self._file_limit_message(state.files, file.size_bytes)
            if limit_message:
                return ReviewIntakeDecision(action="wait", reply=limit_message)
            stored = self._persist_uploaded_file(key, file)
            state.files.append(stored)
            state.awaiting_primary = False
            state.revision += 1
            state.updated_at = time.time()
            self._states[key] = state
            self._persist_state(key, state)
            return ReviewIntakeDecision(
                action="wait_auto",
                files=tuple(state.files),
                revision=state.revision,
            )

        raise ValueError(f"不支持的审核暂存模式: {state.mode}")

    def auto_batch_snapshot(
        self,
        *,
        channel: str,
        sender_userid: str,
        expected_revision: int,
    ) -> ReviewIntakeDecision:
        state = self._get_state((channel, sender_userid))
        if (
            state is None
            or state.mode not in {"auto", "multi"}
            or state.awaiting_primary
            or state.revision != expected_revision
        ):
            return ReviewIntakeDecision(action="stale")
        return ReviewIntakeDecision(
            action="snapshot",
            files=tuple(state.files),
            revision=state.revision,
        )

    def finalize_auto_batch(
        self,
        *,
        channel: str,
        sender_userid: str,
        expected_revision: int,
        file_texts: tuple[str, ...] = (),
    ) -> ReviewIntakeDecision:
        key = (channel, sender_userid)
        state = self._get_state(key)
        if (
            state is None
            or state.mode not in {"auto", "multi"}
            or state.awaiting_primary
            or state.revision != expected_revision
        ):
            return ReviewIntakeDecision(action="stale")
        if not state.files:
            return ReviewIntakeDecision(action="stale")
        if len(state.files) == 1:
            if state.mode == "multi":
                return ReviewIntakeDecision(
                    action="wait",
                    reply="联合审核至少需要 2 份文件，请继续发送其他文件。",
                )
            return self._remember_recent(
                key,
                state,
                action="run_single",
            )

        primary_file_index = infer_primary_file_index(
            state.files,
            file_texts=file_texts,
        )
        if primary_file_index is None:
            state.mode = "multi"
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

    def clear(self, *, channel: str, sender_userid: str) -> None:
        key = (channel, sender_userid)
        self._states.pop(key, None)
        self._remove_persisted_state(key, preserve_files=False)

    def cleanup_files(self, files: tuple[UploadedFile, ...] | list[UploadedFile]) -> None:
        for file in files:
            self._persistence.delete_file(file)

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
            revision=state.revision,
        )

    def _remember_recent(
        self,
        key: tuple[str, str],
        state: ReviewIntakeState,
        *,
        action: str,
    ) -> ReviewIntakeDecision:
        """单文件内容审核启动后保留原件，供 30 分钟内追加格式审核。"""
        file = state.files[0]
        state.mode = None
        state.files = []
        state.recent_file = file
        state.instructions = []
        state.awaiting_primary = False
        state.updated_at = time.time()
        self._states[key] = state
        self._persist_state(key, state)
        return ReviewIntakeDecision(
            action=action,
            files=(file,),
            revision=state.revision,
        )

    def _file_limit_message(self, files: list[UploadedFile], incoming_size: int) -> str:
        violation = check_intake_file_limits(
            files,
            incoming_size=incoming_size,
            max_files=self._max_files,
            max_total_file_bytes=self._max_total_file_bytes,
        )
        if violation and violation.code == "too_many_files":
            return (
                f"每次联合审核最多接收 {self._max_files} 个文件；"
                "已收到的文件会继续处理，本文件未纳入。"
            )
        if violation and violation.code == "total_size_exceeded":
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
        return self._persistence.persist_file(key, file)

    def _persist_state(self, key: tuple[str, str], state: ReviewIntakeState) -> None:
        payload = {
            "mode": state.mode,
            "files": [self._persistence.file_payload(file) for file in state.files],
            "recent_file": self._persistence.file_payload(state.recent_file) if state.recent_file else None,
            "instructions": list(state.instructions),
            "awaiting_primary": state.awaiting_primary,
            "revision": state.revision,
            "created_at": state.created_at,
            "updated_at": state.updated_at,
        }
        self._persistence.save_state(key, payload)

    def _load_state(self, key: tuple[str, str]) -> ReviewIntakeState | None:
        payload = self._persistence.load_state(key)
        if payload is None:
            return None
        try:
            files = [self._file_from_dict(key, item) for item in payload.get("files", [])]
            recent_payload = payload.get("recent_file")
            recent_file = self._file_from_dict(key, recent_payload) if recent_payload else None
            return ReviewIntakeState(
                mode=str(payload.get("mode")) if payload.get("mode") else None,
                files=[file for file in files if file is not None],
                recent_file=recent_file,
                instructions=[str(item) for item in payload.get("instructions", [])],
                awaiting_primary=bool(payload.get("awaiting_primary", False)),
                revision=int(payload.get("revision", 0)),
                created_at=float(payload.get("created_at", time.time())),
                updated_at=float(payload.get("updated_at", time.time())),
            )
        except (ValueError, TypeError):
            self._remove_persisted_state(key, preserve_files=False)
            return None

    def _file_from_dict(self, key: tuple[str, str], payload: object) -> UploadedFile | None:
        return self._persistence.restore_file(
            key,
            payload,
            default_filename="upload.docx",
        )

    def _remove_persisted_state(self, key: tuple[str, str], *, preserve_files: bool) -> None:
        self._persistence.clear(key, preserve_files=preserve_files)

    def _delete_stored_file(self, file: UploadedFile) -> None:
        self._persistence.delete_file(file)
