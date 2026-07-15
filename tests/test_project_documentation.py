from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from scripts.project_docs import (
    build_sync_status_message,
    classify_changed_areas,
    documentation_sync_errors,
    has_core_document_change,
    is_core_document,
    parse_sync_counts,
    record_push_status,
    requires_core_document_change,
    validate_todo_document,
)


ROOT = Path(__file__).resolve().parents[1]


def test_todo_ids_are_unique_and_statuses_are_valid():
    todo_text = (ROOT / "docs/development/TODO.md").read_text(encoding="utf-8")

    assert validate_todo_document(todo_text) == []


def test_status_report_is_declared_local_only():
    ignore_text = (ROOT / ".gitignore").read_text(encoding="utf-8")

    assert "/STATUS-REPORT.md" in ignore_text.splitlines()
    assert "/config/platform-policy.yaml" in ignore_text.splitlines()


def test_core_document_gate_recognizes_versioned_document_changes():
    assert has_core_document_change(["app/platform/runtime.py", "docs/development/architecture.md"])
    assert has_core_document_change(["skills/rewrite/workflow.py", "skills/rewrite/SKILL.md"])
    assert not has_core_document_change(["app/platform/runtime.py", "tests/test_platform_runtime.py"])
    assert not is_core_document("docs/superpowers/plans/old-plan.md")


def test_document_gate_covers_dependencies_hooks_and_relevant_module_docs():
    assert requires_core_document_change(["app/requirements.txt"])
    assert requires_core_document_change(["pyproject.toml"])
    assert requires_core_document_change(["uv.lock"])
    assert requires_core_document_change([".python-version"])
    assert requires_core_document_change([".githooks/pre-commit"])

    assert documentation_sync_errors(
        ["app/writing/bot.py", "docs/superpowers/plans/unrelated.md"]
    )
    assert documentation_sync_errors(
        ["app/platform/runtime.py", "app/review/README.md"]
    )
    assert documentation_sync_errors(
        ["app/requirements.txt", "docs/development/architecture.md"]
    )

    assert documentation_sync_errors(
        ["app/writing/bot.py", "app/writing/README.md"]
    ) == []
    assert documentation_sync_errors(
        ["app/platform/runtime.py", "docs/agent-platform/README.md"]
    ) == []
    assert documentation_sync_errors(
        ["app/requirements.txt", "docs/development/testing-and-delivery.md"]
    ) == []
    assert documentation_sync_errors(
        ["pyproject.toml", "docs/development/testing-and-delivery.md"]
    ) == []


def test_git_sync_status_reports_ahead_behind_and_synced_states():
    assert parse_sync_counts("0\t46\n") == (0, 46)
    assert build_sync_status_message(behind=0, ahead=0) == "本地分支与远端已同步。"
    assert "46 个提交尚未推送" in build_sync_status_message(behind=0, ahead=46)
    assert "远端有 2 个新提交" in build_sync_status_message(behind=2, ahead=0)


def test_change_area_classifies_uv_environment_files():
    assert classify_changed_areas(
        (".python-version", "pyproject.toml", "uv.lock")
    ) == ("依赖与交付",)


def test_git_hooks_remind_after_commit_and_validate_before_push():
    pre_commit = (ROOT / ".githooks/pre-commit").read_text(encoding="utf-8")
    post_commit = (ROOT / ".githooks/post-commit").read_text(encoding="utf-8")
    pre_push = ROOT / ".githooks/pre-push"

    assert "uv run --locked python" in pre_commit
    assert "uv run --locked python" in post_commit
    assert "check-sync --warn-only" in post_commit
    assert "record-commit" not in post_commit
    assert pre_push.exists()
    pre_push_text = pre_push.read_text(encoding="utf-8")
    assert "uv run --locked python" in pre_push_text
    assert "project_docs.py check" in pre_push_text
    assert "M_AGENT_MANAGED_PUSH" in pre_push_text


def test_push_record_is_a_capability_focused_development_log(tmp_path: Path):
    report_path = tmp_path / "STATUS-REPORT.md"
    timestamp = datetime(2026, 7, 13, 10, 20, tzinfo=timezone.utc)
    changed_paths = (
        "app/platform/app.py",
        "app/review/main.py",
        "skills/rewrite/workflow.py",
        "docs/development/architecture.md",
        "tests/test_platform_app.py",
    )

    assert classify_changed_areas(changed_paths) == (
        "底座",
        "审核",
        "Skills",
        "文档与规范",
        "测试",
    )

    for _ in range(2):
        record_push_status(
            report_path=report_path,
            remote="origin",
            branch="main",
            before_hash="03533520c462",
            after_hash="ad2e6c1ececb",
            commit_subjects=("feat: synchronize M-Agent platform",),
            changed_paths=changed_paths,
            summary="完成公共任务执行器和附件交付接入。",
            impact="写作任务可以排队执行，附件失败会重试并通知运维。",
            next_step="直报先做真实重启验收，审核 Bot 暂不切流。",
            timestamp=timestamp,
        )

    text = report_path.read_text(encoding="utf-8")
    assert "## [2026-07-13 10:20] 开发进展" in text
    assert "- 完成功能：完成公共任务执行器和附件交付接入。" in text
    assert "- 能力变化：写作任务可以排队执行，附件失败会重试并通知运维。" in text
    assert "- 当前边界/下一步：直报先做真实重启验收，审核 Bot 暂不切流。" in text
    assert "- 影响模块：底座、审核、Skills、文档与规范、测试" in text
    assert "- 交付状态：已同步到远端 `origin/main`。" in text
    assert "- 技术追溯：`ad2e6c1ececb`" in text
    assert "推送范围" not in text
    assert "提交摘要" not in text
    assert "变更文件" not in text
    assert text.count("push:origin/main:ad2e6c1ececb") == 1
