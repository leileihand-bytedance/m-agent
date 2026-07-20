from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.platform.config import DEFAULT_ENV_PATH, ROOT, parse_env_file
from app.platform.data_paths import DataPaths, configured_path
from app.platform.runtime_environment import bot_credentials, prepare_runtime_environment


@dataclass(frozen=True)
class OpsBotConfig:
    bot_id: str
    bot_secret: str
    admin_user_id: str
    ops_events_dir: Path
    chat_log_dir: Path
    state_path: Path
    heartbeat_dir: Path
    monitored_services: tuple[str, ...] = (
        "writing_bot",
        "review_bot",
        "rewrite_bot",
    )
    heartbeat_max_age_seconds: int = 180
    daily_report_hour: int = 9
    daily_report_minute: int = 0
    poll_seconds: int = 10
    notification_cooldown: int = 300
    runtime_mode: str = "production"
    data_root: Path | None = None


def load_config(env_path: Path = DEFAULT_ENV_PATH) -> OpsBotConfig:
    runtime = prepare_runtime_environment(parse_env_file(env_path), project_root=ROOT)
    values = runtime.values
    bot_id, bot_secret = bot_credentials(
        runtime,
        production_keys=("M_AGENT_OPS_BOT_ID", "M_AGENT_OPS_BOT_SECRET"),
        test_keys=("M_AGENT_TEST_OPS_BOT_ID", "M_AGENT_TEST_OPS_BOT_SECRET"),
    )
    data_paths = DataPaths.from_values(values, project_root=ROOT)
    ops_events_dir = configured_path(
        values, "M_AGENT_OPS_EVENTS_DIR", data_paths.ops_events, project_root=ROOT
    )
    chat_log_dir = configured_path(
        values, "M_AGENT_CHAT_LOG_DIR", data_paths.chat_logs, project_root=ROOT
    )
    state_path = configured_path(
        values, "M_AGENT_OPS_STATE_PATH", data_paths.ops_state, project_root=ROOT
    )
    heartbeat_dir = configured_path(
        values, "M_AGENT_OPS_HEARTBEAT_DIR", data_paths.heartbeats, project_root=ROOT
    )
    return OpsBotConfig(
        bot_id=bot_id,
        bot_secret=bot_secret,
        admin_user_id=values.get("M_AGENT_OPS_ADMIN_USER_ID", "").strip(),
        ops_events_dir=ops_events_dir,
        chat_log_dir=chat_log_dir,
        state_path=state_path,
        heartbeat_dir=heartbeat_dir,
        monitored_services=_services_from_env(values.get("M_AGENT_OPS_MONITORED_SERVICES")),
        heartbeat_max_age_seconds=_int_from_env(values.get("M_AGENT_OPS_HEARTBEAT_MAX_AGE_SECONDS"), 180),
        daily_report_hour=_int_from_env(values.get("M_AGENT_OPS_DAILY_REPORT_HOUR"), 9),
        daily_report_minute=_int_from_env(values.get("M_AGENT_OPS_DAILY_REPORT_MINUTE"), 0),
        poll_seconds=max(1, _int_from_env(values.get("M_AGENT_OPS_POLL_SECONDS"), 10)),
        notification_cooldown=_int_from_env(values.get("M_AGENT_OPS_NOTIFICATION_COOLDOWN"), 300),
        runtime_mode=runtime.mode,
        data_root=runtime.data_root,
    )


def mask_value(value: str) -> str:
    if len(value) < 8:
        return "***"
    return f"{value[:4]}...{value[-4:]}"


def _int_from_env(raw: str | None, default: int) -> int:
    try:
        return int(str(raw or "").strip() or default)
    except ValueError:
        return default


def _services_from_env(raw: str | None) -> tuple[str, ...]:
    services = tuple(item.strip() for item in str(raw or "").split(",") if item.strip())
    return services or ("writing_bot", "review_bot", "rewrite_bot")
