from skills.direct_report.schema import DirectReportCriticResult, DirectReportViolation


def critic_check(
    *,
    title: str,
    body: str,
    materials: list[object],
    planning_note: str,
    tools: object,
) -> list[DirectReportViolation]:
    """调用模型审查直报初稿，返回语义层面的违规项。"""
    instruction = f"""请审查以下直报初稿：

标题：{title}

正文：
{body}
"""

    try:
        result = tools.call(
            "llm_writer",
            {
                "task": "direct_report_critic",
                "skill_id": "direct_report",
                "output_type": DirectReportCriticResult,
                "prompt_path": "prompts/critic.md",
                "instruction": instruction,
                "planning_note": planning_note,
                "materials": materials,
            },
        )
    except Exception:
        return []

    if not isinstance(result, dict):
        return []

    violations = result.get("violations") or []
    if isinstance(violations, list):
        return [DirectReportViolation.model_validate(item) for item in violations]
    return []
