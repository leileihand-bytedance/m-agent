"""写作 Bot 多消息任务组装。

这一层只负责把用户分多条发送的“意图、素材、补充要求”组装成一次结构化写作请求。
真正的写作、权限、工具调用仍然交给 app.platform。
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re
import time

from app.platform.models import UploadedFile
from app.platform.router import URL_RE


BRIEF_INTENT = "brief"
DIRECT_REPORT_INTENT = "direct_report"
REWRITE_INTENT = "rewrite"
DEFAULT_MAX_FILES = 5
DEFAULT_MAX_TOTAL_FILE_BYTES = 20 * 1024 * 1024


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


class WritingIntakeStore:
    """内存态写作任务暂存。

    当前写作 bot 是本机单进程常驻运行，内存态足够支撑“几分钟内连续发几条消息”的场景。
    后续如果要多进程或重启续写，可以把这个接口替换成 sqlite/redis。
    """

    def __init__(
        self,
        *,
        ttl_seconds: int = 1800,
        max_files: int = DEFAULT_MAX_FILES,
        max_total_file_bytes: int = DEFAULT_MAX_TOTAL_FILE_BYTES,
    ):
        self._ttl_seconds = ttl_seconds
        self._max_files = max_files
        self._max_total_file_bytes = max_total_file_bytes
        self._sessions: dict[tuple[str, str], WritingIntakeSession] = {}

    def handle_text(self, *, channel: str, sender_userid: str, text: str) -> IntakeDecision:
        clean_text = text.strip()
        key = (channel, sender_userid)
        session = self._get_active_session(key)
        intent = detect_writing_intent(clean_text)
        urls = extract_urls(clean_text)
        has_material = bool(urls) or looks_like_material_text(clean_text)

        if session is None and intent and has_material:
            return IntakeDecision(action="bypass")

        if session is None and not intent and not has_material:
            return IntakeDecision(action="bypass")

        if session is None:
            session = WritingIntakeSession()
            self._sessions[key] = session

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
                session.materials = [item for item in session.materials if item.kind == "text"]
                if not session.materials:
                    return IntakeDecision(action="wait", reply=_reply_for_waiting_material(intent))
            if session.materials:
                return self._build_run_decision(key, session)
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
            if session.intent:
                if session.intent == REWRITE_INTENT and added == 0:
                    return IntakeDecision(action="wait", reply=_reply_for_waiting_material(REWRITE_INTENT))
                return IntakeDecision(
                    action="wait",
                    reply=f"已收到第 {len(session.materials)} 份材料。可以继续发送材料，发完后回复“开始写”。",
                )
            can_rewrite = any(item.kind == "text" for item in session.materials)
            options = "写简报、写直报或润色" if can_rewrite else "写简报或写直报"
            return IntakeDecision(action="wait", reply=f"已收到 {added or 1} 份材料。你希望我怎么处理？可以回复：{options}。")

        session.instructions.append(clean_text)
        session.updated_at = time.time()
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
    ) -> IntakeDecision:
        key = (channel, sender_userid)
        session = self._get_active_session(key)
        if session is None:
            session = WritingIntakeSession()
            self._sessions[key] = session
        if session.intent == REWRITE_INTENT:
            return IntakeDecision(action="wait", reply=_reply_for_waiting_material(REWRITE_INTENT))
        limit_message = self.file_limit_message(
            channel=channel,
            sender_userid=sender_userid,
            incoming_size=len(file.content),
        )
        if limit_message:
            return IntakeDecision(action="wait", reply=limit_message)
        session.materials.append(IntakeMaterial(kind="file", file=file))
        session.updated_at = time.time()
        if session.intent:
            return IntakeDecision(
                action="wait",
                reply=f"已收到第 {len(session.materials)} 份材料。可以继续发送材料，发完后回复“开始写”。",
            )
        return IntakeDecision(
            action="wait",
            reply="已收到文件。你希望我怎么处理？可以回复：写简报或写直报。如需润色，请直接粘贴原文。",
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
        if len(file_materials) >= self._max_files:
            return f"每次任务最多接收 {self._max_files} 个文件，请先回复“开始写”处理当前材料。"
        if incoming_size is not None:
            current_size = sum(len(item.content) for item in file_materials)
            if incoming_size < 0 or current_size + incoming_size > self._max_total_file_bytes:
                limit_mb = self._max_total_file_bytes // 1024 // 1024
                if limit_mb:
                    return f"本次任务文件总大小不能超过 {limit_mb}MB，请减少文件后重试。"
                return f"本次任务文件总大小不能超过 {self._max_total_file_bytes} 字节，请减少文件后重试。"
        return ""

    def clear(self, *, channel: str, sender_userid: str) -> None:
        self._sessions.pop((channel, sender_userid), None)

    def _get_active_session(self, key: tuple[str, str]) -> WritingIntakeSession | None:
        session = self._sessions.get(key)
        if session is None:
            return None
        if time.time() - session.updated_at > self._ttl_seconds:
            self._sessions.pop(key, None)
            return None
        return session

    def _run_or_ask_more(self, key: tuple[str, str], session: WritingIntakeSession) -> IntakeDecision:
        if not session.intent:
            options = "写简报、写直报，还是润色" if any(item.kind == "text" for item in session.materials) else "写简报还是写直报"
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
        self._sessions.pop(key, None)
        return IntakeDecision(
            action="run",
            skill_id=skill_id,
            text="\n".join(session.instructions).strip(),
            material_text="\n\n".join(material_texts).strip(),
            urls=urls,
            files=files,
            ack_message=f"收到，正在按{_skill_label(skill_id)}流程处理，请稍后……",
        )


def detect_writing_intent(text: str) -> str | None:
    if "直报" in text:
        return DIRECT_REPORT_INTENT
    if "简报" in text:
        return BRIEF_INTENT
    if any(word in text for word in ("改写", "润色", "优化", "修改", "改稿")):
        return REWRITE_INTENT
    return None


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
    }
    return normalized in pure_values.get(intent, set())


def _reply_for_waiting_material(intent: str) -> str:
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
    }
    return labels.get(skill_id, "写作")
