from __future__ import annotations

import json
from pathlib import Path

from skills.internal_weekly.schema import InternalWeeklyResult


def render_review_markdown(result: InternalWeeklyResult) -> str:
    source_map = {record.source_id: record for record in result.source_records}
    review_title = (
        "今日资本市场综述更新"
        if result.generation_mode == "market_update"
        else "内参周报"
    )
    lines = [
        f"# {review_title}（内容核对稿）",
        "",
        (
            f"出版日：{result.publication_date}"
            f"｜统计期：{result.period_start} 至 {result.period_end}"
        ),
        "",
    ]
    for section in result.sections:
        lines.extend([f"## {section.name}", ""])
        if not section.items:
            lines.extend(["_本板块本期暂无入选内容。_", ""])
        for index, item in enumerate(section.items, start=1):
            links: list[str] = []
            for source_id in item.source_ids:
                source = source_map[source_id]
                links.append(f"[{source.title}]({source.url})")
            lines.extend(
                [
                    f"### {index}. {item.title}",
                    "",
                    item.body,
                    "",
                    f"原文：{'；'.join(links)}",
                    "",
                ]
            )
    if result.warnings:
        lines.extend(["## 待核事项", ""])
        lines.extend(f"- {warning}" for warning in result.warnings)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_review_bundle(result: InternalWeeklyResult, output_dir: str) -> tuple[str, str]:
    target = Path(output_dir).expanduser().resolve()
    target.mkdir(parents=True, exist_ok=True)
    stem = (
        f"内参周报-{result.publication_date}-今日资本市场更新"
        if result.generation_mode == "market_update"
        else f"内参周报-{result.publication_date}"
    )
    review_path = target / f"{stem}-内容核对稿.md"
    manifest_path = target / f"{stem}-溯源清单.json"
    review_path.write_text(render_review_markdown(result), encoding="utf-8")
    manifest = {
        "generation_mode": result.generation_mode,
        "title": result.title,
        "publication_date": result.publication_date,
        "period_start": result.period_start,
        "period_end": result.period_end,
        "draft_version": result.draft_version,
        "ready_for_approval": result.ready_for_approval,
        "warnings": result.warnings,
        "sections": [section.model_dump(mode="json") for section in result.sections],
        "source_records": [record.model_dump(mode="json") for record in result.source_records],
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return str(review_path), str(manifest_path)
