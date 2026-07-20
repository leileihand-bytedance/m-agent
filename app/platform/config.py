from dataclasses import dataclass
from pathlib import Path

from app.platform.data_paths import DataPaths, configured_path
from app.platform.runtime_environment import prepare_runtime_environment


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ENV_PATH = ROOT / ".env"


@dataclass(frozen=True)
class PlatformConfig:
    model_name: str
    anthropic_api_key: str
    anthropic_base_url: str
    skills_dir: Path
    jobs_dir: Path
    policy_db_path: Path
    bank_db_path: Path
    conversation_dir: Path | None = None
    model_max_tokens: int = 4096
    model_timeout_seconds: float = 180.0
    model_max_attempts: int = 2
    model_retry_backoff_seconds: float = 1.0
    direct_report_critic_mode: str = "advisory"
    chat_log_enabled: bool = True
    chat_log_dir: Path | None = None
    access_policy_path: Path | None = None
    user_registry_path: Path | None = None
    document_max_bytes: int = 50 * 1024 * 1024
    document_ocr_enabled: bool = True
    task_queue_db_path: Path | None = None
    task_relation_db_path: Path | None = None
    skill_allowlist: tuple[str, ...] | None = None
    search_api_key: str = ""
    search_api_base_url: str = ""
    runtime_mode: str = "production"
    data_root: Path | None = None


def parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def normalize_direct_report_critic_mode(value: str | None) -> str:
    mode = str(value or "advisory").strip().lower()
    if mode in {"off", "advisory", "rewrite"}:
        return mode
    return "advisory"


def parse_bool(value: str | None, *, default: bool) -> bool:
    if value is None:
        return default
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on", "y"}:
        return True
    if normalized in {"0", "false", "no", "off", "n"}:
        return False
    return default


def load_config(env_path: Path = DEFAULT_ENV_PATH) -> PlatformConfig:
    runtime = prepare_runtime_environment(parse_env_file(env_path), project_root=ROOT)
    values = runtime.values
    data_paths = DataPaths.from_values(values, project_root=ROOT)
    skills_dir = Path(values.get("M_AGENT_SKILLS_DIR", str(ROOT / "skills")) or str(ROOT / "skills"))
    if not skills_dir.is_absolute():
        skills_dir = ROOT / skills_dir
    jobs_dir = configured_path(
        values,
        "M_AGENT_PLATFORM_JOBS_DIR",
        data_paths.writing_jobs,
        project_root=ROOT,
    )
    policy_db_path = configured_path(
        values,
        "M_AGENT_POLICY_DB_PATH",
        data_paths.policy_db,
        project_root=ROOT,
    )
    bank_db_path = configured_path(
        values,
        "M_AGENT_BANK_DB_PATH",
        data_paths.bank_db,
        project_root=ROOT,
    )
    conversation_dir = configured_path(
        values,
        "M_AGENT_CONVERSATION_DIR",
        data_paths.conversations,
        project_root=ROOT,
    )
    policy_path_raw = values.get("M_AGENT_PLATFORM_POLICY", "").strip()
    access_policy_path = Path(policy_path_raw) if policy_path_raw else None
    if access_policy_path and not access_policy_path.is_absolute():
        access_policy_path = ROOT / access_policy_path
    chat_log_dir = configured_path(
        values,
        "M_AGENT_CHAT_LOG_DIR",
        data_paths.chat_logs,
        project_root=ROOT,
    )
    user_registry_path = configured_path(
        values,
        "M_AGENT_USER_REGISTRY_PATH",
        data_paths.user_registry,
        project_root=ROOT,
    )
    task_queue_db_path = configured_path(
        values,
        "M_AGENT_TASK_QUEUE_DB",
        data_paths.task_queue_db,
        project_root=ROOT,
    )
    task_relation_db_path = configured_path(
        values,
        "M_AGENT_TASK_RELATION_DB",
        data_paths.task_relation_db,
        project_root=ROOT,
    )

    model_max_tokens = int(values.get("M_AGENT_MODEL_MAX_TOKENS", "4096") or "4096")

    search_api_key = (
        values.get("SEARCH_API_KEY")
        or values.get("MODEL_API_KEY")
        or values.get("ANTHROPIC_API_KEY", "")
    )
    search_api_base_url = (
        values.get("SEARCH_API_BASE_URL")
        or values.get("MODEL_BASE_URL")
        or values.get("ANTHROPIC_BASE_URL", "https://api.minimaxi.com/anthropic")
        or "https://api.minimaxi.com/anthropic"
    )

    return PlatformConfig(
        model_name=values.get("MODEL_NAME", "MiniMax-M2.7") or "MiniMax-M2.7",
        anthropic_api_key=values.get("MODEL_API_KEY") or values.get("ANTHROPIC_API_KEY", ""),
        anthropic_base_url=values.get("MODEL_BASE_URL")
        or values.get("ANTHROPIC_BASE_URL", "https://api.minimaxi.com/anthropic")
        or "https://api.minimaxi.com/anthropic",
        skills_dir=skills_dir,
        jobs_dir=jobs_dir,
        policy_db_path=policy_db_path,
        bank_db_path=bank_db_path,
        conversation_dir=conversation_dir,
        model_max_tokens=model_max_tokens,
        model_timeout_seconds=max(
            1.0,
            float(values.get("M_AGENT_MODEL_TIMEOUT_SECONDS", "180") or "180"),
        ),
        model_max_attempts=max(
            1,
            min(3, int(values.get("M_AGENT_MODEL_MAX_ATTEMPTS", "2") or "2")),
        ),
        model_retry_backoff_seconds=max(
            0.0,
            min(
                30.0,
                float(values.get("M_AGENT_MODEL_RETRY_BACKOFF_SECONDS", "1") or "1"),
            ),
        ),
        direct_report_critic_mode=normalize_direct_report_critic_mode(
            values.get("M_AGENT_DIRECT_REPORT_CRITIC_MODE")
        ),
        chat_log_enabled=parse_bool(values.get("M_AGENT_CHAT_LOG_ENABLED"), default=True),
        chat_log_dir=chat_log_dir,
        access_policy_path=access_policy_path,
        user_registry_path=user_registry_path,
        document_max_bytes=max(
            1,
            int(values.get("M_AGENT_DOCUMENT_MAX_MB", "50") or "50"),
        )
        * 1024
        * 1024,
        document_ocr_enabled=parse_bool(
            values.get("M_AGENT_DOCUMENT_OCR_ENABLED"),
            default=True,
        ),
        task_queue_db_path=task_queue_db_path,
        task_relation_db_path=task_relation_db_path,
        search_api_key=search_api_key,
        search_api_base_url=search_api_base_url,
        runtime_mode=runtime.mode,
        data_root=runtime.data_root,
    )
