from pathlib import Path

from skills.brief_quality import (
    brief_critic_check,
    build_brief_plan,
    format_brief_violations,
    validate_brief_deterministic,
)
from app.policy_research import candidate_to_material
from app.platform.tools import ToolGateway, ToolNotAllowedError
from skills.material_priority import source_materials_have_quantitative_data
from skills.revision_support import build_revision_payload, previous_sources
from skills.writer1.schema import BriefResult


def run(inputs: dict[str, object], tools: ToolGateway) -> BriefResult:
    if inputs.get("revision"):
        return _revise_previous_draft(inputs=inputs, tools=tools)

    materials = _source_materials(inputs=inputs, tools=tools)
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
    if not source_materials_have_quantitative_data(source_materials):
        materials.extend(_bank_materials(inputs=inputs, materials=list(source_materials), tools=tools))
    materials.extend(_policy_research_materials(inputs=inputs, materials=list(source_materials), tools=tools))
    planning_note = build_brief_plan(str(inputs.get("text", "") or ""), materials, multi_source=False)
    return _generate_and_validate(
        payload={
            "skill_id": "writer1",
            "task": "writer1",
            "instruction": inputs.get("text", ""),
            "materials": materials,
            "planning_note": planning_note,
        },
        tools=tools,
        sources=[str(item.get("url", "")) for item in materials if item.get("url")],
        default_message="已生成简报初稿。",
    )


def _revise_previous_draft(inputs: dict[str, object], tools: ToolGateway) -> BriefResult:
    payload = build_revision_payload(inputs, skill_id="writer1")
    sources = previous_sources(inputs)
    if inputs.get("supplement_materials"):
        supplemental = _source_materials(inputs=inputs, tools=tools)
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
    payload["planning_note"] = build_brief_plan(str(payload.get("instruction", "") or ""), payload["materials"], multi_source=False)
    return _generate_and_validate(
        payload=payload,
        tools=tools,
        sources=list(dict.fromkeys(sources)),
        default_message="已根据上一稿完成简报修改。",
    )


def _apply_material_role(materials: list[dict[str, object]], inputs: dict[str, object]) -> None:
    role = str(inputs.get("material_role", "supplement") or "supplement")
    for item in materials:
        item["material_role"] = role


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
        skill_id="writer1",
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
        message = "已生成简报初稿，但仍有部分规则未完全修正，建议人工复核。"
    return BriefResult(
        title=title,
        body=body,
        sources=sources,
        needs_clarification=False,
        message=message,
    )


def _source_materials(inputs: dict[str, object], tools: ToolGateway) -> list[dict[str, object]]:
    urls = [str(url) for url in list(inputs.get("urls") or []) if str(url).strip()]
    materials: list[dict[str, object]] = []
    for url in urls:
        try:
            materials.append(tools.call("web_reader", url))
        except Exception as exc:
            materials.append(_read_error_material(url=url, error=exc))

    input_dir = str(inputs.get("input_dir", "") or "").strip()
    for file_path in [str(path) for path in list(inputs.get("files") or []) if str(path).strip()]:
        try:
            material = _read_file_material(file_path=file_path, input_dir=input_dir, tools=tools)
        except Exception:
            materials.append(_file_read_error_material(file_path))
            continue
        if material and str(material.get("text", "") or "").strip():
            materials.append(material)
        else:
            materials.append(_file_read_error_material(file_path, no_text=True))

    material_text = str(inputs.get("material_text", "")).strip()
    if material_text:
        materials.append(
            {
                "title": "用户补充文字素材",
                "text": material_text,
                "url": "",
                "source": "user_text",
            }
        )

    if materials:
        return materials

    text = str(inputs.get("text", "")).strip()
    if len(text) >= 30:
        return [
            {
                "title": "用户直接提供素材",
                "text": text,
                "url": "",
                "source": "user_text",
            }
        ]
    return []


def _has_usable_materials(materials: list[dict[str, object]]) -> bool:
    return any(item.get("source") != "read_error" for item in materials)


def _missing_material_message(materials: list[dict[str, object]]) -> str:
    failed_urls = [
        str(item.get("failed_url", "") or "").strip()
        for item in materials
        if item.get("source") == "read_error" and str(item.get("failed_url", "") or "").strip()
    ]
    if failed_urls:
        return "链接读取失败，请换一个可访问链接，或直接粘贴素材正文。"
    failed_files = _failed_file_names(materials)
    if failed_files:
        return f"文件未读到有效正文：{'、'.join(failed_files)}。文件可能是扫描件、内容为空或已经损坏，请重新发送可读取版本，或直接粘贴正文。"
    return "请发送网页链接、Word/PDF/PPTX 文件，或直接粘贴需要写成简报的素材。"


def _has_read_errors(materials: list[dict[str, object]]) -> bool:
    return any(item.get("source") == "read_error" for item in materials)


