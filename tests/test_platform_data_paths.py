from pathlib import Path

from app.platform.data_paths import DataPaths


def test_data_paths_default_to_visible_desktop_sibling(tmp_path: Path):
    project_root = tmp_path / "Desktop" / "M-Agent"

    paths = DataPaths.from_values({}, project_root=project_root)

    assert paths.root == project_root.parent / "M-Agent-Files"
    assert paths.writing_jobs == paths.root / "tasks" / "writing"
    assert paths.review_tasks == paths.root / "tasks" / "review"
    assert paths.policy_db == paths.root / "knowledge" / "policy" / "policies.sqlite3"
    assert paths.bank_db == paths.root / "knowledge" / "bank" / "bank.sqlite3"
    assert paths.policy_wiki == paths.root / "knowledge" / "policy-wiki"
    assert paths.chat_logs == paths.root / "runtime" / "chat-logs"
    assert paths.conversations == paths.root / "runtime" / "conversations"
    assert paths.intake == paths.root / "runtime" / "intake"
    assert paths.task_queue_db == paths.root / "runtime" / "task-execution" / "tasks.sqlite3"
    assert paths.ops_events == paths.root / "runtime" / "ops" / "events"
    assert paths.ops_state == paths.root / "runtime" / "ops" / "state.json"
    assert paths.heartbeats == paths.root / "runtime" / "ops" / "heartbeats"
    assert paths.logs == paths.root / "runtime" / "logs"
    assert paths.user_registry == paths.root / "runtime" / "users" / "review_users.yaml"


def test_data_paths_expand_configured_root_and_keep_all_data_below_it(tmp_path: Path):
    configured = tmp_path / "external-data"

    paths = DataPaths.from_values(
        {"M_AGENT_DATA_DIR": str(configured)},
        project_root=tmp_path / "project",
    )

    assert paths.root == configured
    for path in paths.managed_paths():
        assert path == configured or configured in path.parents


def test_data_paths_prepare_creates_private_visible_structure(tmp_path: Path):
    paths = DataPaths.from_values(
        {"M_AGENT_DATA_DIR": str(tmp_path / "M-Agent-Files")},
        project_root=tmp_path / "project",
    )

    paths.prepare()

    assert paths.root.is_dir()
    assert paths.writing_jobs.is_dir()
    assert paths.review_tasks.is_dir()
    assert paths.logs.is_dir()
    assert paths.intake.is_dir()
    assert paths.task_queue_db.parent.is_dir()
    assert paths.root.stat().st_mode & 0o777 == 0o700
    readme = (paths.root / "README.txt").read_text(encoding="utf-8")
    assert "不纳入 Git" in readme
    assert "tasks" in readme
