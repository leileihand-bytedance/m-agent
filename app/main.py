"""M-Agent WeCom text gateway.

Phase 1 scope: connect to the WeCom intelligent bot, receive text messages,
and send a minimal reply. File handling and model calls are later phases.
"""

import argparse
import asyncio
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Mapping
import re
import zipfile
import xml.etree.ElementTree as ET


class SessionState(Enum):
    IDLE = "idle"
    COLLECTING = "collecting"
    EXTRACTING = "extracting"
    WAITING_CONFIRMATION = "waiting_confirmation"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


# Import AgentCore - delay import to avoid circular dependency
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from agent import AgentCore


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ENV_PATH = ROOT / ".env"


@dataclass(frozen=True)
class AppConfig:
    wecom_bot_id: str
    wecom_bot_secret: str
    model_name: str
    anthropic_api_key: str
    anthropic_base_url: str
    data_dir: Path


@dataclass(frozen=True)
class SavedFile:
    file_path: Path
    meta_path: Path


@dataclass(frozen=True)
class FilePayload:
    url: str
    aes_key: str | None
    filename: str | None


@dataclass(frozen=True)
class ParsedFile:
    source_path: Path
    parsed_path: Path | None
    success: bool
    message: str
    text_length: int = 0


@dataclass(frozen=True)
class TextIntent:
    leader: str | None
    material: str | None


@dataclass(frozen=True)
class PendingMaterial:
    kind: str
    content: bytes | str
    original_filename: str | None
    sender: str
    msgid: str


@dataclass(frozen=True)
class ProcessedMaterial:
    saved: SavedFile
    parsed: ParsedFile


class SessionStore:
    def __init__(self) -> None:
        self._leaders_by_user: dict[str, str] = {}
        self._next_leaders_by_user: dict[str, str] = {}
        self._pending_materials_by_user: dict[str, PendingMaterial] = {}
        self._states: dict[str, SessionState] = {}
        self._suggestions_by_user: dict[str, Path] = {}

    def start_style_collection(self, user_id: str, leader: str) -> str:
        normalized = clean_leader_name(leader)
        self._leaders_by_user[user_id] = normalized
        self.remember_next_leader(user_id, normalized)
        return f"收到，接下来请发送要提炼到\"{normalized}\"的文件或文字材料。"

    def get_leader(self, user_id: str) -> str | None:
        return self._leaders_by_user.get(user_id)

    def remember_next_leader(self, user_id: str, leader: str) -> None:
        self._next_leaders_by_user[user_id] = clean_leader_name(leader)

    def pop_next_leader(self, user_id: str) -> str | None:
        return self._next_leaders_by_user.pop(user_id, None)

    def remember_pending_material(self, user_id: str, material: PendingMaterial) -> None:
        self._pending_materials_by_user[user_id] = material

    def pop_pending_material(self, user_id: str) -> PendingMaterial | None:
        return self._pending_materials_by_user.pop(user_id, None)

    def get_state(self, user_id: str) -> SessionState:
        return self._states.get(user_id, SessionState.IDLE)

    def set_state(self, user_id: str, state: SessionState) -> None:
        self._states[user_id] = state

    def clear_session(self, user_id: str) -> None:
        self._leaders_by_user.pop(user_id, None)
        self._next_leaders_by_user.pop(user_id, None)
        self._pending_materials_by_user.pop(user_id, None)
        self._states.pop(user_id, None)

    def remember_suggestion(self, user_id: str, suggestion_path: Path) -> None:
        self._suggestions_by_user[user_id] = suggestion_path

    def pop_suggestion(self, user_id: str) -> Path | None:
        return self._suggestions_by_user.pop(user_id, None)


LEADER_PATTERN = re.compile(
    r"([\u4e00-\u9fffA-Za-z0-9]{1,10}(?:董事长|行长|书记|主任|局长|处长|经理|部长|院长|总)(?:测试)?)"
)


def clean_leader_name(value: str) -> str:
    return value.strip().strip(" \t\r\n，,。.;；:：、+（）()[]【】")


def looks_like_leader_name(value: str) -> bool:
    text = clean_leader_name(value)
    if not text or len(text) > 12:
        return False
    return bool(LEADER_PATTERN.fullmatch(text))


