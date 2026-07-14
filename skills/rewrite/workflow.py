from __future__ import annotations

import re

from app.platform.tools import ToolGateway
from skills.revision_support import build_revision_payload
from skills.rewrite.schema import RewriteResult


REWRITE_HINTS = ("润色", "改写", "优化", "正式", "简洁", "顺一下", "口语化", "文字")


def run(inputs: dict[str, object], tools: ToolGateway) -> RewriteResult:
    if inputs.get("revision"):
        return _revise_previous_text(inputs=inputs, tools=tools)

    if list(inputs.get("urls") or []) or list(inputs.get("files") or []):
        return RewriteResult(
            body="",
            needs_clarification=True,
            message="材料润色当前只支持直接粘贴文字，请把待润色原文直接粘贴过来。",
        )

    source_text, user_request = _resolve_source_and_request(inputs)
    if not source_text:
        return RewriteResult(
            body="",
            needs_clarification=True,
            message="请把需要润色的原文贴出来，我再按你的要求修改。",
        )

    request = user_request or "请在不新增事实的前提下，优化原文表达，让语句更顺、更规范。"
    draft = tools.call(
        "llm_writer",
        {
            "skill_id": "rewrite",
            "task": "rewrite",
            "instruction": request,
            "materials": [
                {
                    "title": "用户原文",
                    "text": source_text,
                    "url": "",
                    "source": "user_text",
                }
            ],
            "output_type": RewriteResult,
        },
    )
    return _normalize_result(draft)


def _revise_previous_text(inputs: dict[str, object], tools: ToolGateway) -> RewriteResult:
    payload = build_revision_payload(inputs, skill_id="rewrite")
    payload["output_type"] = RewriteResult
    draft = tools.call("llm_writer", payload)
    return _normalize_result(draft)


def _normalize_result(draft: object) -> RewriteResult:
    result = RewriteResult.model_validate(draft)
    return result.model_copy(update={"title": "", "sources": []})


def _resolve_source_and_request(inputs: dict[str, object]) -> tuple[str, str]:
    material_text = str(inputs.get("material_text", "") or "").strip()
    text = str(inputs.get("text", "") or "").strip()

    if material_text:
        return material_text, text

    if not text:
        return "", ""

    blank_line_split = re.split(r"\n\s*\n", text, maxsplit=1)
    if len(blank_line_split) == 2 and _looks_like_instruction(blank_line_split[0]):
        request = blank_line_split[0].strip()
        source_text = blank_line_split[1].strip()
        return source_text, request
    if len(blank_line_split) == 2 and _looks_like_instruction(blank_line_split[1]):
        source_text = blank_line_split[0].strip()
        request = blank_line_split[1].strip()
        if len(source_text) >= 8:
            return source_text, request

    colon_index = _find_instruction_separator(text)
    if colon_index >= 0:
        request = text[:colon_index].strip()
        source_text = text[colon_index + 1 :].strip()
        if _looks_like_instruction(request) and len(source_text) >= 8:
            return source_text, request

    if _looks_like_instruction(text):
        return "", text

    return text, "请在不新增事实的前提下，润色原文表达。"


def _find_instruction_separator(text: str) -> int:
    positions = [text.find("："), text.find(":")]
    valid_positions = [pos for pos in positions if pos >= 0]
    return min(valid_positions) if valid_positions else -1


def _looks_like_instruction(text: str) -> bool:
    normalized = text.strip()
    return any(marker in normalized for marker in REWRITE_HINTS)
