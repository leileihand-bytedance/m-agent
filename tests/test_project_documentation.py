from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import stat

from scripts.project_docs import (
    build_sync_status_message,
    classify_changed_areas,
    documentation_sync_errors,
    document_tree_errors,
    has_core_document_change,
    is_core_document,
    migrate_status_report,
    monthly_report_path,
    parse_sync_counts,
    record_push_status,
    requires_core_document_change,
    root_layout_errors,
    status_report_directory,
    validate_todo_document,
    write_status_index,
)


ROOT = Path(__file__).resolve().parents[1]


def test_todo_ids_are_unique_and_statuses_are_valid():
    todo_text = (ROOT / "docs/development/TODO.md").read_text(encoding="utf-8")

    assert validate_todo_document(todo_text) == []


def test_current_todo_rejects_completed_and_cancelled_history():
    text = """### TODO-001：示例\n\n状态：已完成\n"""

    assert validate_todo_document(text) == ["TODO-001 状态无效：已完成"]


def test_current_todo_rejects_references_to_removed_items():
    text = (
        "### TODO-001：示例\n\n"
        "状态：未开始\n\n"
        "依赖 `TODO-999`。\n"
    )

    assert validate_todo_document(text) == ["TODO-001 引用了不存在的 TODO-999"]


def test_status_report_is_declared_local_only():
    ignore_text = (ROOT / ".gitignore").read_text(encoding="utf-8")

    assert "/STATUS-REPORT.md" in ignore_text.splitlines()
    assert "/config/platform-policy.yaml" in ignore_text.splitlines()


def test_document_tree_rejects_old_zones_and_bad_active_plan_names():
    assert document_tree_errors(
        {
            "docs/README.md",
            "docs/development/README.md",
            "docs/development/TODO.md",
            "docs/development/architecture.md",
            "docs/development/directory-standard.md",
            "docs/development/testing-and-delivery.md",
            "docs/development/status-report.md",
            "docs/agent-platform/README.md",
            "docs/capabilities/README.md",
            "docs/operations/bots.md",
            "docs/knowledge/README.md",
            "docs/history/README.md",
            "docs/plans/README.md",
            "docs/plans/unfinished.md",
            "docs/capabilities/review-2026-07-17.md",
            "docs/superpowers/old-plan.md",
        }
    ) == [
        "历史目录 docs/superpowers/ 已停用，请使用 docs/plans/ 或 docs/history/",
        "当前事实文档文件名不得带日期：docs/capabilities/review-2026-07-17.md",
        "当前计划命名不规范：docs/plans/unfinished.md",
    ]


def test_root_layout_rejects_temporary_scripts_and_os_files():
    assert root_layout_errors(
        {
            "README.md",
            "scripts/maintenance.py",
            "debug_probe.py",
            "run_audit.sh",
            ".DS_Store",
        }
    ) == [
        "根目录存在系统临时文件：.DS_Store",
        "根目录存在一次性脚本：debug_probe.py",
        "根目录存在一次性脚本：run_audit.sh",
    ]


def test_core_document_gate_recognizes_versioned_document_changes():
    assert has_core_document_change(["app/platform/runtime.py", "docs/development/architecture.md"])
    assert has_core_document_change(["skills/rewrite/workflow.py", "skills/rewrite/SKILL.md"])
    assert not has_core_document_change(["app/platform/runtime.py", "tests/test_platform_runtime.py"])
    assert not is_core_document("docs/history/designs-and-plans/plans/old-plan.md")