def clean_inline_material(value: str) -> str | None:
    text = value.strip().lstrip("：:，,。；;、+--- ").strip()
    if not text:
        return None
    if text in {"文件", "材料", "附件", "附上文件", "附文件", "见附件", "这个", "这段"}:
        return None
    if "文件" in text and len(text) <= 8:
        return None
    return text


def extract_leader_from_instruction(text: str) -> tuple[str, int] | None:
    instruction = re.search(
        r"(?:提炼|沉淀|整理|归纳|总结)[\u4e00-\u9fffA-Za-z0-9\s]{0,8}?到\s*" + LEADER_PATTERN.pattern,
        text,
    )
    if instruction:
        leader = clean_leader_name(instruction.group(1))
        return leader, instruction.end(1)
    return None


def analyze_text_message(content: str) -> TextIntent:
    text = content.strip()
    if not text:
        return TextIntent(leader=None, material=None)

    old_prefix = "沉淀领导风格"
    if text.startswith(old_prefix):
        leader = clean_leader_name(text.removeprefix(old_prefix).lstrip("：: "))
        return TextIntent(leader=leader or None, material=None)

    instruction = extract_leader_from_instruction(text)
    if instruction:
        leader, leader_end = instruction
        return TextIntent(
            leader=leader,
            material=clean_inline_material(text[leader_end:]),
        )

    for separator in ("：", ":"):
        if separator in text:
            left, right = text.split(separator, 1)
            if looks_like_leader_name(left):
                return TextIntent(
                    leader=clean_leader_name(left),
                    material=clean_inline_material(right),
                )

    leader_candidate = text.replace("+文件", "").replace("+文件", "").strip()
    if looks_like_leader_name(leader_candidate):
        return TextIntent(leader=clean_leader_name(leader_candidate), material=None)

    return TextIntent(leader=None, material=text)


def parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def require_value(values: Mapping[str, str], key: str) -> str:
    value = values.get(key, "").strip()
    if not value:
        raise ValueError(f"Missing required config: {key}")
    return value


def load_config(env_path: Path = DEFAULT_ENV_PATH) -> AppConfig:
    values = parse_env_file(env_path)
    return AppConfig(
        wecom_bot_id=require_value(values, "WECOM_BOT_ID"),
        wecom_bot_secret=require_value(values, "WECOM_BOT_SECRET"),
        model_name=values.get("MODEL_NAME", "MiniMax-M2.7") or "MiniMax-M2.7",
        anthropic_api_key=require_value(values, "ANTHROPIC_API_KEY"),
        anthropic_base_url=values.get("ANTHROPIC_BASE_URL", "https://api.minimaxi.com/anthropic") or "https://api.minimaxi.com/anthropic",
        data_dir=Path(values.get("M_AGENT_DATA_DIR", "data") or "data"),
    )


def build_text_reply(content: str) -> str:
    text = content.strip()
    if not text:
        return "没有收到有效文字。你可以发送：沉淀领导风格：张总"

    prefix = "沉淀领导风格"
    if text.startswith(prefix):
        leader = text.removeprefix(prefix).lstrip("：: ").strip()
        if not leader:
            return "请按这个格式发送：沉淀领导风格：张总"
        return f"收到。已进入\"{leader}\"领导风格材料收集状态，请继续发送材料。"

    return f"M-Agent 已收到：{text}"


def parse_style_collection_leader(content: str) -> str | None:
    text = content.strip()
    prefix = "沉淀领导风格"
    if not text.startswith(prefix):
        return None
    leader = text.removeprefix(prefix).lstrip("：: ").strip()
    return leader or None


def get_sender_id(frame: Mapping[str, object]) -> str:
    body = frame.get("body")
    if not isinstance(body, Mapping):
        return "unknown"
    sender = body.get("from")
    if isinstance(sender, Mapping):
        value = sender.get("userid")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return "unknown"


