from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.platform.cli import check_config  # noqa: E402
from app.platform.config import PlatformConfig, load_config  # noqa: E402


def test_check_config_reports_ready_shape(tmp_path):
    config = PlatformConfig(
        model_name="MiniMax-M2.7",
        anthropic_api_key="test-key",
        anthropic_base_url="https://example.com/anthropic",
        skills_dir=Path("skills"),
        jobs_dir=tmp_path / "jobs",
        policy_db_path=tmp_path / "policies.sqlite3",
        bank_db_path=tmp_path / "bank.sqlite3",
        model_max_tokens=4096,
        direct_report_critic_mode="advisory",
        chat_log_enabled=True,
        chat_log_dir=tmp_path / "chat_logs",
        access_policy_path=None,
        user_registry_path=tmp_path / "users.yaml",
        document_ocr_enabled=True,
        task_queue_db_path=tmp_path / "runtime" / "tasks.sqlite3",
    )

    report = check_config(config)

    assert report["skills_dir_exists"] is True
    assert report["jobs_dir"] == str(tmp_path / "jobs")
    assert report["policy_db_path"] == str(tmp_path / "policies.sqlite3")
    assert report["policy_db_exists"] is False
    assert report["bank_db_path"] == str(tmp_path / "bank.sqlite3")
    assert report["bank_db_exists"] is False
    assert report["model_name"] == "MiniMax-M2.7"
    assert report["has_api_key"] is True
    assert report["model_max_tokens"] == 4096
    assert report["direct_report_critic_mode"] == "advisory"
    assert report["chat_log_enabled"] is True
    assert report["chat_log_dir"] == str(tmp_path / "chat_logs")
    assert report["user_registry_path"] == str(tmp_path / "users.yaml")
    assert report["user_registry_exists"] is False
    assert report["document_ocr_enabled"] is True
    assert report["task_queue_db_path"] == str(tmp_path / "runtime" / "tasks.sqlite3")


def test_load_config_prefers_model_api_settings_over_legacy_anthropic(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "\n".join(
            [
                "MODEL_NAME=deepseek-v4-flash",
                "MODEL_BASE_URL=https://api.deepseek.com/v1",
                "MODEL_API_KEY=model-key",
                "M_AGENT_MODEL_MAX_TOKENS=6144",
                "M_AGENT_DIRECT_REPORT_CRITIC_MODE=rewrite",
                "M_AGENT_CHAT_LOG_ENABLED=false",
                "M_AGENT_CHAT_LOG_DIR=custom-chat-logs",
                "M_AGENT_USER_REGISTRY_PATH=custom-users.yaml",
                "M_AGENT_DOCUMENT_OCR_ENABLED=false",
                "ANTHROPIC_BASE_URL=https://legacy.example.com/anthropic",
                "ANTHROPIC_API_KEY=legacy-key",
            ]
        ),
        encoding="utf-8",
    )

    config = load_config(env_path)

    assert config.model_name == "deepseek-v4-flash"
    assert config.anthropic_base_url == "https://api.deepseek.com/v1"
    assert config.anthropic_api_key == "model-key"
    assert config.model_max_tokens == 6144
    assert config.direct_report_critic_mode == "rewrite"
    assert config.chat_log_enabled is False
    assert config.chat_log_dir == Path(__file__).resolve().parent.parent / "custom-chat-logs"
    assert config.user_registry_path == Path(__file__).resolve().parent.parent / "custom-users.yaml"
    assert config.document_ocr_enabled is False


def test_load_config_uses_single_external_data_root(tmp_path):
    data_root = tmp_path / "M-Agent-Files"
    env_path = tmp_path / ".env"
    env_path.write_text(f"M_AGENT_DATA_DIR={data_root}\n", encoding="utf-8")

    config = load_config(env_path)

    assert config.jobs_dir == data_root / "tasks" / "writing"
    assert config.conversation_dir == data_root / "runtime" / "conversations"
    assert config.chat_log_dir == data_root / "runtime" / "chat-logs"
    assert config.policy_db_path == data_root / "knowledge" / "policy" / "policies.sqlite3"
    assert config.bank_db_path == data_root / "knowledge" / "bank" / "bank.sqlite3"
    assert config.user_registry_path == data_root / "runtime" / "users" / "review_users.yaml"
    assert config.task_queue_db_path == data_root / "runtime" / "task-execution" / "tasks.sqlite3"
    assert config.document_ocr_enabled is True
