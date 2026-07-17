from pathlib import Path

import pytest

from app.platform.runtime_environment import (
    RuntimeEnvironmentError,
    bot_credentials,
    prepare_runtime_environment,
    validate_bot_startup,
)
from app.platform.config import load_config as load_platform_config
from app.platform.ops.config import load_config as load_ops_config
from app.review.main import load_config as load_review_config
from app.rewrite_bot.config import load_config as load_rewrite_config
from app.writing.config import load_config as load_writing_config


def test_production_runtime_keeps_production_data_and_credentials(tmp_path: Path):
    production_root = tmp_path / "production-data"
    runtime = prepare_runtime_environment(
        {
            "M_AGENT_DATA_DIR": str(production_root),
            "WRITING_BOT_ID": "production-id",
            "WRITING_BOT_SECRET": "production-secret",
        },
        project_root=tmp_path,
    )

    assert runtime.mode == "production"
    assert runtime.data_root == production_root
    assert runtime.values["M_AGENT_DATA_DIR"] == str(production_root)
    assert bot_credentials(
        runtime,
        production_keys=("WRITING_BOT_ID", "WRITING_BOT_SECRET"),
        test_keys=("M_AGENT_TEST_WRITING_BOT_ID", "M_AGENT_TEST_WRITING_BOT_SECRET"),
    ) == ("production-id", "production-secret")


def test_test_runtime_requires_dedicated_credentials_and_data_root(tmp_path: Path):
    runtime = prepare_runtime_environment(
        {
            "M_AGENT_RUNTIME_ENV": "test",
            "M_AGENT_DATA_DIR": str(tmp_path / "production-data"),
            "M_AGENT_TEST_DATA_DIR": str(tmp_path / "test-data"),
            "WRITING_BOT_ID": "production-id",
            "WRITING_BOT_SECRET": "production-secret",
        },
        project_root=tmp_path,
    )

    assert runtime.mode == "test"
    assert runtime.data_root == tmp_path / "test-data"
    assert runtime.values["M_AGENT_DATA_DIR"] == str(tmp_path / "test-data")
    with pytest.raises(RuntimeEnvironmentError, match="专用测试 Bot"):
        bot_credentials(
            runtime,
            production_keys=("WRITING_BOT_ID", "WRITING_BOT_SECRET"),
            test_keys=("M_AGENT_TEST_WRITING_BOT_ID", "M_AGENT_TEST_WRITING_BOT_SECRET"),
        )


def test_test_runtime_rejects_missing_or_shared_data_root(tmp_path: Path):
    with pytest.raises(RuntimeEnvironmentError, match="M_AGENT_TEST_DATA_DIR"):
        prepare_runtime_environment(
            {"M_AGENT_RUNTIME_ENV": "test"},
            project_root=tmp_path,
        )

    shared_root = tmp_path / "shared"
    with pytest.raises(RuntimeEnvironmentError, match="不能与生产数据目录相同"):
        prepare_runtime_environment(
            {
                "M_AGENT_RUNTIME_ENV": "test",
                "M_AGENT_DATA_DIR": str(shared_root),
                "M_AGENT_TEST_DATA_DIR": str(shared_root),
            },
            project_root=tmp_path,
        )


def test_production_bot_cannot_start_from_task_branch(tmp_path: Path):
    runtime = prepare_runtime_environment({}, project_root=tmp_path)

    with pytest.raises(RuntimeEnvironmentError, match="生产 Bot 只能从 main"):
        validate_bot_startup(
            runtime,
            data_paths=(runtime.data_root / "runtime",),
            current_branch="codex/change-writing",
            project_root=tmp_path,
        )


def test_test_bot_rejects_any_runtime_path_outside_test_data_root(tmp_path: Path):
    runtime = prepare_runtime_environment(
        {
            "M_AGENT_RUNTIME_ENV": "test",
            "M_AGENT_DATA_DIR": str(tmp_path / "production-data"),
            "M_AGENT_TEST_DATA_DIR": str(tmp_path / "test-data"),
        },
        project_root=tmp_path,
    )

    with pytest.raises(RuntimeEnvironmentError, match="越过测试数据目录"):
        validate_bot_startup(
            runtime,
            data_paths=(tmp_path / "production-data" / "runtime",),
            current_branch="codex/change-writing",
            project_root=tmp_path,
        )


def _write_test_env(path: Path, *, production_root: Path, test_root: Path) -> None:
    path.write_text(
        "\n".join(
            [
                "M_AGENT_RUNTIME_ENV=test",
                f"M_AGENT_DATA_DIR={production_root}",
                f"M_AGENT_TEST_DATA_DIR={test_root}",
                "WRITING_BOT_ID=production-writing-id",
                "WRITING_BOT_SECRET=production-writing-secret",
                "M_AGENT_TEST_WRITING_BOT_ID=test-writing-id",
                "M_AGENT_TEST_WRITING_BOT_SECRET=test-writing-secret",
                "WECOM_REVIEW_BOT_ID=production-review-id",
                "WECOM_REVIEW_BOT_SECRET=production-review-secret",
                "M_AGENT_TEST_REVIEW_BOT_ID=test-review-id",
                "M_AGENT_TEST_REVIEW_BOT_SECRET=test-review-secret",
                "M_AGENT_REWRITE_BOT_ID=production-rewrite-id",
                "M_AGENT_REWRITE_BOT_SECRET=production-rewrite-secret",
                "M_AGENT_TEST_REWRITE_BOT_ID=test-rewrite-id",
                "M_AGENT_TEST_REWRITE_BOT_SECRET=test-rewrite-secret",
                "M_AGENT_OPS_BOT_ID=production-ops-id",
                "M_AGENT_OPS_BOT_SECRET=production-ops-secret",
                "M_AGENT_TEST_OPS_BOT_ID=test-ops-id",
                "M_AGENT_TEST_OPS_BOT_SECRET=test-ops-secret",
            ]
        ),
        encoding="utf-8",
    )


def test_all_bot_configs_use_test_credentials_and_test_data(tmp_path: Path):
    production_root = tmp_path / "production-data"
    test_root = tmp_path / "test-data"
    env_path = tmp_path / ".env.test"
    _write_test_env(env_path, production_root=production_root, test_root=test_root)

    platform = load_platform_config(env_path)
    writing = load_writing_config(env_path)
    review = load_review_config(env_path)
    rewrite = load_rewrite_config(env_path)
    ops = load_ops_config(env_path)

    assert platform.runtime_mode == "test"
    assert platform.data_root == test_root
    assert writing.wecom_bot_id == "test-writing-id"
    assert writing.wecom_bot_secret == "test-writing-secret"
    assert writing.jobs_dir.is_relative_to(test_root)
    assert review.wecom_bot_id == "test-review-id"
    assert review.wecom_bot_secret == "test-review-secret"
    assert review.reviews_dir.is_relative_to(test_root)
    assert rewrite.bot_id == "test-rewrite-id"
    assert rewrite.bot_secret == "test-rewrite-secret"
    assert rewrite.intake_dir.is_relative_to(test_root)
    assert ops.bot_id == "test-ops-id"
    assert ops.bot_secret == "test-ops-secret"
    assert ops.ops_events_dir.is_relative_to(test_root)