def test_document_gate_covers_dependencies_hooks_and_relevant_module_docs():
    assert requires_core_document_change(["app/requirements.txt"])
    assert requires_core_document_change(["pyproject.toml"])
    assert requires_core_document_change(["uv.lock"])
    assert requires_core_document_change([".python-version"])
    assert requires_core_document_change([".githooks/pre-commit"])

    assert documentation_sync_errors(
        ["app/writing/bot.py", "docs/history/designs-and-plans/plans/unrelated.md"]
    )
    assert documentation_sync_errors(
        ["app/platform/runtime.py", "app/review/README.md"]
    )
    assert documentation_sync_errors(
        ["app/platform/runtime.py", "docs/development/TODO.md"]
    )
    assert documentation_sync_errors(
        ["skills/rewrite/workflow.py", "docs/capabilities/rewrite.md"]
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
        ["app/review/main.py", "docs/capabilities/review.md"]
    ) == []
    assert documentation_sync_errors(
        ["skills/rewrite/workflow.py", "skills/rewrite/SKILL.md"]
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


def test_push_record_is_a_capability_focused_monthly_log(tmp_path: Path):
    timestamp = datetime(2026, 7, 13, 10, 20, tzinfo=timezone.utc)
    report_path = monthly_report_path(tmp_path, timestamp)
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
            verification="专项自动化测试通过，并完成一次进程重启恢复验证。",
            next_step="直报先做真实重启验收，审核 Bot 暂不切流。",
            timestamp=timestamp,
        )

    text = report_path.read_text(encoding="utf-8")
    assert "## [2026-07-13 10:20] 开发进展" in text
    assert "- 完成功能：完成公共任务执行器和附件交付接入。" in text
    assert "- 能力变化：写作任务可以排队执行，附件失败会重试并通知运维。" in text
    assert "- 关键验证：专项自动化测试通过，并完成一次进程重启恢复验证。" in text
    assert "- 当前边界/下一步：直报先做真实重启验收，审核 Bot 暂不切流。" in text
    assert "- 影响模块：底座、审核、Skills、文档与规范、测试" in text
    assert "- 交付状态：已同步到远端 `origin/main`。" in text
    assert "- 技术追溯：`ad2e6c1ececb`" in text
    assert "推送范围" not in text
    assert "提交摘要" not in text
    assert "变更文件" not in text
    assert text.count("push:origin/main:ad2e6c1ececb") == 1
    assert stat.S_IMODE(report_path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(report_path.stat().st_mode) == 0o600


def test_status_report_directory_follows_unified_data_root():
    assert status_report_directory(
        {
            "M_AGENT_DATA_DIR": "/tmp/m-agent-data",
        }
    ) == Path("/tmp/m-agent-data/runtime/development-logs").resolve(strict=False)
    assert status_report_directory(
        {
            "M_AGENT_DATA_DIR": "/tmp/m-agent-data",
            "M_AGENT_STATUS_REPORT_DIR": "/tmp/explicit-development-logs",
        }
    ) == Path("/tmp/explicit-development-logs").resolve(strict=False)


def test_status_index_lists_monthly_logs_without_copying_entries(tmp_path: Path):
    report_dir = tmp_path / "development-logs"
    report_dir.mkdir()
    (report_dir / "2026-06.md").write_text("# 2026-06\n", encoding="utf-8")
    (report_dir / "2026-07.md").write_text("# 2026-07\n", encoding="utf-8")
    index_path = tmp_path / "STATUS-REPORT.md"

    write_status_index(index_path=index_path, report_dir=report_dir)

    text = index_path.read_text(encoding="utf-8")
    assert "2026-07.md" in text
    assert "2026-06.md" in text
    assert text.index("2026-07.md") < text.index("2026-06.md")
    assert "## [" not in text


def test_legacy_status_report_is_split_without_losing_entries(tmp_path: Path):
    source = tmp_path / "STATUS-REPORT.md"
    report_dir = tmp_path / "development-logs"
    source.write_text(
        "# M-Agent 状态报告\n\n---\n\n"
        "## [2026-07-13 10:20] 开发进展\n\n七月记录\n\n---\n\n"
        "## 一、[2026-06-22] 审核能力\n\n六月记录\n\n---\n\n"
        "## 无法识别日期的历史说明\n\n遗留记录\n",
        encoding="utf-8",
    )

    result = migrate_status_report(source_path=source, report_dir=report_dir)

    assert result == {"2026-07": 1, "2026-06": 1, "legacy-undated": 1}
    assert "七月记录" in (report_dir / "2026-07.md").read_text(encoding="utf-8")
    assert "六月记录" in (report_dir / "2026-06.md").read_text(encoding="utf-8")
    assert "遗留记录" in (report_dir / "legacy-undated.md").read_text(encoding="utf-8")
    index = source.read_text(encoding="utf-8")
    assert "月度开发日志索引" in index
    assert "七月记录" not in index

    second = migrate_status_report(source_path=source, report_dir=report_dir)
    assert second == {}
    assert (report_dir / "2026-07.md").read_text(encoding="utf-8").count("七月记录") == 1
