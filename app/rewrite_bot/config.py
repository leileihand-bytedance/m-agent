from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

from app.platform.config import (
    DEFAULT_ENV_PATH,
    PlatformConfig,
    ROOT,
    load_config as load_platform_config,
    parse_env_file,
)
from app.platform.data_paths import DataPaths, configured_path


@dataclass(frozen=True)
class RewriteBotConfig:
    bot_id: str
    bot_secret: str
    platform_config: PlatformConfig
    ops_events_dir: Path
    heartbeat_dir: Path


def load_config(env_path: Path = DEFAULT_ENV_PATH) -> RewriteBotConfig:
    values = parse_env_file(env_path)
    data_paths = DataPaths.from_values(values, project_root=ROOT)
    platform_config = load_platform_config(env_path)
    jobs_dir = configured_path(
        values,
        "M_AGENT_REWRITE_JOBS_DIR",
        data_paths.writing_jobs / "rewrite",
        project_root=ROOT,
    )
    conversation_dir = configured_path(
        values,
        "M_AGENT_REWRITE_CONVERSATION_DIR",
        data_paths.conversations / "rewrite-bot",
        project_root=ROOT,
    )
    ops_events_dir = configured_path(
        values,
        "M_AGENT_OPS_EVENTS_DIR",
        data_paths.ops_events,
        project_root=ROOT,
    )
    heartbeat_dir = configured_path(
        values,
        "M_AGENT_OPS_HEARTBEAT_DIR",
        data_paths.heartbeats,
        project_root=ROOT,
    )
    return RewriteBotConfig(
        bot_id=values.get("M_AGENT_REWRITE_BOT_ID", ""),
        bot_secret=values.get("M_AGENT_REWRITE_BOT_SECRET", ""),
        platform_config=replace(
            platform_config,
            jobs_dir=jobs_dir,
            conversation_dir=conversation_dir,
            skill_allowlist=("rewrite",),
        ),
        ops_events_dir=ops_events_dir,
        heartbeat_dir=heartbeat_dir,
    )


def mask_value(value: str) -> str:
    if len(value) < 8:
        return "***"
    return f"{value[:4]}...{value[-4:]}"
