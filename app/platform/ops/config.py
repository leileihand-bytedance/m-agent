from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.platform.config import DEFAULT_ENV_PATH, ROOT, parse_env_file


@dataclass(frozen=True)
class OpsBotConfig:
    bot_id: str
    bot_secret: str
    admin_user_id: str
    ops_events_dir: Path
    chat_log_dir: Path
    state_path: Path
    heartbeat_dir: Path
    monitored_services: tuple[str, ...] = ("writing_bot", "review_bot")
    heartbeat_max_age_seconds: int = 180
    daily_report_hour: int = 9
    daily_report_minute: int = 0
    poll_seconds: int = 10
    notification_cooldown: int = 300


def load_config(env_path: Path = DEFAULT_ENV_PATH) -> OpsBotConfig:
    values = parse_env_file(env_path)
    ops_events_dir = _path_from_env(values.get("M_AGENT_OPS_EVENTS_DIR"), ROOT / "data/platform/ops_events")
    chat_log_dir = _path_from_env(values.get("M_AGENT_CHAT_LOG_DIR"), ROOT / "data/platform/chat_logs")
    state_path = _path_from_env(values.get("M_AGENT_OPS_STATE_PATH"), ROOT / "data/platform/ops_state.json")
    heartbeat_dir = _path_from_env(values.get("M_AGENT_OPS_HEARTBEAT_DIR"), ROOT / "data/platform/heartbeats")
    return OpsBotConfig(
        bot_id=values.get("M_AGENT_OPS_BOT_ID", ""),
        bot_secret=values.get("M_AGENT_OPS_BOT_SECRET", ""),
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
    )


def mask_value(value: str) -> str:
    if len(value) < 8:
        return "***"
    return f"{value[:4]}...{value[-4:]}"


def _path_from_env(raw: str | None, default: Path) -> Path:
    path = Path(raw or str(default))
    if not path.is_absolute():
        path = ROOT / path
    return path


def _int_from_env(raw: str | None, default: int) -> int:
    try:
        return int(str(raw or "").strip() or default)
    except ValueError:
        return default


def _services_from_env(raw: str | None) -> tuple[str, ...]:
    services = tuple(item.strip() for item in str(raw or "").split(",") if item.strip())
    return services or ("writing_bot", "review_bot")
