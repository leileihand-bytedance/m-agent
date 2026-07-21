from collections.abc import Callable, Mapping
from dataclasses import dataclass

from app.platform.models import PlatformResult


@dataclass(frozen=True)
class WeComTextMessage:
    sender_userid: str
    content: str


def extract_message_id(frame: Mapping[str, object]) -> str:
    """提取企业微信稳定消息标识，供幂等任务键复用。"""

    candidates: list[object] = [frame.get("msgid")]
    body = frame.get("body")
    headers = frame.get("headers")
    if isinstance(body, Mapping):
        candidates.append(body.get("msgid"))
    if isinstance(headers, Mapping):
        candidates.append(headers.get("req_id"))
    for value in candidates:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def extract_text_message(frame: dict[str, object]) -> WeComTextMessage:
    body = _dict_value(frame, "body")
    text = _dict_value(body, "text")
    sender = _dict_value(body, "from")

    content = text.get("content", "")
    sender_userid = sender.get("userid", "unknown")

    return WeComTextMessage(
        sender_userid=str(sender_userid or "unknown"),
        content=str(content or ""),
    )


def format_text_reply(result: PlatformResult) -> str:
    if result.needs_clarification:
        return result.message
    if result.output.get("message_only") is True:
        return result.message or "处理完成。"

    title = str(result.output.get("title", "") or "").strip()
    body = str(result.output.get("body", "") or "").strip()
    revision_note = str(result.output.get("revision_note", "") or "").strip()

    parts = []
    if title:
        parts.append(title)
    if body:
        parts.append(body)
    if revision_note:
        parts.append(f"修改说明：{revision_note}")

    if parts:
        return "\n\n".join(parts)
    return result.message or "处理完成。"


def handle_text_frame(
    frame: dict[str, object],
    runner: Callable[[str], PlatformResult],
) -> str:
    message = extract_text_message(frame)
    content = message.content.strip()
    if not content:
        return "请发送要处理的文字、链接或文件。"

    result = runner(content)
    return format_text_reply(result)


def handle_text_frame_with_app(frame: dict[str, object], app: object) -> str:
    message = extract_text_message(frame)
    content = message.content.strip()
    if not content:
        return "请发送要处理的文字、链接或文件。"

    result = app.handle_text_message(
        channel="wecom",
        sender_userid=message.sender_userid,
        text=content,
    )
    return format_text_reply(result)


def _dict_value(source: dict[str, object], key: str) -> dict[str, object]:
    value = source.get(key, {})
    if isinstance(value, dict):
        return value
    return {}
