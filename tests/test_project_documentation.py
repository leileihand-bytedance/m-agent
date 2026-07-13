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
    record_commit_status,
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


def test_git_sync_status_reports_ahead_behind_and_synced_states():
    assert parse_sync_counts("0\t46\n") == (0, 46)
    assert build_sync_status_message(behind=0, ahead=0) == "本地分支与远端已同步。"
    assert "46 个提交尚未推送" in build_sync_status_message(behind=0, ahead=46)
    assert "远端有 2 个新提交" in build_sync_status_message(behind=2, ahead=0)


def test_git_hooks_remind_after_commit_and_validate_before_push():
    post_commit = (ROOT / ".githooks/post-commit").read_text(encoding="utf-8")
    pre_push = ROOT / ".githooks/pre-push"

    assert "check-sync --warn-only" in post_commit
    assert pre_push.exists()
    assert "project_docs.py check" in pre_push.read_text(encoding="utf-8")
    assert "M_AGENT_MANAGED_PUSH" in pre_push.read_text(encoding="utf-8")


def test_push_record_describes_changes_and_is_idempotent(tmp_path: Path):
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
            summary="统一底座、写作、审核和项目规范，并撤下远端状态报告。",
            timestamp=timestamp,
        )

    text = report_path.read_text(encoding="utf-8")
    assert "## [2026-07-13 10:20] Git 推送 origin/main" in text
    assert "统一底座、写作、审核和项目规范" in text
    assert "底座、审核、Skills、文档与规范、测试" in text
    assert "feat: synchronize M-Agent platform" in text
    assert "5 个" in text
    assert text.count("push:origin/main:ad2e6c1ececb") == 1


def test_record_commit_status_uses_timestamp_and_is_idempotent(tmp_path: Path):
    report_path = tmp_path / "STATUS-REPORT.md"
    timestamp = datetime(2026, 7, 10, 9, 30, tzinfo=timezone.utc)

    record_commit_status(
        report_path=report_path,
        commit_hash="abc123456789",
        subject="feat: add documentation gate",
        changed_file_count=4,
        core_documents=("AGENTS.md", "docs/development/testing-and-delivery.md"),
        timestamp=timestamp,
    )
    record_commit_status(
        report_path=report_path,
        commit_hash="abc123456789",
        subject="feat: add documentation gate",
        changed_file_count=4,
        core_documents=("AGENTS.md",),
        timestamp=timestamp,
    )

    text = report_path.read_text(encoding="utf-8")
    assert "## [2026-07-10 09:30] feat: add documentation gate" in text
    assert text.count("abc123456789") == 1
    assert "二零一" not in text
