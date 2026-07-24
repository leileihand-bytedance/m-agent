import re
from pathlib import Path

from app.platform.revision import (
    PreparedRevision,
    RevisionEngine,
    RevisionPolicy,
    format_revision_violations,
)
from app.platform.tools import ToolGateway, ToolNotAllowedError
from skills.direct_report.critic import critic_check
from skills.direct_report.docx_output import (
    generate_direct_report_docx,
    is_direct_report_export_only_request,
    should_generate_direct_report_docx,
)
from skills.direct_report.guardrails import validate_deterministic
from skills.direct_report.policy_research import research_direct_report_policy
from skills.direct_report.schema import DirectReportResult
from skills.writing_planner import build_direct_report_plan


def run(inputs: dict[str, object], tools: ToolGateway) -> DirectReportResult:
    if inputs.get("revision"):
        return _revise_previous_draft(inputs=inputs, tools=tools)

    materials, read_errors = _source_materials(inputs=inputs, tools=tools)
    if read_errors and not (materials and _should_continue_with_readable_materials(inputs)):
        return DirectReportResult(
            title="",
            body="",
            needs_clarification=True,
            message="；".join(read_errors),
        )

    source_materials = list(materials)
    if _is_only_single_enterprise_case(source_materials):
        return DirectReportResult(
            title="",
            body="",
            needs_clarification=True,
            message="当前素材主要是单个企业个案，不符合直报件定位。请补充能体现业务机制、批量成效、政策落地或更高层级进展的材料，我再继续起草。",
        )
    policy_research = None
    if source_materials:
        policy_research = research_direct_report_policy(
            instruction=str(inputs.get("text", "") or ""),
            materials=list(source_materials),
            tools=tools,
        )
        if policy_research.use_policy and policy_research.selected_policy:
            materials.append(policy_research.selected_policy)
    if not materials:
        materials = _search_materials(inputs=inputs, tools=tools)

    if not materials:
        return DirectReportResult(
            title="",
            body="",
            needs_clarification=True,
            message="请提供网页链接、Word 文件、PDF 文件，或更明确的搜索主题，我再为你写直报。",
        )

    materials = _truncate_material_texts(materials, max_length=2000)
    planning_note = build_direct_report_plan(
        str(inputs.get("text", "") or ""),
        materials,
        policy_research=policy_research,
    )
    draft = _generate_and_validate(
        inputs=inputs,
        tools=tools,
        materials=materials,
        planning_note=planning_note,
    )

    return _add_word_output_if_requested(draft, inputs)


def _generate_and_validate(
    *,
    inputs: dict[str, object],
    tools: ToolGateway,
    materials: list[dict[str, object]],
    planning_note: str,
    feedback: str | None = None,
) -> DirectReportResult:
    payload: dict[str, object] = {
        "task": "direct_report",
        "instruction": inputs.get("text", ""),
        "planning_note": planning_note,
        "materials": materials,
    }
    if feedback:
        payload["revision_feedback"] = feedback

    draft = tools.call("llm_writer", payload)
    title = str(draft.get("title", ""))
    body = str(draft.get("body", ""))

    mode = _critic_mode(inputs)
    deterministic_violations = validate_deterministic(title, body)
    critic_violations = []
    if mode != "off":
        critic_violations = critic_check(
            title=title,
            body=body,
            materials=materials,
            planning_note=planning_note,
            tools=tools,
        )
    violations = [*deterministic_violations, *critic_violations]

    blocking_violations = [v for v in deterministic_violations if v.severity == "hard"]
    if mode == "rewrite":
        blocking_violations.extend(v for v in critic_violations if v.severity == "hard")

    if blocking_violations and feedback is None:
        rewrite_feedback = _format_violations(blocking_violations)
        return _generate_and_validate(
            inputs=inputs,
            tools=tools,
            materials=materials,
            planning_note=planning_note,
            feedback=rewrite_feedback,
        )

    message = "已生成直报初稿。"
    if violations:
        hard_rules = {v.rule for v in violations if v.severity == "hard"}
        if hard_rules:
            message = "已生成直报初稿，但仍有部分规则未能完全修正：" + "、".join(hard_rules) + "。建议人工复核或补充素材。"
        else:
            message = "已生成直报初稿，存在轻微可优化项（如篇幅），建议人工复核。"

    return DirectReportResult(
        title=title,
        body=body,
        sources=[str(item.get("url", "")) for item in materials if item.get("url")],
        needs_clarification=False,
        message=message,
    )


def _critic_mode(inputs: dict[str, object]) -> str:
    mode = str(inputs.get("direct_report_critic_mode", "advisory") or "advisory").strip().lower()
    if mode in {"off", "advisory", "rewrite"}:
        return mode
    return "advisory"