def _should_continue_with_readable_materials(inputs: dict[str, object]) -> bool:
    instruction = str(inputs.get("text", "") or "").strip()
    compact = "".join(instruction.split())
    return compact.startswith("1") or "继续使用已读取素材" in compact


def _partial_read_error_message(materials: list[dict[str, object]]) -> str:
    failed_urls = [
        str(item.get("failed_url", "") or "").strip()
        for item in materials
        if item.get("source") == "read_error" and str(item.get("failed_url", "") or "").strip()
    ]
    readable_count = sum(1 for item in materials if item.get("source") != "read_error")
    failed_files = _failed_file_names(materials)
    failed_items = [*failed_urls, *failed_files]
    failed_text = "\n".join(f"- {item}" for item in failed_items)
    failure_kind = "链接" if failed_urls and not failed_files else "素材"
    followup = "粘贴读取失败链接的正文后，再一起写。" if failed_urls and not failed_files else "粘贴未读取内容的正文后，再一起写。"
    return (
        f"有{failure_kind}读取失败，当前已读取到 {readable_count} 份素材。\n\n"
        f"读取失败的内容：\n{failed_text}\n\n"
        "请回复你的选择：\n"
        "1. 继续使用已读取素材写；\n"
        f"2. {followup}"
    )


def _read_error_material(*, url: str, error: Exception) -> dict[str, object]:
    return {
        "title": "链接读取失败",
        "text": f"以下链接未能读取，写作时不能使用该链接内容：{url}\n错误摘要：{type(error).__name__}: {error}",
        "url": "",
        "failed_url": url,
        "source": "read_error",
    }


def _file_read_error_material(file_path: str, *, no_text: bool = False) -> dict[str, object]:
    filename = Path(file_path).name
    reason = "未读到有效正文，可能是扫描件或内容为空" if no_text else "文件损坏、格式不符或解析失败"
    return {
        "title": "文件读取失败",
        "text": f"文件 {filename} {reason}，写作时不能使用该文件内容。",
        "url": "",
        "failed_file": filename,
        "source": "read_error",
    }


def _failed_file_names(materials: list[dict[str, object]]) -> list[str]:
    return [
        str(item.get("failed_file", "") or "").strip()
        for item in materials
        if item.get("source") == "read_error" and str(item.get("failed_file", "") or "").strip()
    ]


def _policy_materials(
    *,
    inputs: dict[str, object],
    materials: list[object],
    tools: ToolGateway,
) -> list[dict[str, object]]:
    try:
        packaged = tools.call(
            "policy_materials",
            user_instruction=str(inputs.get("text", "")),
            materials=materials,
            limit=3,
        )
    except (ToolNotAllowedError, KeyError):
        return []

    return [item for item in packaged if isinstance(item, dict)] if isinstance(packaged, list) else []


def _policy_research_materials(
    *,
    inputs: dict[str, object],
    materials: list[object],
    tools: ToolGateway,
) -> list[dict[str, object]]:
    try:
        result = tools.call(
            "policy_research",
            user_instruction=str(inputs.get("text", "")),
            materials=materials,
            usage_profile="brief",
            limit=3,
        )
    except (ToolNotAllowedError, KeyError):
        return _policy_materials(inputs=inputs, materials=materials, tools=tools)

    if not isinstance(result, dict) or not result.get("should_attach_policy"):
        return []

    packaged: list[dict[str, object]] = []
    primary = result.get("primary_policy")
    if isinstance(primary, dict):
        packaged.append(candidate_to_material(primary))
    for item in list(result.get("alternative_policies") or []):
        if isinstance(item, dict):
            packaged.append(candidate_to_material(item))
    return packaged


def _bank_materials(
    *,
    inputs: dict[str, object],
    materials: list[object],
    tools: ToolGateway,
) -> list[dict[str, object]]:
    try:
        packaged = tools.call(
            "bank_materials",
            user_instruction=str(inputs.get("text", "")),
            materials=materials,
            limit=3,
        )
    except (ToolNotAllowedError, KeyError):
        return []

    return [item for item in packaged if isinstance(item, dict)] if isinstance(packaged, list) else []


def _read_file_material(*, file_path: str, input_dir: str, tools: ToolGateway) -> dict[str, object] | None:
    suffix = Path(file_path).suffix.lower()
    if suffix in {".docx", ".pdf", ".pptx"}:
        try:
            return tools.call(
                "document_reader",
                file_path,
                allowed_root=input_dir or str(Path(file_path).parent),
                work_dir=str(Path(input_dir).parent / "work")
                if input_dir
                else str(Path(file_path).parent.parent / "work"),
            )
        except (ToolNotAllowedError, KeyError):
            pass
    if suffix == ".docx":
        return tools.call("word_reader", file_path, allowed_root=input_dir or str(Path(file_path).parent))
    if suffix == ".pdf":
        return tools.call("pdf_reader", file_path, allowed_root=input_dir or str(Path(file_path).parent))
    return None
