from pathlib import Path

from app.platform.ops.config import load_config, mask_value


def test_ops_load_config_reads_env_file(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "\n".join(
            [
                "M_AGENT_OPS_BOT_ID=ops-bot-id",
                "M_AGENT_OPS_BOT_SECRET=ops-secret",
                "M_AGENT_OPS_ADMIN_USER_ID=test-user",
                "M_AGENT_OPS_EVENTS_DIR=custom-ops-events",
                "M_AGENT_CHAT_LOG_DIR=custom-chat-logs",
                "M_AGENT_OPS_STATE_PATH=custom-state.json",
                "M_AGENT_OPS_HEARTBEAT_DIR=custom-heartbeats",
                "M_AGENT_OPS_HEARTBEAT_MAX_AGE_SECONDS=180",
                "M_AGENT_OPS_MONITORED_SERVICES=writing_bot,review_bot",
                "M_AGENT_OPS_DAILY_REPORT_HOUR=9",
                "M_AGENT_OPS_DAILY_REPORT_MINUTE=5",
                "M_AGENT_OPS_POLL_SECONDS=3",
                "M_AGENT_OPS_NOTIFICATION_COOLDOWN=120",
            ]
        ),
        encoding="utf-8",
    )

    config = load_config(env_path)

    root = Path(__file__).resolve().parent.parent
    assert config.bot_id == "ops-bot-id"
    assert config.bot_secret == "ops-secret"
    assert config.admin_user_id == "test-user"
    assert config.ops_events_dir == root / "custom-ops-events"
    assert config.chat_log_dir == root / "custom-chat-logs"
    assert config.state_path == root / "custom-state.json"
    assert config.heartbeat_dir == root / "custom-heartbeats"
    assert config.heartbeat_max_age_seconds == 180
    assert config.monitored_services == ("writing_bot", "review_bot")
    assert config.daily_report_hour == 9
    assert config.daily_report_minute == 5
    assert config.poll_seconds == 3
    assert config.notification_cooldown == 120


def test_mask_value_hides_secret():
    assert mask_value("abcdef123456") == "abcd...3456"
    assert mask_value("short") == "***"


def test_ops_load_config_does_not_embed_a_real_admin_default(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text("M_AGENT_OPS_BOT_ID=ops-bot-id\n", encoding="utf-8")

    config = load_config(env_path)

    assert config.admin_user_id == ""
    assert config.monitored_services == (
        "writing_bot",
        "review_bot",
        "rewrite_bot",
    )


def test_ops_load_config_uses_single_external_data_root(tmp_path):
    data_root = tmp_path / "M-Agent-Files"
    env_path = tmp_path / ".env"
    env_path.write_text(f"M_AGENT_DATA_DIR={data_root}\n", encoding="utf-8")

    config = load_config(env_path)

    assert config.ops_events_dir == data_root / "runtime" / "ops" / "events"
    assert config.chat_log_dir == data_root / "runtime" / "chat-logs"
    assert config.state_path == data_root / "runtime" / "ops" / "state.json"
    assert config.heartbeat_dir == data_root / "runtime" / "ops" / "heartbeats"
