from __future__ import annotations

from enum import Enum
import re

from app.platform.router import URL_RE, looks_like_inline_rewrite_task


class ConversationIntent(str, Enum):
    REVISE_PREVIOUS = "revise_previous"
    NEW_TASK = "new_task"
    CLARIFY = "clarify"
    OUT_OF_SCOPE = "out_of_scope"


REVISION_MARKERS = (
    "修改",
    "改稿",
    "改成",
    "调整",
    "优化",
    "润色",
    "精简",
    "压缩",
    "扩写",
    "补充",
    "增加",
    "加入",
    "加进",
    "删掉",
    "删除",
    "突出",
    "强化",
    "弱化",
    "短一点",
    "长一点",
    "换个标题",
    "标题改",
    "改得",
    "更正式",
    "正式一点",
    "更简洁",
    "再改",
    "再压缩",
    "再优化",
    "太长",
    "太短",
    "口吻",
    "语气",
    "这篇",
    "这版",
    "上一版",
    "上版",
    "刚才",
    "前面",
    "原文",
    "原稿",
    "稿子",
    "初稿",
    "不对",
    "不准确",
    "应该",
    "不要",
    "去掉",
    "加上",
    "保留",
    "只留",
    "合并",
    "挪到",
    "移到",
    "替换",
    "体现",
    "全文",
    "篇幅",
    "控制一下",
    "作为正文",
    "输出Word",
    "输出word",
    "导出Word",
    "导出word",
    "Word文档",
    "word文档",
    "正式文档",
)
CRITIQUE_MARKERS = (
    "新闻稿",
    "宣传稿",
    "不像",
    "不够",
    "不太",
    "还是",
    "感觉",
    "太虚",
    "空泛",
    "虚",
    "散",
    "弱",
    "硬",
    "生硬",
    "啰嗦",
    "啰嗦",
    "标题",
    "开头",
    "结尾",
    "第一段",
    "第二段",
    "第三段",
    "政策背景",
    "主线",
    "事实",
    "数据",
)
NEW_TASK_MARKERS = (
    "写直报",
    "写简报",
    "写一篇",
    "起草",
    "生成",
)
NEW_TASK_CONTEXT_MARKERS = (
    "根据",
    "基于",
    "材料",
    "素材",
    "链接",
)
NEUTRAL_MARKERS = (
    "谢谢",
    "先看看",
    "我看看",
    "收到",
    "好的",
)


def classify_conversation_intent(
    *,
    text: str,
    has_active_conversation: bool,
    route_skill_id: str | None,
    route_needs_clarification: bool,
) -> ConversationIntent:
    normalized = text.strip()
    if not normalized:
        return ConversationIntent.CLARIFY

    if _looks_like_explicit_new_task_request(normalized):
        return ConversationIntent.NEW_TASK
    if not has_active_conversation:
        return ConversationIntent.CLARIFY
    if _looks_like_neutral_message(normalized):
        return ConversationIntent.CLARIFY
    if _looks_like_revision_request(normalized):
        return ConversationIntent.REVISE_PREVIOUS
    if route_skill_id and not route_needs_clarification:
        return ConversationIntent.NEW_TASK
    return ConversationIntent.CLARIFY


def select_draft_version(text: str, *, current_version: int) -> int | None:
    normalized = text.strip()
    if not normalized:
        return None
    if any(marker in normalized for marker in ("上一版", "上版", "前一版")):
        return max(1, current_version - 1)

    match = re.search(r"[vV]\s*(\d+)", normalized)
    if match:
        return _clamp_version(int(match.group(1)), current_version)

    match = re.search(r"第\s*(\d+)\s*版", normalized)
    if match:
        return _clamp_version(int(match.group(1)), current_version)

    chinese_numbers = {
        "一": 1,
        "二": 2,
        "两": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
        "九": 9,
    }
    match = re.search(r"第\s*([一二两三四五六七八九])\s*版", normalized)
    if match:
        return _clamp_version(chinese_numbers[match.group(1)], current_version)
    return None


def _looks_like_revision_request(text: str) -> bool:
    if URL_RE.search(text):
        return False
    return any(marker in text for marker in (*REVISION_MARKERS, *CRITIQUE_MARKERS))


def _looks_like_explicit_new_task_request(text: str) -> bool:
    if looks_like_inline_rewrite_task(text):
        return True
    if URL_RE.search(text):
        return True
    if any(marker in text for marker in NEW_TASK_MARKERS) and any(
        marker in text for marker in NEW_TASK_CONTEXT_MARKERS
    ):
        return True
    return text.startswith(("帮我写", "请帮我写", "请写"))
def _looks_like_neutral_message(text: str) -> bool:
    if len(text) > 12:
        return False
    return any(marker in text for marker in NEUTRAL_MARKERS)


def _clamp_version(version: int, current_version: int) -> int:
    if version < 1:
        return 1
    if version > current_version:
        return current_version
    return version
