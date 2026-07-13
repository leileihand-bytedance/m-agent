import re

from app.platform.models import RoutedRequest
from app.platform.registry import SkillRegistry


URL_RE = re.compile(r"https?://[^\s，。；;）)]+")
INLINE_REWRITE_STYLE_MARKERS = ("润色", "改写", "优化", "更正式", "更简洁", "口语化", "顺一下")
INLINE_REWRITE_SUBJECT_MARKERS = ("这段", "下面", "以下", "原文", "文字", "这句话")


def route_message(message: str, registry: SkillRegistry) -> RoutedRequest:
    normalized = message.strip()
    urls = URL_RE.findall(normalized)

    if "简报" in normalized and len(urls) >= 2 and registry.get("writer2").enabled:
        return RoutedRequest(
            skill_id="writer2",
            confidence=0.9,
            needs_clarification=False,
            message="已识别为简报写作（多素材）。",
            inputs={"text": normalized, "urls": urls},
        )

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
        message="我还不确定你要做什么。你是想写直报、写简报、润色文字，还是审核文档？",
        inputs={"text": normalized, "urls": urls},
    )


def _looks_like_inline_rewrite_task(message: str, registry: SkillRegistry) -> bool:
    try:
        rewrite_skill = registry.get("rewrite")
    except KeyError:
        return False
    if not rewrite_skill.enabled:
        return False
    if not any(marker in message for marker in INLINE_REWRITE_STYLE_MARKERS):
        return False
    if not any(marker in message for marker in INLINE_REWRITE_SUBJECT_MARKERS):
        return False
    for separator in ("：", ":"):
        if separator not in message:
            continue
        _, content = message.split(separator, 1)
        if len(content.strip()) >= 8:
            return True
    if "\n\n" in message:
        _, content = re.split(r"\n\s*\n", message, maxsplit=1)
        return len(content.strip()) >= 8
    return False