def _should_continue_with_readable_materials(inputs: dict[str, object]) -> bool:
    instruction = str(inputs.get("text", "") or "").strip()
    compact = "".join(instruction.split())
    return compact.startswith("1") or "继续使用已读取素材" in compact


def _format_violations(violations: list[object]) -> str:
    lines = ["上一稿存在以下问题，请逐项修正后重写："]
    for idx, violation in enumerate(violations, 1):
        lines.append(
            f"{idx}. [{violation.severity}] {violation.message}（{violation.rule}）\n   修改建议：{violation.suggestion}"
        )
    return "\n".join(lines)


def _truncate_material_texts(materials: list[dict[str, object]], max_length: int) -> list[dict[str, object]]:
    truncated: list[dict[str, object]] = []
    for item in materials:
        if not isinstance(item, dict):
            continue
        copied = dict(item)
        text = str(copied.get("text", "") or "")
        if len(text) > max_length:
            if copied.get("artifact_path"):
                marker = "\n[中间内容已省略，完整解析结果保存在任务 work 目录]\n"
                available = max(2, max_length - len(marker))
                head_length = max(1, available * 3 // 5)
                tail_length = max(1, available - head_length)
                copied["text"] = text[:head_length] + marker + text[-tail_length:]
            else:
                copied["text"] = text[:max_length] + "\n[后文已截断]"
        truncated.append(copied)
    return truncated


def _revise_previous_draft(inputs: dict[str, object], tools: ToolGateway) -> DirectReportResult:
    revision_request = str(inputs.get("revision_request", "") or "").strip()
    if (
        not inputs.get("supplement_materials")
        and is_direct_report_export_only_request(revision_request)
    ):
        result = DirectReportResult(
            title=str(inputs.get("previous_title", "") or "").strip(),
            body=str(inputs.get("previous_body", "") or "").strip(),
            sources=[
                str(item).strip()
                for item in list(inputs.get("previous_sources") or [])
                if str(item).strip()
            ],
            needs_clarification=False,
            message="已按要求生成直报 Word 文档。",
        )
        return _add_word_output_if_requested(result, inputs)

    revision = RevisionEngine(
        policy=RevisionPolicy(min_target_length=100, max_target_length=5000)
    ).prepare(
        inputs,
        skill_id="direct_report",
        tools=tools,
    )
    payload = revision.payload
    sources = list(revision.sources)
    if inputs.get("supplement_materials"):
        supplemental, read_errors = _source_materials(inputs=inputs, tools=tools)
        if read_errors:
            return DirectReportResult(
                title="",
                body="",
                sources=sources,
                needs_clarification=True,
                message="；".join(read_errors),
            )
        _apply_material_role(supplemental, inputs)
        payload["materials"].extend(_truncate_material_texts(supplemental, max_length=2000))
        sources.extend(
            str(item.get("url", ""))
            for item in supplemental
            if str(item.get("url", "")).strip()
        )
    result = _generate_revised_direct_report(
        revision=revision,
        payload=payload,
        tools=tools,
        sources=list(dict.fromkeys(sources)),
    )
    return _add_word_output_if_requested(result, inputs)


def _generate_revised_direct_report(
    *,
    revision: PreparedRevision,
    payload: dict[str, object],
    tools: ToolGateway,
    sources: list[str],
    feedback: str | None = None,
) -> DirectReportResult:
    draft_payload = dict(payload)
    if feedback:
        draft_payload["revision_feedback"] = feedback
    draft = tools.call("llm_writer", draft_payload)
    title, body = revision.apply(
        generated_title=str(draft.get("title", "")),
        generated_body=str(draft.get("body", "")),
    )
    violations = revision.validate(revised_title=title, revised_body=body)
    if violations and feedback is None:
        return _generate_revised_direct_report(
            revision=revision,
            payload=payload,
            tools=tools,
            sources=sources,
            feedback=format_revision_violations(violations),
        )
    message = str(
        draft.get("message", "已根据上一稿完成修改。")
        or "已根据上一稿完成修改。"
    )
    if violations:
        message = "已完成修改，但部分改稿要求仍需人工复核。"
    return DirectReportResult(
        title=title,
        body=body,
        sources=sources,
        revision_plan=revision.plan.model_dump(mode="json"),
        needs_clarification=False,
        message=message,
    )


def _add_word_output_if_requested(
    result: DirectReportResult,
    inputs: dict[str, object],
) -> DirectReportResult:
    request_text = str(
        inputs.get("revision_request", "") if inputs.get("revision") else inputs.get("text", "")
    ).strip()
    if result.needs_clarification or not should_generate_direct_report_docx(request_text):
        return result
    output_path = generate_direct_report_docx(
        title=result.title,
        body=result.body,
        request_text=request_text,
        output_dir=str(inputs.get("output_dir", "") or ""),
    )
    return result.model_copy(update={"output_file": str(output_path)})


def _apply_material_role(materials: list[dict[str, object]], inputs: dict[str, object]) -> None:
    role = str(inputs.get("material_role", "supplement") or "supplement")
    for item in materials:
        item["material_role"] = role


def _source_materials(inputs: dict[str, object], tools: ToolGateway) -> tuple[list[dict[str, object]], list[str]]:
    urls = [str(url) for url in list(inputs.get("urls") or []) if str(url).strip()]
    materials: list[dict[str, object]] = []
    read_errors: list[str] = []
    for url in urls:
        try:
            material = tools.call("web_reader", url)
        except Exception:
            read_errors.append(f"链接暂时无法读取：{url}。请更换可直接访问的链接，或直接粘贴正文/上传 Word、PDF、PPTX 文件。")
            continue
        if not _has_meaningful_web_body(material):
            read_errors.append(f"链接未读到有效正文：{url}。请更换可直接访问的链接，或直接补充正文/上传 Word、PDF、PPTX 文件。")
            continue
        materials.append(material)

    input_dir = str(inputs.get("input_dir", "") or "").strip()
    for file_path in [str(path) for path in list(inputs.get("files") or []) if str(path).strip()]:
        try:
            material = _read_file_material(file_path=file_path, input_dir=input_dir, tools=tools)
        except Exception:
            read_errors.append(f"文件暂时无法读取：{Path(file_path).name}。请确认文件未损坏，并重新发送 Word、PDF 或 PPTX 文件。")
            continue
        if material and str(material.get("text", "") or "").strip():
            materials.append(material)
        else:
            read_errors.append(
                f"文件未读到有效正文：{Path(file_path).name}。文件可能是扫描件或内容为空，请重新发送可读取版本，或直接粘贴正文。"
            )

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

    text = str(inputs.get("text", "")).strip()
    if not materials and len(text) >= 30:
        materials.append(
            {
                "title": "用户直接提供素材",
                "text": text,
                "url": "",
                "source": "user_text",
            }
        )

    return materials, read_errors


def _search_materials(inputs: dict[str, object], tools: ToolGateway) -> list[dict[str, str]]:
    query = str(inputs.get("text", "")).strip()
    if not query:
        return []

    try:
        results = tools.call("search", query, max_results=5)
    except (ToolNotAllowedError, KeyError):
        return []

    materials: list[dict[str, str]] = []
    for item in results if isinstance(results, list) else []:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url", ""))
        title = str(item.get("title", ""))
        snippet = str(item.get("snippet", ""))
        if not (url or title or snippet):
            continue
        materials.append(
            {
                "url": url,
                "title": title,
                "text": snippet,
                "source": str(item.get("source", "")),
            }
        )
    return materials
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
def _has_meaningful_web_body(material: object) -> bool:
    if not isinstance(material, dict):
        return False

    title = str(material.get("title", "") or "").strip()
    text = str(material.get("text", "") or "").strip()
    normalized_title = "".join(title.split())
    normalized_text = "".join(text.split())
    if normalized_text == normalized_title:
        return False
    if normalized_text in {"新京报", "人民网", "新华社", "中新网", "第一财经", "证券时报"}:
        return False
    if len(normalized_text) >= 30:
        return True
    if len(normalized_text) >= 12 and any(mark in text for mark in ("。", "！", "？", "；")):
        return True
    if len(normalized_text) < 12:
        return False
    return True


def _is_only_single_enterprise_case(materials: list[dict[str, object]]) -> bool:
    texts: list[str] = []
    for item in materials:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text", "") or "").strip()
        if text:
            texts.append(text)
    if not texts:
        return False

    sentences = _split_sentences("\n".join(texts))
    if not sentences:
        return False

    case_sentences = [sentence for sentence in sentences if _is_single_enterprise_case_sentence(sentence)]
    if not case_sentences:
        return False

    if any(_has_broader_direct_report_signal(sentence) for sentence in sentences if sentence not in case_sentences):
        return False

    return True


def _split_sentences(text: str) -> list[str]:
    return [chunk.strip() for chunk in re.split(r"[。！？；\n]", text) if chunk.strip()]


def _is_single_enterprise_case_sentence(sentence: str) -> bool:
    if not re.search(r"(一家|某家|该|某)(?:[^\n，。；]{0,12})?(企业|公司|商户|工厂)", sentence):
        return False
    return bool(re.search(r"(获批|授信|贷款|放款|融资|备货|周转|资金)", sentence))


def _has_broader_direct_report_signal(sentence: str) -> bool:
    if re.search(r"(模式|机制|批量|场景|体系|平台|名单制|担保|风险分担|共担|政策|落地|上线|推广)", sentence):
        return True
    if re.search(r"(累计|已服务|覆盖|支持)\D{0,8}\d+(家|户|笔|万元|亿元|人次)", sentence):
        return True
    return False
