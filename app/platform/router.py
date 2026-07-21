import re

from app.platform.models import RoutedRequest
from app.platform.registry import SkillRegistry


URL_RE = re.compile(r"https?://[^\s，。；;）)]+")
INLINE_REWRITE_STYLE_MARKERS = ("润色", "改写", "优化", "更正式", "更简洁", "口语化", "顺一下")
INLINE_REWRITE_SUBJECT_MARKERS = ("这段", "下面", "以下", "原文", "文字", "这句话")
INLINE_REWRITE_REQUEST_MARKERS = (
    "帮我",
    "请帮我",
    "请",
    "麻烦",
    "整体",
    "全文",
    "一下",
    "一点",
    "改得",
    "改成",
    "再",
)


def route_message(message: str, registry: SkillRegistry) -> RoutedRequest:
    normalized = message.strip()
    urls = URL_RE.findall(normalized)

    if _looks_like_inline_rewrite_task(normalized, registry):
        return RoutedRequest(
            skill_id="rewrite",
            confidence=0.9,
            needs_clarification=False,
            message="已识别为材料润色。",
            inputs={"text": normalized, "urls": urls},
        )

    matches = []
    for skill in registry.list_enabled():
        matched_triggers = [trigger for trigger in skill.triggers if trigger in normalized]
        if matched_triggers:
            matches.append((max(len(trigger) for trigger in matched_triggers), skill))

    if matches:
        _, skill = sorted(matches, key=lambda item: item[0], reverse=True)[0]
        return RoutedRequest(
            skill_id=skill.id,
            confidence=0.85,
            needs_clarification=False,
            message=f"已识别为{skill.name}。",
            inputs={"text": normalized, "urls": urls},
        )

    return RoutedRequest(
        skill_id=None,
        confidence=0.0,
        needs_clarification=True,
        message="我还不确定你要做什么。你是想写直报、写简报、生成内参周报、按提纲整合综合调研材料、润色文字，还是审核文档？",
        inputs={"text": normalized, "urls": urls},
    )


def _looks_like_inline_rewrite_task(message: str, registry: SkillRegistry) -> bool:
    try:
        rewrite_skill = registry.get("rewrite")
    except KeyError:
        return False
    if not rewrite_skill.enabled:
        return False
    return looks_like_inline_rewrite_task(message)


def looks_like_inline_rewrite_task(message: str) -> bool:
    normalized = message.strip()
    if not any(marker in normalized for marker in INLINE_REWRITE_STYLE_MARKERS):
        return False
    if _matches_rewrite_blocks(normalized):
        return True
    if any(marker in normalized for marker in INLINE_REWRITE_SUBJECT_MARKERS):
        return _matches_rewrite_separator(normalized)
    return False


def _matches_rewrite_separator(message: str) -> bool:
    for separator in ("：", ":"):
        if separator not in message:
            continue
        request, content = message.split(separator, 1)
        if _looks_like_rewrite_request(request) and _looks_like_source_text(content, request):
            return True
    return False


def _matches_rewrite_blocks(message: str) -> bool:
    if "\n\n" not in message:
        return False
    blocks = [part.strip() for part in re.split(r"\n\s*\n", message) if part.strip()]
    if len(blocks) < 2:
        return False

    leading_request = blocks[0]
    trailing_request = blocks[-1]
    trailing_source = "\n\n".join(blocks[:-1]).strip()
    leading_source = "\n\n".join(blocks[1:]).strip()

    if _looks_like_rewrite_request(leading_request) and _looks_like_source_text(leading_source, leading_request):
        return True
    if _looks_like_rewrite_request(trailing_request) and _looks_like_source_text(trailing_source, trailing_request):
        return True
    return False


def _looks_like_rewrite_request(text: str) -> bool:
    normalized = text.strip()
    if not any(marker in normalized for marker in INLINE_REWRITE_STYLE_MARKERS):
        return False
    return any(marker in normalized for marker in (*INLINE_REWRITE_SUBJECT_MARKERS, *INLINE_REWRITE_REQUEST_MARKERS))


def _looks_like_source_text(text: str, request: str) -> bool:
    normalized = text.strip()
    if len(normalized) < 8:
        return False
    return len(normalized) > len(request.strip())
