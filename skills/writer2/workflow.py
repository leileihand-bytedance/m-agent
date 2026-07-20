import re

from app.platform.tools import ToolGateway
from skills.brief_quality import assess_multi_source_relation, brief_critic_check, build_brief_plan, format_brief_violations, validate_brief_deterministic
from skills.material_priority import source_materials_have_quantitative_data
from skills.revision_support import build_revision_payload, previous_sources
from skills.writer1.schema import BriefResult
from skills.writer1.workflow import (
    _bank_materials,
    _has_read_errors,
    _has_usable_materials,
    _missing_material_message,
    _partial_read_error_message,
    _policy_research_materials,
    _should_continue_with_readable_materials,
    _source_materials,
)


def run(inputs: dict[str, object], tools: ToolGateway) -> BriefResult:
    if inputs.get("revision"):
        return _revise_previous_draft(inputs=inputs, tools=tools)

    materials = _multi_source_materials(inputs=inputs, tools=tools)
    if not _has_usable_materials(materials):
        return BriefResult(
            title="",
            body="",
            needs_clarification=True,
            message=_missing_material_message(materials),
        )
    if _has_read_errors(materials):
        if _should_continue_with_readable_materials(inputs):
            materials = [item for item in materials if item.get("source") != "read_error"]
        else:
            return BriefResult(
                title="",
                body="",
                sources=[str(item.get("url", "")) for item in materials if item.get("url")],
                needs_clarification=True,
                message=_partial_read_error_message(materials),
            )

    source_materials = list(materials)
    relation = assess_multi_source_relation(source_materials)
    if relation["relation"] == "weak":
        return BriefResult(
            title="",
            body="",
            needs_clarification=True,
            message=relation["message"],
        )
    if not source_materials_have_quantitative_data(source_materials):
        materials.extend(_bank_materials(inputs=inputs, materials=list(source_materials), tools=tools))
    materials.extend(_policy_research_materials(inputs=inputs, materials=list(source_materials), tools=tools))
    planning_note = build_brief_plan(str(inputs.get("text", "") or ""), materials, multi_source=True)
    return _generate_and_validate(
        payload={
            "skill_id": "writer2",
            "task": "writer2",
            "instruction": inputs.get("text", ""),
            "materials": materials,
            "planning_note": planning_note,
        },
        tools=tools,
        sources=[str(item.get("url", "")) for item in materials if item.get("url")],
        default_message="已生成多素材简报初稿。",
    )


def _revise_previous_draft(inputs: dict[str, object], tools: ToolGateway) -> BriefResult:
    payload = build_revision_payload(inputs, skill_id="writer2")
    sources = previous_sources(inputs)
    if inputs.get("supplement_materials"):
        supplemental = _multi_source_materials(inputs=inputs, tools=tools)
        if not _has_usable_materials(supplemental) or _has_read_errors(supplemental):
            return BriefResult(
                title="",
                body="",
                sources=sources,
                needs_clarification=True,
                message=_missing_material_message(supplemental)
                if not _has_usable_materials(supplemental)
                else _partial_read_error_message(supplemental),
            )
        _apply_material_role(supplemental, inputs)
        payload["materials"].extend(supplemental)
        sources.extend(
            str(item.get("url", ""))
            for item in supplemental
            if str(item.get("url", "")).strip()
        )
    payload["planning_note"] = build_brief_plan(str(payload.get("instruction", "") or ""), payload["materials"], multi_source=True)
    return _generate_and_validate(
        payload=payload,
        tools=tools,
        sources=list(dict.fromkeys(sources)),
        default_message="已根据上一稿完成多素材简报修改。",
    )


def _apply_material_role(materials: list[dict[str, object]], inputs: dict[str, object]) -> None:
    role = str(inputs.get("material_role", "supplement") or "supplement")
    for item in materials:
        item["material_role"] = role


def _multi_source_materials(inputs: dict[str, object], tools: ToolGateway) -> list[dict[str, object]]:
    materials = _source_materials(inputs=inputs, tools=tools)
    if len(materials) != 1 or materials[0].get("source") != "user_text":
        return materials

    text = str(materials[0].get("text") or "")
    parts = _split_inline_materials(text)
    if len(parts) < 2:
        return materials
    return [
        {
            "title": f"用户直接提供素材{idx}",
            "text": part,
            "url": "",
            "source": "user_text",
        }
        for idx, part in enumerate(parts, 1)
    ]


def _split_inline_materials(text: str) -> list[str]:
    markers = ("素材一", "素材二", "素材三", "素材四", "材料一", "材料二", "材料三", "材料四")
    pattern = "|".join(markers)
    pieces = []
    current = ""
    for chunk in re.split(f"({pattern})[，,:：、\\s]*", text):
        chunk = chunk.strip()
        if not chunk:
            continue
        if chunk in markers:
            if current.strip():
                pieces.append(current.strip())
            current = ""
            continue
        current = f"{current}\n{chunk}".strip() if current else chunk
    if current.strip():
        pieces.append(current.strip())
    return [piece for piece in pieces if len(piece) >= 8 and not _looks_like_instruction_prefix(piece)]


def _looks_like_instruction_prefix(text: str) -> bool:
    compact = text.strip().rstrip("：:")
    return compact in {"请写多素材简报", "写多素材简报", "请写整合简报", "写整合简报"}


def _generate_and_validate(
    *,
    payload: dict[str, object],
    tools: ToolGateway,
    sources: list[str],
    default_message: str,
    feedback: str | None = None,
) -> BriefResult:
    draft_payload = dict(payload)
    if feedback:
        draft_payload["revision_feedback"] = feedback

    draft = tools.call("llm_writer", draft_payload)
    title = str(draft.get("title", ""))
    body = str(draft.get("body", ""))
    needs_clarification = bool(draft.get("needs_clarification", False)) and not (title.strip() or body.strip())
    if needs_clarification:
        return BriefResult(
            title=title,
            body=body,
            sources=sources,
            needs_clarification=True,
            message=str(draft.get("message", "") or ""),
        )

    deterministic_violations = validate_brief_deterministic(title, body)
    critic_violations = brief_critic_check(
        title=title,
        body=body,
        materials=list(draft_payload.get("materials") or []),
        planning_note=str(draft_payload.get("planning_note", "") or ""),
        tools=tools,
        skill_id="writer2",
    )
    blocking_violations = [violation for violation in [*deterministic_violations, *critic_violations] if violation.severity == "hard"]
    if blocking_violations and feedback is None:
        return _generate_and_validate(
            payload=payload,
            tools=tools,
            sources=sources,
            default_message=default_message,
            feedback=format_brief_violations(blocking_violations),
        )

    message = str(draft.get("message", "") or default_message)
    if blocking_violations:
        message = "已生成多素材简报初稿，但仍有部分规则未完全修正，建议人工复核。"
    return BriefResult(
        title=title,
        body=body,
        sources=sources,
        needs_clarification=False,
        message=message,
    )
