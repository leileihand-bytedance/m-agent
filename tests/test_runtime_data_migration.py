from pathlib import Path

import pytest

from app.platform.data_paths import DataPaths
from scripts.migrate_runtime_data import MigrationConflictError, migrate_runtime_data


def _paths(tmp_path: Path) -> DataPaths:
    return DataPaths.from_values(
        {"M_AGENT_DATA_DIR": str(tmp_path / "M-Agent-Files")},
        project_root=tmp_path / "M-Agent",
    )


def test_migration_dry_run_does_not_write_target(tmp_path: Path):
    source = tmp_path / "M-Agent"
    source_file = source / "data" / "platform" / "jobs" / "20260710-120000-abcd1234" / "output" / "result.json"
    source_file.parent.mkdir(parents=True)
    source_file.write_text('{"ok": true}', encoding="utf-8")
    paths = _paths(tmp_path)

    report = migrate_runtime_data(source_root=source, paths=paths, apply=False)

    assert report.planned_files == 1
    assert report.copied_files == 0
    assert not paths.root.exists()


def test_migration_separates_review_input_and_output_and_partitions_tasks(tmp_path: Path):
    source = tmp_path / "M-Agent"
    review = source / "data" / "reviews" / "20260713-002"
    source_dir = review / "source"
    source_dir.mkdir(parents=True)
    (source_dir / "材料.docx").write_bytes(b"original")
    (source_dir / "marked_材料.docx").write_bytes(b"marked")
    (review / "report.md").write_text("审核报告", encoding="utf-8")
    (review / "meta.md").write_text("审核元信息", encoding="utf-8")
    paths = _paths(tmp_path)

    report = migrate_runtime_data(source_root=source, paths=paths, apply=True)

    target = paths.review_tasks / "2026" / "07" / "20260713-002"
    assert (target / "input" / "材料.docx").read_bytes() == b"original"
    assert (target / "output" / "marked_材料.docx").read_bytes() == b"marked"
    assert (target / "output" / "report.md").read_text(encoding="utf-8") == "审核报告"
    assert (target / "meta.md").read_text(encoding="utf-8") == "审核元信息"
    assert report.copied_files == 4
    assert report.verified_files == 4


def test_migration_preserves_writing_job_structure_under_year_and_month(tmp_path: Path):
    source = tmp_path / "M-Agent"
    job = source / "data" / "platform" / "jobs" / "20260710-120000-abcd1234"
    (job / "input").mkdir(parents=True)
    (job / "input" / "材料.pdf").write_bytes(b"pdf")
    (job / "meta.json").write_text('{"job_id": "x"}', encoding="utf-8")
    paths = _paths(tmp_path)

    migrate_runtime_data(source_root=source, paths=paths, apply=True)

    target = paths.writing_jobs / "2026" / "07" / job.name
    assert (target / "input" / "材料.pdf").read_bytes() == b"pdf"
    assert (target / "meta.json").exists()


def test_migration_copies_runtime_and_knowledge_data(tmp_path: Path):
    source = tmp_path / "M-Agent"
    samples = {
        source / "data/policy_knowledge/policies.sqlite3": b"policy",
        source / "data/bank_knowledge/bank.sqlite3": b"bank",
        source / "data/policy_wiki_vault/index.md": b"wiki",
        source / "data/platform/chat_logs/20260710.jsonl": b"chat",
        source / "data/platform/conversations/thread.json": b"conversation",
        source / "data/platform/ops_events/20260710.jsonl": b"event",
        source / "data/platform/heartbeats/writing_bot.json": b"heartbeat",
        source / "data/platform/ops_state.json": b"state",
        source / "data/logs/review.log": b"log",
        source / "data/review_users.yaml": b"user: name",
    }
    for path, content in samples.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    paths = _paths(tmp_path)

    report = migrate_runtime_data(source_root=source, paths=paths, apply=True)

    assert paths.policy_db.read_bytes() == b"policy"
    assert paths.bank_db.read_bytes() == b"bank"
    assert (paths.policy_wiki / "index.md").read_bytes() == b"wiki"
    assert (paths.chat_logs / "20260710.jsonl").read_bytes() == b"chat"
    assert (paths.conversations / "thread.json").read_bytes() == b"conversation"
    assert (paths.ops_events / "20260710.jsonl").read_bytes() == b"event"
    assert (paths.heartbeats / "writing_bot.json").read_bytes() == b"heartbeat"
    assert paths.ops_state.read_bytes() == b"state"
    assert (paths.logs / "review.log").read_bytes() == b"log"
    assert paths.user_registry.read_bytes() == b"user: name"
    assert report.verified_files == len(samples)


def test_migration_stops_on_different_existing_target(tmp_path: Path):
    source = tmp_path / "M-Agent"
    source_db = source / "data/policy_knowledge/policies.sqlite3"
    source_db.parent.mkdir(parents=True)
    source_db.write_bytes(b"source")
    paths = _paths(tmp_path)
    paths.policy_db.parent.mkdir(parents=True)
    paths.policy_db.write_bytes(b"different")

    with pytest.raises(MigrationConflictError):
        migrate_runtime_data(source_root=source, paths=paths, apply=True)
