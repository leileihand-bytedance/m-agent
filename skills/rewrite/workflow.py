from __future__ import annotations

import re
import sqlite3

from app.platform.revision import (
    PreparedRevision,
    RevisionEngine,
    RevisionPolicy,
    format_revision_violations,
)
from app.platform.tools import ToolGateway, ToolNotAllowedError
from skills.rewrite.schema import RewriteResult


REWRITE_HINTS = ("润色", "改写", "优化", "正式", "简洁", "顺一下", "口语化", "文字")
WEBANK_STRONG_MARKERS = (
    "微众银行",
    "深圳前海微众银行",
    "微众",
    "WeBank",
    "WEBANK",
    "微业贷",
    "微粒贷",
    "微贸贷",
)
WEBANK_CONTEXT_MARKERS = ("我行", "本行")
WEBANK_CONTEXT_TERMS = (
    "数字银行",
    "普惠金融",
    "小微企业",
    "科技金融",
    "数字金融",
    "金融科技",
    "消保",
    "消费者权益",
    "反诈",
    "无障碍",
    "绿色金融",
)
WEBANK_REFERENCE_RULES = """微众银行语料使用约束：
1. 语料只用于核对机构名称、产品名称和已有标准表述，用户原文仍是本次润色的事实边界。
2. 不得把复核语料中原文没有的任何内容写入正文；复核通过不等于允许补充。尤其不得补入数据、日期、荣誉或新事实，也不得因为语料更丰富就扩写内容。
3. 只有在语料提供唯一、直接依据时，才可规范明显写错的专有名称；不能确认时保留原文并请用户核实。
4. 用户原文与语料或不同语料之间出现实质口径冲突时，不得静默改数或自行选口径，应设置 needs_clarification=true 并说明待确认项。"""


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
    source_material = _source_material(source_text)
    bank_references = _bank_reference_materials(
        source_text=source_text,
        user_request=request,
        materials=[source_material],
        tools=tools,
    )
    instruction = _with_bank_reference_rules(request, has_references=bool(bank_references))
    draft = tools.call(
        "llm_writer",
        {
            "skill_id": "rewrite",
            "task": "rewrite",
            "instruction": instruction,
            "materials": [source_material, *bank_references],
            "output_type": RewriteResult,
        },
    )
    return _normalize_result(draft)


def _revise_previous_text(inputs: dict[str, object], tools: ToolGateway) -> RewriteResult:
    revision = RevisionEngine(
        policy=RevisionPolicy(
            supports_title=False,
            min_target_length=1,
            max_target_length=20_000,
        )
    ).prepare(
        inputs,
        skill_id="rewrite",
        tools=tools,
    )
    payload = revision.payload
    previous_body = str(inputs.get("previous_body", "") or "").strip()
    revision_request = str(inputs.get("revision_request", "") or inputs.get("text", "")).strip()
    materials = [item for item in list(payload.get("materials") or []) if isinstance(item, dict)]
    bank_references = _bank_reference_materials(
        source_text=previous_body,
        user_request=revision_request,
        materials=materials,
        tools=tools,
    )
    if bank_references:
        payload["materials"] = [*materials, *bank_references]
        payload["instruction"] = _with_bank_reference_rules(
            str(payload.get("instruction", "") or ""),
            has_references=True,
        )
    return _generate_revised_text(
        revision=revision,
        payload=payload,
        tools=tools,
    )


def _generate_revised_text(
    *,
    revision: PreparedRevision,
    payload: dict[str, object],
    tools: ToolGateway,
    feedback: str | None = None,
) -> RewriteResult:
    draft_payload = dict(payload)
    draft_payload["output_type"] = RewriteResult
    if feedback:
        draft_payload["revision_feedback"] = feedback
    result = _normalize_result(tools.call("llm_writer", draft_payload))
    _title, body = revision.apply(
        generated_title="",
        generated_body=result.body,
    )
    violations = revision.validate(revised_title="", revised_body=body)
    if violations and feedback is None:
        return _generate_revised_text(
            revision=revision,
            payload=payload,
            tools=tools,
            feedback=format_revision_violations(violations),
        )
    message = result.message
    if violations:
        message = "已完成修改，但部分改稿要求仍需人工复核。"
    return result.model_copy(
        update={
            "body": body,
            "message": message,
            "revision_plan": revision.plan.model_dump(mode="json"),
        }
    )


def _source_material(source_text: str) -> dict[str, object]:
    return {
        "title": "用户原文",
        "text": source_text,
        "url": "",
        "source": "user_text",
    }


def _bank_reference_materials(
    *,
    source_text: str,
    user_request: str,
    materials: list[dict[str, object]],
    tools: ToolGateway,
) -> list[dict[str, object]]:
    if not _looks_like_webank_material(source_text=source_text, user_request=user_request):
        return []
    try:
        packaged = tools.call(
            "bank_materials",
            user_instruction=f"核对微众银行相关专有名称和既有口径。用户润色要求：{user_request}",
            materials=materials,
            limit=3,
        )
    except (ToolNotAllowedError, KeyError, OSError, sqlite3.Error):
        return []
    if not isinstance(packaged, list):
        return []
    return [
        {**item, "material_role": "verification_reference"}
        for item in packaged
        if isinstance(item, dict) and item.get("source") == "bank_knowledge"
    ]


def _looks_like_webank_material(*, source_text: str, user_request: str) -> bool:
    combined = f"{user_request}\n{source_text}"
    if "webank" in combined.lower() or any(marker in combined for marker in WEBANK_STRONG_MARKERS):
        return True
    return any(marker in source_text for marker in WEBANK_CONTEXT_MARKERS) and any(
        term in combined for term in WEBANK_CONTEXT_TERMS
    )


def _with_bank_reference_rules(instruction: str, *, has_references: bool) -> str:
    if not has_references:
        return instruction
    return f"{instruction.rstrip()}\n\n{WEBANK_REFERENCE_RULES}"


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