def get_string_value(values: Mapping[str, object], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = values.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def get_file_info(frame: Mapping[str, object]) -> Mapping[str, object] | None:
    body = frame.get("body")
    if not isinstance(body, Mapping):
        return None
    file_info = body.get("file")
    if not isinstance(file_info, Mapping):
        return None
    return file_info


def extract_file_payload(frame: Mapping[str, object]) -> FilePayload | None:
    file_info = get_file_info(frame)
    if file_info is None:
        return None

    url = get_string_value(
        file_info,
        (
            "url",
            "download_url",
            "downloadUrl",
            "file_url",
            "fileUrl",
        ),
    )
    if url is None:
        return None

    aes_key = get_string_value(file_info, ("aeskey", "aes_key", "aesKey"))
    filename = get_string_value(file_info, ("filename", "file_name", "fileName", "name"))
    return FilePayload(url=url, aes_key=aes_key, filename=filename)


def describe_file_message_structure(frame: Mapping[str, object]) -> str:
    body = frame.get("body")
    if not isinstance(body, Mapping):
        return "body字段：无有效结构；file字段：无"

    body_keys = ", ".join(sorted(str(key) for key in body.keys())) or "无"
    file_info = body.get("file")
    if isinstance(file_info, Mapping):
        file_keys = ", ".join(sorted(str(key) for key in file_info.keys())) or "无"
    else:
        file_keys = f"无有效结构（类型：{type(file_info).__name__}）"
    return f"body字段：{body_keys}；file字段：{file_keys}"


def sanitize_path_component(value: str, fallback: str) -> str:
    cleaned = []
    for char in value.strip():
        if char.isalnum() or char in ("-", "_", "."):
            cleaned.append(char)
        elif char.isspace() or char in ("/", "\\", ":", "："):
            cleaned.append("_")
    result = "".join(cleaned).strip("._")
    return result or fallback


def save_downloaded_file(
    *,
    data_dir: Path,
    leader: str,
    file_bytes: bytes,
    original_filename: str | None,
    sender: str,
    msgid: str,
) -> SavedFile:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    leader_dir = data_dir / "leaders" / sanitize_path_component(leader, "unknown-leader") / "source"
    leader_dir.mkdir(parents=True, exist_ok=True)

    safe_name = sanitize_path_component(original_filename or "uploaded-file", "uploaded-file")
    file_path = leader_dir / f"{timestamp}-{safe_name}"
    meta_path = leader_dir / f"{timestamp}-meta.md"

    file_path.write_bytes(file_bytes)
    meta_path.write_text(
        "\n".join(
            [
                "# 文件接收记录",
                "",
                f"领导：{leader}",
                f"发送人：{sender}",
                f"消息 ID：{msgid}",
                f"原始文件名：{original_filename or '未知'}",
                f"保存文件：{file_path.name}",
                f"接收时间：{timestamp}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return SavedFile(file_path=file_path, meta_path=meta_path)


def read_text_with_fallback(path: Path) -> str:
    last_error: UnicodeDecodeError | None = None
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError as exc:
            last_error = exc
    if last_error:
        raise last_error
    return path.read_text(encoding="utf-8")


def extract_docx_text(path: Path) -> str:
    with zipfile.ZipFile(path) as archive:
        document_xml = archive.read("word/document.xml")

    root = ET.fromstring(document_xml)
    namespace = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    paragraphs: list[str] = []
    for paragraph in root.iter(f"{namespace}p"):
        parts = [
            node.text
            for node in paragraph.iter(f"{namespace}t")
            if node.text and node.text.strip()
        ]
        if parts:
            paragraphs.append("".join(parts))
    return "\n".join(paragraphs)


def extract_pdf_text(path: Path) -> str:
    try:
        from pypdf import PdfReader
    except ImportError:
        try:
            from PyPDF2 import PdfReader
        except ImportError as exc:
            raise RuntimeError("当前环境缺少 PDF 解析依赖，暂时无法解析 PDF。") from exc

    reader = PdfReader(str(path))
    pages: list[str] = []
    for page in reader.pages:
        text = page.extract_text() or ""
        if text.strip():
            pages.append(text.strip())
    return "\n\n".join(pages)


def parse_source_file(source_path: Path) -> ParsedFile:
    suffix = source_path.suffix.lower()
    try:
        if suffix == ".md":
            text = read_text_with_fallback(source_path)
        elif suffix == ".txt":
            text = read_text_with_fallback(source_path)
        elif suffix == ".docx":
            text = extract_docx_text(source_path)
        elif suffix == ".pdf":
            text = extract_pdf_text(source_path)
        else:
            return ParsedFile(
                source_path=source_path,
                parsed_path=None,
                success=False,
                message=f"暂不支持解析 {suffix or '无后缀'} 文件。",
            )
    except Exception as exc:
        return ParsedFile(
            source_path=source_path,
            parsed_path=None,
            success=False,
            message=f"解析失败：{exc}",
        )

    normalized = text.strip()
    if not normalized:
        return ParsedFile(
            source_path=source_path,
            parsed_path=None,
            success=False,
            message="文件中未提取到有效文本。",
        )

    if suffix == ".md":
        return ParsedFile(
            source_path=source_path,
            parsed_path=source_path,
            success=True,
            message=f"Markdown 文件可直接用于提炼，正文 {len(normalized)} 个字符。",
            text_length=len(normalized),
        )

    parsed_path = source_path.with_name(f"{source_path.stem}.parsed.md")
    parsed_path.write_text(
        "\n".join(
            [
                "# 解析文本",
                "",
                f"来源文件：{source_path.name}",
                f"解析时间：{datetime.now().strftime('%Y%m%d-%H%M%S')}",
                "",
                "---",
                "",
                normalized,
                "",
            ]
        ),
        encoding="utf-8",
    )
    return ParsedFile(
        source_path=source_path,
        parsed_path=parsed_path,
        success=True,
        message=f"解析成功，提取 {len(normalized)} 个字符。",
        text_length=len(normalized),
    )


def process_material(*, data_dir: Path, leader: str, material: PendingMaterial) -> ProcessedMaterial:
    if material.kind == "text":
        if not isinstance(material.content, str):
            raise ValueError("文字材料内容格式不正确。")
        file_bytes = material.content.encode("utf-8")
        original_filename = material.original_filename or "text-message.md"
    elif material.kind == "file":
        if not isinstance(material.content, bytes):
            raise ValueError("文件材料内容格式不正确。")
        file_bytes = material.content
        original_filename = material.original_filename
    else:
        raise ValueError(f"未知材料类型：{material.kind}")

    saved = save_downloaded_file(
        data_dir=data_dir,
        leader=leader,
        file_bytes=file_bytes,
        original_filename=original_filename,
        sender=material.sender,
        msgid=material.msgid,
    )
    parsed = parse_source_file(saved.file_path)
    return ProcessedMaterial(saved=saved, parsed=parsed)


def build_material_reply(leader: str, processed: ProcessedMaterial) -> str:
    saved = processed.saved
    parsed = processed.parsed
    if parsed.success and parsed.parsed_path is not None:
        if parsed.parsed_path == saved.file_path:
            return (
                f"已收到并保存\"{leader}\"的材料：{saved.file_path.name}。\n"
                f"{parsed.message}\n"
                "下一步可进入风格提炼。"
            )
        return (
            f"已收到并保存\"{leader}\"的材料：{saved.file_path.name}。\n"
            f"已解析为：{parsed.parsed_path.name}。\n"
            "下一步可进入风格提炼。"
        )

    return (
        f"已收到并保存\"{leader}\"的材料：{saved.file_path.name}。\n"
        f"但暂时未能解析：{parsed.message}"
    )


def get_message_id(frame: Mapping[str, object]) -> str:
    body = frame.get("body")
    if isinstance(body, Mapping):
        msgid = body.get("msgid")
        if isinstance(msgid, str) and msgid.strip():
            return msgid.strip()
    headers = frame.get("headers")
    if isinstance(headers, Mapping):
        req_id = headers.get("req_id")
        if isinstance(req_id, str) and req_id.strip():
            return req_id.strip()
    return ""


async def run_wecom_bot(config: AppConfig) -> None:
    try:
        from wecom_aibot_sdk import WSClient, generate_req_id
    except ImportError as exc:
        raise RuntimeError(
            "缺少依赖 wecom-aibot-sdk。请先安装：python -m pip install -r app/requirements.txt"
        ) from exc

    ws_client = WSClient(
        bot_id=config.wecom_bot_id,
        secret=config.wecom_bot_secret,
    )

    sessions = SessionStore()
    agent = AgentCore(config)
    data_dir = config.data_dir if config.data_dir.is_absolute() else ROOT / config.data_dir

    ws_client.on("connected", lambda: print("企业微信长连接已建立。", flush=True))
    ws_client.on("authenticated", lambda: print("企业微信机器人认证成功，等待消息。", flush=True))
    ws_client.on("disconnected", lambda reason: print(f"企业微信连接已断开：{reason}", flush=True))
    ws_client.on("reconnecting", lambda attempt: print(f"企业微信正在重连，第 {attempt} 次。", flush=True))
    ws_client.on("error", lambda error: print(f"企业微信连接错误：{error}", flush=True))

    async def on_text(frame):
        content = frame.get("body", {}).get("text", {}).get("content", "")
        sender = get_sender_id(frame)

        # Delegate to AgentCore
        result = agent.process(content, sender)
        reply = result.message

        stream_id = generate_req_id("m-agent")
        await ws_client.reply_stream(frame, stream_id, reply, True)
        print(f"已回复文本消息，长度：{len(reply)}", flush=True)

    async def on_file(frame):
        sender = get_sender_id(frame)
        stream_id = generate_req_id("m-agent-file")

        body = frame.get("body", {})
        payload = extract_file_payload(frame)
        if payload is None:
            structure = describe_file_message_structure(frame)
            print(f"文件消息缺少下载地址。{structure}", flush=True)
            await ws_client.reply_stream(
                frame,
                stream_id,
                f"文件消息已收到，但暂时没找到下载地址。诊断信息：{structure}",
                True,
            )
            return

        result = await ws_client.download_file(payload.url, payload.aes_key)
        buffer = result.get("buffer", b"")
        filename = result.get("filename") or payload.filename
        material = PendingMaterial(
            kind="file",
            content=buffer,
            original_filename=filename,
            sender=sender,
            msgid=str(body.get("msgid") or frame.get("headers", {}).get("req_id", "")),
        )
        leader = sessions.pop_next_leader(sender)
        if leader:
            processed = process_material(data_dir=data_dir, leader=leader, material=material)
            reply = build_material_reply(leader, processed)
            print(f"已保存文件：{processed.saved.file_path}", flush=True)
            print(f"文件解析结果：{processed.parsed.message}", flush=True)
        else:
            sessions.remember_pending_material(sender, material)
            reply = "已收到文件。这份材料要提炼到哪位领导？请直接回复：黄总"
        await ws_client.reply_stream(
            frame,
            stream_id,
            reply,
            True,
        )

    async def on_enter(frame):
        await ws_client.reply_welcome(
            frame,
            {
                "msgtype": "text",
                "text": {"content": "您好，我是 M-Agent。你可以发送：沉淀领导风格：张总"},
            },
        )

    ws_client.on("message.text", on_text)
    ws_client.on("message.file", on_file)
    ws_client.on("event.enter_chat", on_enter)

    await ws_client.connect()
    try:
        await asyncio.Event().wait()
    except KeyboardInterrupt:
        await ws_client.disconnect()


def build_style_extraction_prompt(leader: str, materials: list[Path], existing_profile: str | None) -> str:
    """构建发给AI的风格提炼prompt"""
    prompt_path = Path(__file__).parent / "prompts" / "style_extraction.md"
    template = prompt_path.read_text(encoding="utf-8")

    material_contents = []
    for path in materials:
        content = path.read_text(encoding="utf-8")
        material_contents.append(f"## {path.name}\n\n{content}")

    existing = existing_profile or "（暂无已确认的档案）"
    # 简单替换占位符
    result = template.replace("{leader_name}", leader)
    result = result.replace("{material_sources}", "\n\n".join(material_contents))
    result = result.replace("{existing_profile}", existing)
    return result


def call_model(config: AppConfig, prompt: str) -> str:
    """调用 MiniMax API 生成风格提炼建议"""
    import anthropic

    client = anthropic.Anthropic(
        api_key=config.anthropic_api_key,
        base_url=config.anthropic_base_url,
    )

    message = client.messages.create(
        model=config.model_name,
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    )

    text_parts = []
    for block in message.content:
        if hasattr(block, "text") and block.text:
            text_parts.append(block.text)
        elif hasattr(block, "thinking") and block.thinking:
            text_parts.append(block.thinking)

    return "\n".join(text_parts) if text_parts else ""


def extract_style_suggestion(ai_output: str) -> tuple[list[str], list[str], list[str], list[str], list[str], list[str], list[str]]:
    """从AI输出中解析出结构化建议

    Returns: (sources, observations, suggestions, accepted, avoided, not_recommended, questions)
    """
    sections = {}
    current_section = None
    current_content = []

    for line in ai_output.split("\n"):
        if line.startswith("## "):
            if current_section:
                sections[current_section] = "\n".join(current_content).strip()
            # 去掉 "## " 前缀和编号
            section_title = line[3:].strip()
            # 去掉开头的编号如 "1. " 或 "1、"
            import re
            section_title = re.sub(r"^\d+[.、]\s*", "", section_title)
            current_section = section_title
            current_content = []
        else:
            current_content.append(line)

    if current_section:
        sections[current_section] = "\n".join(current_content).strip()

    def extract_list(section_key: str) -> list[str]:
        if section_key not in sections:
            return []
        text = sections[section_key]
        lines = [l.strip() for l in text.split("\n") if l.strip()]
        result = []
        for line in lines:
            # 跳过标题行和非列表项
            if line.startswith("#"):
                continue
            # 如果不是编号行或 - 开头，可能需要截取
            if line and not line[0].isdigit() and not line.startswith("-"):
                continue
            # 提取编号后的内容或 - 后面的内容
            # 同时处理中文和英文标点
            cleaned = line.lstrip("0123456789.） ").strip("- ")
            # 处理中文标点结尾
            for punct in "。；，,":
                if cleaned.endswith(punct):
                    cleaned = cleaned[:-1]
            if cleaned:
                result.append(cleaned)
        return result

    # 构建 section key 到中文名称的映射（处理 AI 输出中带编号的情况）
    key_mapping = {
        "材料来源": "材料来源",
        "本次观察到的风格倾向": "本次观察到的风格倾向",
        "建议写入 profile.md 的内容": "建议写入 profile.md 的内容",
        "建议加入常用表达": "建议加入常用表达",
        "建议加入慎用表达": "建议加入慎用表达",
        "不建议沉淀的内容": "不建议沉淀的内容",
        "需要用户确认的问题": "需要用户确认的问题",
    }

    # 尝试用中文名称匹配
    def find_section(key):
        if key in sections:
            return sections[key]
        # 尝试模糊匹配（去掉编号前缀）
        for section_key, section_value in sections.items():
            if key in section_key:
                return section_value
        return ""

    sources = extract_list("材料来源") if "材料来源" in sections else []
    observations = extract_list("本次观察到的风格倾向") if "本次观察到的风格倾向" in sections else []
    suggestions = extract_list("建议写入 profile.md 的内容") if "建议写入 profile.md 的内容" in sections else []
    accepted = extract_list("建议加入常用表达") if "建议加入常用表达" in sections else []
    avoided = extract_list("建议加入慎用表达") if "建议加入慎用表达" in sections else []
    not_recommended = extract_list("不建议沉淀的内容") if "不建议沉淀的内容" in sections else []
    questions = extract_list("需要用户确认的问题") if "需要用户确认的问题" in sections else []

    # 如果没有匹配到，尝试遍历 sections 模糊匹配
    if not suggestions:
        for section_key, section_value in sections.items():
            if "建议写入" in section_key and "profile" in section_key:
                suggestions = extract_list(section_key)
                break

    return sources, observations, suggestions, accepted, avoided, not_recommended, questions


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="M-Agent WeCom text gateway")
    parser.add_argument(
        "--check-config",
        action="store_true",
        help="只检查本地配置，不连接企业微信",
    )
    args = parser.parse_args(argv)

    config = load_config()
    if args.check_config:
        print("配置检查通过。")
        print(f"模型：{config.model_name}")
        print(f"数据目录：{config.data_dir}")
        return

    print("正在连接企业微信智能机器人。按 Ctrl+C 可停止。", flush=True)
    asyncio.run(run_wecom_bot(config))


if __name__ == "__main__":
    main()
