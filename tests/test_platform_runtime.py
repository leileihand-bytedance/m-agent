from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.platform.registry import SkillRegistry
from app.platform.models import RoutedRequest
from app.platform.router import route_message
from app.platform.runtime import PlatformRuntime
from skills.internal_weekly.schema import InternalWeeklyResult
from skills.research_synthesis.schema import ResearchSynthesisResult


def test_runtime_executes_routed_direct_report_skill():
    registry = SkillRegistry.from_directory(Path("skills"))
    route = route_message("请根据这个链接写直报：https://example.com/news", registry)
    runtime = PlatformRuntime(
        registry=registry,
        tools={
            "web_reader": lambda url: {
                "title": "微众银行服务小微企业",
                "text": "微众银行通过数字化方式提升小微企业金融服务可得性。",
                "url": url,
            },
            "llm_writer": lambda payload: {
                "title": "微众银行提升小微企业金融服务可得性",
                "body": "微众银行围绕小微企业融资需求，持续完善数字化服务能力。",
            },
        },
    )

    result = runtime.run(route)

    assert result.skill_id == "direct_report"
    assert result.output["title"] == "微众银行提升小微企业金融服务可得性"
    assert result.output["sources"] == ["https://example.com/news"]


def test_runtime_returns_clarification_for_unknown_route():
    registry = SkillRegistry.from_directory(Path("skills"))
    route = route_message("帮我处理一下", registry)
    runtime = PlatformRuntime(registry=registry, tools={})

    result = runtime.run(route)

    assert result.skill_id is None
    assert result.needs_clarification is True
    assert "写直报" in result.message


def test_runtime_preserves_generated_output_file(monkeypatch, tmp_path):
    output_path = tmp_path / "output" / "综合调研材料初稿.docx"
    output_path.parent.mkdir()
    output_path.write_bytes(b"word")
    monkeypatch.setattr(
        "skills.research_synthesis.workflow.run",
        lambda inputs, tools: ResearchSynthesisResult(
            title="综合调研材料",
            body="一、总体情况\n正文",
            sources=["调研提纲.docx", "科技部素材.docx"],
            message="已生成综合调研 Word 初稿。",
            output_file=str(output_path),
        ),
    )
    runtime = PlatformRuntime(registry=SkillRegistry.from_directory(Path("skills")), tools={})

    result = runtime.run(
        RoutedRequest(
            skill_id="research_synthesis",
            confidence=1.0,
            needs_clarification=False,
            message="",
            inputs={"output_dir": str(output_path.parent)},
        )
    )

    assert result.output["output_file"] == str(output_path)


def test_runtime_preserves_internal_weekly_review_and_manifest_files(
    monkeypatch,
    tmp_path,
):
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    review_path = output_dir / "内参周报-2026-07-20-内容核对稿.md"
    manifest_path = output_dir / "内参周报-2026-07-20-溯源清单.json"
    review_path.write_text("# 内参周报", encoding="utf-8")
    manifest_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        "skills.internal_weekly.workflow.run",
        lambda inputs, tools: InternalWeeklyResult(
            title="内参周报（2026-07-20）",
            body="# 内参周报",
            message="已生成内容核对稿和溯源清单。",
            document_metadata={
                "draft_version": "draft-001",
                "ready_for_approval": "true",
            },
            output_file=str(review_path),
            manifest_file=str(manifest_path),
        ),
    )
    runtime = PlatformRuntime(
        registry=SkillRegistry.from_directory(Path("skills")),
        tools={},
    )

    result = runtime.run(
        RoutedRequest(
            skill_id="internal_weekly",
            confidence=1.0,
            needs_clarification=False,
            message="",
            inputs={"output_dir": str(output_dir)},
        )
    )

    assert result.output["output_file"] == str(review_path)
    assert result.output["manifest_file"] == str(manifest_path)
    assert result.output["document_metadata"] == {
        "draft_version": "draft-001",
        "ready_for_approval": "true",
    }


def test_runtime_preserves_internal_weekly_approved_word(monkeypatch, tmp_path):
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    word_path = output_dir / "内参周报.docx"
    word_path.write_bytes(b"approved-word")
    monkeypatch.setattr(
        "skills.internal_weekly.workflow.run",
        lambda inputs, tools: InternalWeeklyResult(
            title="内参周报（2026-07-20）",
            body="# 内参周报",
            message="已生成洁净版 Word。",
            output_file=str(word_path),
        ),
    )
    runtime = PlatformRuntime(
        registry=SkillRegistry.from_directory(Path("skills")),
        tools={},
    )

    result = runtime.run(
        RoutedRequest(
            skill_id="internal_weekly",
            confidence=1.0,
            needs_clarification=False,
            message="",
            inputs={"output_dir": str(output_dir)},
        )
    )

    assert result.output["output_file"] == str(word_path)


def test_runtime_rejects_generated_output_file_outside_job_output(monkeypatch, tmp_path):
    unsafe_path = tmp_path / "outside" / "综合调研材料初稿.docx"
    monkeypatch.setattr(
        "skills.research_synthesis.workflow.run",
        lambda inputs, tools: ResearchSynthesisResult(
            title="综合调研材料",
            body="正文",
            output_file=str(unsafe_path),
        ),
    )
    runtime = PlatformRuntime(registry=SkillRegistry.from_directory(Path("skills")), tools={})

    with pytest.raises(ValueError, match="当前任务 output 目录"):
        runtime.run(
            RoutedRequest(
                skill_id="research_synthesis",
                confidence=1.0,
                needs_clarification=False,
                message="",
                inputs={"output_dir": str(tmp_path / "job" / "output")},
            )
        )
