"""Shared extraction and parsing for paragraph-based model output."""

from __future__ import annotations

import json

from .models import Finding


def collect_message_text(message: object) -> str:
    text_parts: list[str] = []
    for block in getattr(message, "content", []):
        if hasattr(block, "text") and block.text:
            text_parts.append(block.text)
    return "\n".join(text_parts)


def _json_object_text(output: str) -> str:
    text = output.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        return text[start : end + 1]
    return text


def looks_like_valid_issue_json(output: str) -> bool:
    try:
        data = json.loads(_json_object_text(output))
    except json.JSONDecodeError:
        return False
    return isinstance(data, dict) and isinstance(data.get("issues", []), list)


def parse_paragraph_findings(
    output: str,
    paragraphs: list[str],
    allowed_rules: tuple[str, ...],
) -> tuple[list[Finding], str]:
    """Parse bounded paragraph findings while preserving legacy field behavior."""
    try:
        data = json.loads(_json_object_text(output))
    except json.JSONDecodeError:
        return [], ""
    if not isinstance(data, dict):
        return [], ""

    reasoning = str(data.get("reasoning", ""))[:200]
    issues = data.get("issues", [])
    if not isinstance(issues, list):
        return [], reasoning

    findings: list[Finding] = []
    for issue in issues:
        if not isinstance(issue, dict):
            continue
        rule_id = str(issue.get("rule_id", ""))
        if rule_id not in allowed_rules:
            continue
        try:
            paragraph_index = int(issue.get("paragraph_index", -1))
        except (TypeError, ValueError):
            continue
        if paragraph_index < 0 or paragraph_index >= len(paragraphs):
            continue
        findings.append(
            Finding(
                rule_id=rule_id,
                paragraph_index=paragraph_index,
                line_number=paragraph_index + 1,
                original_text=str(
                    issue.get("original_text", paragraphs[paragraph_index])
                ),
                description=str(issue.get("description", ""))[:100],
                target_text=str(issue.get("target_text", ""))[:50],
            )
        )
    return findings, reasoning
