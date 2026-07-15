"""写作 Bot 多消息任务组装。

这一层只负责把用户分多条发送的“意图、素材、补充要求”组装成一次结构化写作请求。
真正的写作、权限、工具调用仍然交给 app.platform。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re
import time

from app.platform.intake import (
    IntakeAction,
    IntakeMaterialRef,
    IntakeOutcome,
    IntakePersistence,
    IntakeTaskSubmission,
    check_intake_file_limits,
)
from app.platform.models import UploadedFile
from app.platform.router import URL_RE


BRIEF_INTENT = "brief"
DIRECT_REPORT_INTENT = "direct_report"
REWRITE_INTENT = "rewrite"
RESEARCH_SYNTHESIS_INTENT = "research_synthesis"
DEFAULT_MAX_FILES = 10
DEFAULT_MAX_TOTAL_FILE_BYTES = 20 * 1024 * 1024
_CANCEL_SIGNALS = {"取消", "取消写作", "不要写了", "不用写了", "清空材料", "重新开始"}


@dataclass(frozen=True)
class IntakeMaterial:
    kind: str
    text: str = ""
    url: str = ""
    file: UploadedFile | None = None


@dataclass
class WritingIntakeSession:
    intent: str | None = None
    materials: list[IntakeMaterial] = field(default_factory=list)
    instructions: list[str] = field(default_factory=list)
    awaiting_clarification: bool = False
    clarification_message: str = ""
    processed_message_ids: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)


@dataclass(frozen=True)
class IntakeDecision:
    action: str
    reply: str = ""
    skill_id: str | None = None
    text: str = ""
    material_text: str = ""
    urls: tuple[str, ...] = ()
    files: tuple[UploadedFile, ...] = ()
    ack_message: str = ""

    def to_platform_outcome(self, *, channel: str, sender_userid: str) -> IntakeOutcome:
        if self.action == "bypass":
            return IntakeOutcome.bypass()
        if self.action == "wait":
            return IntakeOutcome.wait(self.reply)
        if self.action == "cancel":
            return IntakeOutcome.cancelled(self.reply)
        if self.action != "run":
            raise ValueError(f"未知的写作组装动作：{self.action}")
        if not self.skill_id:
            raise ValueError("写作任务提交缺少 skill_id")
        materials: list[IntakeMaterialRef] = []
        if self.material_text.strip():
            materials.append(IntakeMaterialRef.text(self.material_text))
        materials.extend(IntakeMaterialRef.url(item) for item in self.urls)
        materials.extend(IntakeMaterialRef.file(item) for item in self.files)
        instructions = (self.text.strip(),) if self.text.strip() else ()
        return IntakeOutcome.submit(
            IntakeTaskSubmission(
                channel=channel,
                sender_userid=sender_userid,
                task_type=self.skill_id,
                instructions=instructions,
                materials=tuple(materials),
                metadata={"source": "writing_intake"},
            ),
            reply=self.ack_message,
        )


class WritingIntakeStore:
    """写作短任务暂存，可选持久化到 M-Agent-Files/runtime/intake。"""

    def __init__(
        self,
        *,
        ttl_seconds: int = 1800,
        max_files: int = DEFAULT_MAX_FILES,
        max_total_file_bytes: int = DEFAULT_MAX_TOTAL_FILE_BYTES,
        storage_dir: str | Path | None = None,
    ):
        self._ttl_seconds = ttl_seconds
        self._max_files = max_files
        self._max_total_file_bytes = max_total_file_bytes
        self._persistence = IntakePersistence(
            storage_dir=storage_dir,
            state_filename="session.json",
            ttl_seconds=ttl_seconds,
        )
        self._sessions: dict[tuple[str, str], WritingIntakeSession] = {}

    def handle_text(
        self,
        *,
        channel: str,
        sender_userid: str,
        text: str,
        message_id: str = "",
    ) -> IntakeDecision:
        clean_text = text.strip()
        key = (channel, sender_userid)
        session = self._get_active_session(key)
        if session is not None and _is_duplicate_message(session, message_id):
            return IntakeDecision(action="wait", reply="这条消息已经收到，无需重复发送。")
        intent = detect_writing_intent(clean_text)
        urls = extract_urls(clean_text)
        has_material = bool(urls) or looks_like_material_text(clean_text)

        if is_writing_cancel_signal(clean_text):
            if session is None:
                return IntakeDecision(action="cancel", reply="当前没有待处理的写作材料。")
            self.clear(channel=channel, sender_userid=sender_userid)
            return IntakeDecision(action="cancel", reply="已取消本次写作并清空暂存材料。")

        if session is not None and session.awaiting_clarification and clean_text:
            if has_material:
                if session.intent == REWRITE_INTENT:
                    _add_rewrite_text_material(session, clean_text)
                else:
                    _add_text_materials(session, clean_text, urls)
            elif not is_start_signal(clean_text):
                session.instructions.append(clean_text)
            session.awaiting_clarification = False
            session.clarification_message = ""
            session.updated_at = time.time()
            self._persist_session(key, session)
            return self._run_or_ask_more(key, session)

        if session is None and intent and has_material:
            return IntakeDecision(action="bypass")

        if session is None and not intent and not has_material:
            return IntakeDecision(action="bypass")

        if session is None:
            session = WritingIntakeSession()
            self._sessions[key] = session
        _remember_message_id(session, message_id)
        self._persist_session(key, session)

        if is_start_signal(clean_text):
            return self._run_or_ask_more(key, session)

        # 已经选定写作类型后，长文本即使包含“优化、修改、简报”等普通词，
        # 也应优先视为用户发送的正文材料，避免被重复识别成意图。
        if session.intent is not None and has_material:
            intent = None

        if intent:
            session.intent = intent
            if not _is_pure_intent_text(clean_text, intent):
                session.instructions.append(clean_text)
            session.updated_at = time.time()
            if intent == REWRITE_INTENT:
                discarded = [item for item in session.materials if item.kind == "file"]
                session.materials = [item for item in session.materials if item.kind == "text"]
                for item in discarded:
                    self._delete_stored_file(item.file)
                if not session.materials:
                    self._persist_session(key, session)
                    return IntakeDecision(action="wait", reply=_reply_for_waiting_material(intent))
            if session.materials:
                return self._build_run_decision(key, session)
            self._persist_session(key, session)
            return IntakeDecision(
                action="wait",
                reply=_reply_for_waiting_material(intent),
            )

        if has_material:
            if session.intent == REWRITE_INTENT:
                added = _add_rewrite_text_material(session, clean_text)
            else:
                added = _add_text_materials(session, clean_text, urls)
            session.updated_at = time.time()
            self._persist_session(key, session)
            if session.intent:
                if session.intent == REWRITE_INTENT and added == 0:
                    return IntakeDecision(action="wait", reply=_reply_for_waiting_material(REWRITE_INTENT))
                return IntakeDecision(
                    action="wait",
                    reply=f"已收到第 {len(session.materials)} 份材料。可以继续发送材料，发完后回复“开始写”。",
                )
            can_rewrite = any(item.kind == "text" for item in session.materials)
            options = "写简报、写直报、做综合调研整合或润色" if can_rewrite else "写简报、写直报或做综合调研整合"
            return IntakeDecision(action="wait", reply=f"已收到 {added or 1} 份材料。你希望我怎么处理？可以回复：{options}。")

        session.instructions.append(clean_text)
        session.updated_at = time.time()
        self._persist_session(key, session)
        return IntakeDecision(
            action="wait",
            reply="已补充要求。可以继续发送材料，发完后回复“开始写”。",
        )

    def add_file(
        self,
        *,
        channel: str,
        sender_userid: str,
        file: UploadedFile,
        message_id: str = "",
    ) -> IntakeDecision:
        key = (channel, sender_userid)
        session = self._get_active_session(key)
        if session is None:
            session = WritingIntakeSession()
            self._sessions[key] = session
        elif _is_duplicate_message(session, message_id):
            return IntakeDecision(action="wait", reply="这个文件已经收到，无需重复发送。")
        _remember_message_id(session, message_id)
        self._persist_session(key, session)
        if session.intent == REWRITE_INTENT:
            return IntakeDecision(action="wait", reply=_reply_for_waiting_material(REWRITE_INTENT))
        limit_message = self.file_limit_message(
            channel=channel,
            sender_userid=sender_userid,
            incoming_size=file.size_bytes,
        )
        if limit_message:
            return IntakeDecision(action="wait", reply=limit_message)
        stored_file = self._persist_uploaded_file(key, file)
        session.materials.append(IntakeMaterial(kind="file", file=stored_file))
        session.updated_at = time.time()
        self._persist_session(key, session)
        if session.intent:
            return IntakeDecision(
                action="wait",
                reply=f"已收到第 {len(session.materials)} 份材料。可以继续发送材料，发完后回复“开始写”。",
            )
        return IntakeDecision(
            action="wait",
            reply="已收到文件。你希望我怎么处理？可以回复：写简报、写直报或做综合调研整合。如需润色，请直接粘贴原文。",
        )

    def file_limit_message(
        self,
        *,
        channel: str,
        sender_userid: str,
        incoming_size: int | None,
    ) -> str:
        session = self._get_active_session((channel, sender_userid))
        file_materials = [
            item.file
            for item in (session.materials if session else [])
            if item.kind == "file" and item.file is not None
        ]
        violation = check_intake_file_limits(
            file_materials,
            incoming_size=incoming_size,
            max_files=self._max_files,
            max_total_file_bytes=self._max_total_file_bytes,
        )
        if violation and violation.code == "too_many_files":
            return f"每次任务最多接收 {self._max_files} 个文件，请先回复“开始写”处理当前材料。"
        if violation and violation.code == "total_size_exceeded":
            limit_mb = self._max_total_file_bytes // 1024 // 1024
            if limit_mb:
                return f"本次任务文件总大小不能超过 {limit_mb}MB，请减少文件后重试。"
            return f"本次任务文件总大小不能超过 {self._max_total_file_bytes} 字节，请减少文件后重试。"
        return ""

    def clear(self, *, channel: str, sender_userid: str) -> None:
        key = (channel, sender_userid)
        self._sessions.pop(key, None)
        self._remove_persisted_session(key, preserve_files=False)

    def mark_clarification(
        self,
        *,
        channel: str,
        sender_userid: str,
        message: str,
    ) -> None:
        key = (channel, sender_userid)
        session = self._get_active_session(key)
        if session is None:
            return
        session.awaiting_clarification = True
        session.clarification_message = message.strip()
        session.updated_at = time.time()
        self._persist_session(key, session)

    def restore_clarification(
        self,
        *,
        channel: str,
        sender_userid: str,
        skill_id: str,
        text: str,
        material_text: str,
        urls: tuple[str, ...],
        files: tuple[UploadedFile, ...],
        message: str,
    ) -> None:
        key = (channel, sender_userid)
        session = self._get_active_session(key)
        if session is None:
            intent = DIRECT_REPORT_INTENT if skill_id == "direct_report" else BRIEF_INTENT
            session = WritingIntakeSession(
                intent=intent,
                instructions=[text.strip()] if text.strip() else [],
            )
            for url in urls:
                if str(url).strip():
                    session.materials.append(IntakeMaterial(kind="url", url=str(url).strip()))
            if material_text.strip():
                session.materials.append(IntakeMaterial(kind="text", text=material_text.strip()))
            for item in files:
                session.materials.append(
                    IntakeMaterial(kind="file", file=self._persist_uploaded_file(key, item))
                )
            self._sessions[key] = session
        session.awaiting_clarification = True
        session.clarification_message = message.strip()
        session.updated_at = time.time()
        self._persist_session(key, session)

    def _get_active_session(self, key: tuple[str, str]) -> WritingIntakeSession | None:
        session = self._sessions.get(key)
        if session is None:
            session = self._load_session(key)
            if session is not None:
                self._sessions[key] = session
        if session is None:
            return None
        if time.time() - session.updated_at > self._ttl_seconds:
            self._sessions.pop(key, None)
            self._remove_persisted_session(key, preserve_files=False)
            return None
        return session

    def _run_or_ask_more(self, key: tuple[str, str], session: WritingIntakeSession) -> IntakeDecision:
        if not session.intent:
            options = "写简报、写直报、做综合调研整合，还是润色" if any(item.kind == "text" for item in session.materials) else "写简报、写直报，还是做综合调研整合"
            return IntakeDecision(
                action="wait",
                reply=f"材料已收到。请再告诉我你要{options}。",
            )
        if session.intent == REWRITE_INTENT and not any(item.kind == "text" for item in session.materials):
            return IntakeDecision(action="wait", reply=_reply_for_waiting_material(REWRITE_INTENT))
        if not session.materials:
            return IntakeDecision(
                action="wait",
                reply=_reply_for_waiting_material(session.intent),
            )
        return self._build_run_decision(key, session)

    def _build_run_decision(self, key: tuple[str, str], session: WritingIntakeSession) -> IntakeDecision:
        skill_id = resolve_skill_id(session)
        material_texts = [item.text for item in session.materials if item.kind == "text" and item.text]
        if skill_id == REWRITE_INTENT:
            urls: tuple[str, ...] = ()
            files: tuple[UploadedFile, ...] = ()
        else:
            urls = tuple(item.url for item in session.materials if item.kind == "url" and item.url)
            files = tuple(item.file for item in session.materials if item.kind == "file" and item.file is not None)
        return IntakeDecision(
            action="run",
            skill_id=skill_id,
            text="\n".join(session.instructions).strip(),
            material_text="\n\n".join(material_texts).strip(),
            urls=urls,
            files=files,
            ack_message=f"收到，正在按{_skill_label(skill_id)}流程处理，请稍后……",
        )

    def _persist_uploaded_file(self, key: tuple[str, str], file: UploadedFile) -> UploadedFile:
        return self._persistence.persist_file(key, file)

    def _persist_session(self, key: tuple[str, str], session: WritingIntakeSession) -> None:
        payload = {
            "intent": session.intent,
            "instructions": list(session.instructions),
            "awaiting_clarification": session.awaiting_clarification,
            "clarification_message": session.clarification_message,
            "processed_message_ids": list(session.processed_message_ids),
            "created_at": session.created_at,
            "updated_at": session.updated_at,
            "materials": [_material_to_dict(item) for item in session.materials],
        }
        self._persistence.save_state(key, payload)

    def _load_session(self, key: tuple[str, str]) -> WritingIntakeSession | None:
        payload = self._persistence.load_state(key)
        if payload is None:
            return None
        try:
            materials = [self._material_from_dict(key, item) for item in payload.get("materials", [])]
            return WritingIntakeSession(
                intent=str(payload.get("intent")) if payload.get("intent") else None,
                materials=[item for item in materials if item is not None],
                instructions=[str(item) for item in payload.get("instructions", [])],
                awaiting_clarification=bool(payload.get("awaiting_clarification", False)),
                clarification_message=str(payload.get("clarification_message", "")),
                processed_message_ids=[
                    str(item)
                    for item in list(payload.get("processed_message_ids", []))
                    if str(item).strip()
                ][-100:],
                created_at=float(payload.get("created_at", time.time())),
                updated_at=float(payload.get("updated_at", time.time())),
            )
        except (ValueError, TypeError):
            self._remove_persisted_session(key, preserve_files=False)
            return None

    def _material_from_dict(self, key: tuple[str, str], payload: object) -> IntakeMaterial | None:
        if not isinstance(payload, dict):
            return None
        kind = str(payload.get("kind", ""))
        if kind == "file":
            restored = self._persistence.restore_file(key, payload)
            return IntakeMaterial(kind="file", file=restored) if restored is not None else None
        if kind == "url":
            return IntakeMaterial(kind="url", url=str(payload.get("url", "")))
        if kind == "text":
            return IntakeMaterial(kind="text", text=str(payload.get("text", "")))
        return None

    def _remove_persisted_session(self, key: tuple[str, str], *, preserve_files: bool) -> None:
        self._persistence.clear(key, preserve_files=preserve_files)

    def _delete_stored_file(self, file: UploadedFile | None) -> None:
        if file is not None:
            self._persistence.delete_file(file)


def detect_writing_intent(text: str) -> str | None:
    if any(marker in text for marker in ("综合调研", "调研材料整合", "调研材料汇总", "调研材料做个汇总", "按提纲整合", "按提纲汇总", "按调研提纲整合", "按调研提纲汇总")):
        return RESEARCH_SYNTHESIS_INTENT
    if "调研材料" in text and any(marker in text for marker in ("整合", "汇总")):
        return RESEARCH_SYNTHESIS_INTENT
    if "直报" in text:
        return DIRECT_REPORT_INTENT
    if "简报" in text:
        return BRIEF_INTENT
    if any(word in text for word in ("改写", "润色", "优化", "修改", "改稿")):
        return REWRITE_INTENT
    return None


def is_writing_cancel_signal(text: str) -> bool:
    normalized = re.sub(r"[\s，。！？,.!?；;:：]+", "", text.strip())
    return normalized in _CANCEL_SIGNALS


def _is_duplicate_message(session: WritingIntakeSession, message_id: str) -> bool:
    normalized = str(message_id or "").strip()
    return bool(normalized and normalized in session.processed_message_ids)


def _remember_message_id(session: WritingIntakeSession, message_id: str) -> None:
    normalized = str(message_id or "").strip()
    if not normalized or normalized in session.processed_message_ids:
        return
    session.processed_message_ids.append(normalized)
    if len(session.processed_message_ids) > 100:
        del session.processed_message_ids[:-100]


def extract_urls(text: str) -> tuple[str, ...]:
    seen: set[str] = set()
    urls: list[str] = []
    for match in URL_RE.findall(text):
        url = match.strip().rstrip("，。；;、)")
        if url and url not in seen:
            urls.append(url)
            seen.add(url)
    return tuple(urls)


def looks_like_material_text(text: str) -> bool:
    without_urls = URL_RE.sub("", text).strip()
    if len(without_urls) >= 60:
        return True
    return "\n" in without_urls and len(without_urls) >= 30


def is_start_signal(text: str) -> bool:
    normalized = re.sub(r"\s+", "", text)
    if len(normalized) > 20:
        return False
    return normalized in {
        "开始",
        "开始写",
        "开始处理",
        "可以写",
        "可以写了",
        "写吧",
        "生成",
        "就这些",
        "就这几个",
        "材料齐了",
        "材料发完了",
    }


def resolve_skill_id(session: WritingIntakeSession) -> str:
    if session.intent == RESEARCH_SYNTHESIS_INTENT:
        return RESEARCH_SYNTHESIS_INTENT
    if session.intent == DIRECT_REPORT_INTENT:
        return "direct_report"
    if session.intent == REWRITE_INTENT:
        return "rewrite"
    material_count = len(session.materials)
    return "writer2" if material_count >= 2 else "writer1"


def _add_text_materials(session: WritingIntakeSession, text: str, urls: tuple[str, ...]) -> int:
    added = 0
    without_urls = URL_RE.sub("", text).strip()
    for url in urls:
        session.materials.append(IntakeMaterial(kind="url", url=url))
        added += 1
    if without_urls and looks_like_material_text(text):
        session.materials.append(IntakeMaterial(kind="text", text=without_urls))
        added += 1
    return added


def _add_rewrite_text_material(session: WritingIntakeSession, text: str) -> int:
    without_urls = URL_RE.sub("", text).strip()
    if without_urls and looks_like_material_text(without_urls):
        session.materials.append(IntakeMaterial(kind="text", text=without_urls))
        return 1
    return 0


def _is_pure_intent_text(text: str, intent: str) -> bool:
    normalized = re.sub(r"\s+", "", text)
    pure_values = {
        DIRECT_REPORT_INTENT: {"写直报", "帮我写直报", "做直报", "写一个直报"},
        BRIEF_INTENT: {"写简报", "帮我写简报", "做简报", "写一个简报"},
        REWRITE_INTENT: {"改写", "帮我改写", "润色", "帮我润色", "修改", "改稿"},
        RESEARCH_SYNTHESIS_INTENT: {"综合调研", "做综合调研", "综合调研材料整合", "帮我做综合调研材料整合", "按提纲整合"},
    }
    return normalized in pure_values.get(intent, set())


def _reply_for_waiting_material(intent: str) -> str:
    if intent == RESEARCH_SYNTHESIS_INTENT:
        return "收到，准备做综合调研整合。请发送 1 份调研提纲和各部门 Word/PDF/PPTX 素材，发完后回复“开始写”。"
    if intent == DIRECT_REPORT_INTENT:
        return "收到，准备写直报。请继续发送链接、文字或文件素材，发完后回复“开始写”。"
    if intent == REWRITE_INTENT:
        return "材料润色当前只支持直接粘贴文字。请把待润色原文直接粘贴过来，发完后回复“开始写”。"
    return "收到，准备写简报。请继续发送一个或多个链接、文字或文件素材，发完后回复“开始写”。"


def _skill_label(skill_id: str) -> str:
    labels = {
        "direct_report": "直报写作",
        "writer1": "简报写作",
        "writer2": "多素材简报写作",
        "rewrite": "材料润色",
        "research_synthesis": "综合调研整合",
    }
    return labels.get(skill_id, "写作")


def _material_to_dict(material: IntakeMaterial) -> dict[str, object]:
    if material.kind == "file" and material.file is not None:
        return {
            "kind": "file",
            "filename": material.file.filename,
            "content_type": material.file.content_type,
            "stored_path": material.file.stored_path,
            "size_bytes": material.file.size_bytes,
        }
    if material.kind == "url":
        return {"kind": "url", "url": material.url}
    return {"kind": "text", "text": material.text}
