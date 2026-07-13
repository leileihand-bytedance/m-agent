from dataclasses import dataclass
from pathlib import Path


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
    model_max_tokens: int = 4096
    direct_report_critic_mode: str = "advisory"
    chat_log_enabled: bool = True
    chat_log_dir: Path | None = None
    access_policy_path: Path | None = None
    user_registry_path: Path | None = None


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
    values = parse_env_file(env_path)
    skills_dir = Path(values.get("M_AGENT_SKILLS_DIR", str(ROOT / "skills")) or str(ROOT / "skills"))
    if not skills_dir.is_absolute():
        skills_dir = ROOT / skills_dir
    jobs_dir = Path(values.get("M_AGENT_PLATFORM_JOBS_DIR", str(ROOT / "data/platform/jobs")) or str(ROOT / "data/platform/jobs"))
    if not jobs_dir.is_absolute():
        jobs_dir = ROOT / jobs_dir
    policy_db_path = Path(
        values.get("M_AGENT_POLICY_DB_PATH", str(ROOT / "data/policy_knowledge/policies.sqlite3"))
        or str(ROOT / "data/policy_knowledge/policies.sqlite3")
    )
    if not policy_db_path.is_absolute():
        policy_db_path = ROOT / policy_db_path
    bank_db_path = Path(
        values.get("M_AGENT_BANK_DB_PATH", str(ROOT / "data/bank_knowledge/bank.sqlite3"))
        or str(ROOT / "data/bank_knowledge/bank.sqlite3")
    )
    if not bank_db_path.is_absolute():
        bank_db_path = ROOT / bank_db_path
    policy_path_raw = values.get("M_AGENT_PLATFORM_POLICY", "").strip()
    access_policy_path = Path(policy_path_raw) if policy_path_raw else None
    if access_policy_path and not access_policy_path.is_absolute():
        access_policy_path = ROOT / access_policy_path
    chat_log_dir = Path(
        values.get("M_AGENT_CHAT_LOG_DIR", str(ROOT / "data/platform/chat_logs"))
        or str(ROOT / "data/platform/chat_logs")
    )
    if not chat_log_dir.is_absolute():
        chat_log_dir = ROOT / chat_log_dir
    user_registry_path = Path(
        values.get("M_AGENT_USER_REGISTRY_PATH", str(ROOT / "data/review_users.yaml"))
        or str(ROOT / "data/review_users.yaml")
    )
    if not user_registry_path.is_absolute():
        user_registry_path = ROOT / user_registry_path

    model_max_tokens = int(values.get("M_AGENT_MODEL_MAX_TOKENS", "4096") or "4096")

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
        model_max_tokens=model_max_tokens,
        direct_report_critic_mode=normalize_direct_report_critic_mode(
            values.get("M_AGENT_DIRECT_REPORT_CRITIC_MODE")
        ),
        chat_log_enabled=parse_bool(values.get("M_AGENT_CHAT_LOG_ENABLED"), default=True),
        chat_log_dir=chat_log_dir,
        access_policy_path=access_policy_path,
        user_registry_path=user_registry_path,
    )
