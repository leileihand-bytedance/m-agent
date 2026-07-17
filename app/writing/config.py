"""直报写作 Bot 配置"""

from dataclasses import dataclass
import ipaddress
from pathlib import Path
import re
import socket
import subprocess

from app.platform.config import normalize_direct_report_critic_mode, parse_bool
from app.platform.data_paths import DataPaths, configured_path
from app.platform.runtime_environment import bot_credentials, prepare_runtime_environment

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ENV_PATH = ROOT / ".env"


@dataclass(frozen=True)
class WritingBotConfig:
    wecom_bot_id: str
    wecom_bot_secret: str
    model_name: str
    anthropic_api_key: str
    anthropic_base_url: str
    skills_dir: Path
    jobs_dir: Path
    policy_db_path: Path
    bank_db_path: Path
    conversation_dir: Path
    intake_dir: Path | None = None
    model_max_tokens: int = 4096
    direct_report_critic_mode: str = "advisory"
    chat_log_enabled: bool = True
    chat_log_dir: Path | None = None
    ops_events_dir: Path | None = None
    ops_heartbeat_dir: Path | None = None
    access_policy_path: Path | None = None
    user_registry_path: Path | None = None
    portal_host: str = "127.0.0.1"
    portal_port: int = 8790
    portal_base_url: str = "http://127.0.0.1:8790"
    portal_token_ttl_seconds: int = 1800
    intake_ttl_seconds: int = 1800
    document_max_bytes: int = 50 * 1024 * 1024
    document_ocr_enabled: bool = True
    task_queue_db_path: Path | None = None
    task_worker_count: int = 1
    task_poll_seconds: float = 0.25
    task_recovery_seconds: float = 5.0
    task_lease_seconds: int = 120
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


def _is_usable_portal_ipv4(candidate: str) -> bool:
    try:
        address = ipaddress.ip_address(candidate)
    except ValueError:
        return False
    if address.version != 4:
        return False
    return not (
        address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_unspecified
        or address.is_reserved
    )


def _detect_portal_public_host() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            candidate = sock.getsockname()[0]
            if _is_usable_portal_ipv4(candidate):
                return candidate
    except OSError:
        pass

    try:
        infos = socket.getaddrinfo(socket.gethostname(), None, family=socket.AF_INET)
    except OSError:
        infos = []

    for info in infos:
        candidate = info[4][0]
        if _is_usable_portal_ipv4(candidate):
            return candidate

    try:
        result = subprocess.run(
            ["ifconfig"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
    except OSError:
        result = None

    if result is not None:
        candidates = re.findall(r"\binet (\d+\.\d+\.\d+\.\d+)\b", result.stdout)
        for candidate in candidates:
            if _is_usable_portal_ipv4(candidate):
                return candidate

    return "127.0.0.1"


def _default_portal_base_url(*, host: str, port: int) -> str:
    normalized_host = (host or "").strip()
    if normalized_host in {"127.0.0.1", "localhost"}:
        public_host = "127.0.0.1"
    elif normalized_host in {"", "0.0.0.0"}:
        public_host = _detect_portal_public_host()
    else:
        public_host = normalized_host
    return f"http://{public_host}:{port}"


def load_config(env_path: Path = DEFAULT_ENV_PATH) -> WritingBotConfig:
    runtime = prepare_runtime_environment(parse_env_file(env_path), project_root=ROOT)
    values = runtime.values
    bot_id, bot_secret = bot_credentials(
        runtime,
        production_keys=("WRITING_BOT_ID", "WRITING_BOT_SECRET"),
        test_keys=("M_AGENT_TEST_WRITING_BOT_ID", "M_AGENT_TEST_WRITING_BOT_SECRET"),
    )
    data_paths = DataPaths.from_values(values, project_root=ROOT)
    skills_dir = Path(values.get("M_AGENT_SKILLS_DIR", str(ROOT / "skills")) or str(ROOT / "skills"))
    if not skills_dir.is_absolute():
        skills_dir = ROOT / skills_dir
    jobs_dir = configured_path(
        values, "M_AGENT_PLATFORM_JOBS_DIR", data_paths.writing_jobs, project_root=ROOT
    )
    policy_db_path = configured_path(
        values, "M_AGENT_POLICY_DB_PATH", data_paths.policy_db, project_root=ROOT
    )
    bank_db_path = configured_path(
        values, "M_AGENT_BANK_DB_PATH", data_paths.bank_db, project_root=ROOT
    )
    conversation_dir = configured_path(
        values, "M_AGENT_CONVERSATION_DIR", data_paths.conversations, project_root=ROOT
    )
    policy_path_raw = values.get("M_AGENT_PLATFORM_POLICY", "").strip()
    access_policy_path = Path(policy_path_raw) if policy_path_raw else None
    if access_policy_path and not access_policy_path.is_absolute():
        access_policy_path = ROOT / access_policy_path
    chat_log_dir = configured_path(
        values, "M_AGENT_CHAT_LOG_DIR", data_paths.chat_logs, project_root=ROOT
    )
    ops_events_dir = configured_path(
        values, "M_AGENT_OPS_EVENTS_DIR", data_paths.ops_events, project_root=ROOT
    )
    ops_heartbeat_dir = configured_path(
        values, "M_AGENT_OPS_HEARTBEAT_DIR", data_paths.heartbeats, project_root=ROOT
    )
    user_registry_path = configured_path(
        values, "M_AGENT_USER_REGISTRY_PATH", data_paths.user_registry, project_root=ROOT
    )
    intake_dir = configured_path(
        values, "M_AGENT_INTAKE_DIR", data_paths.intake, project_root=ROOT
    )
    task_queue_db_path = configured_path(
        values,
        "M_AGENT_WRITING_TASK_QUEUE_DB",
        data_paths.task_queue_db.with_name("writing.sqlite3"),
        project_root=ROOT,
    )
    model_max_tokens = int(values.get("M_AGENT_MODEL_MAX_TOKENS", "4096") or "4096")

    portal_host = values.get("M_AGENT_PORTAL_HOST", "127.0.0.1") or "127.0.0.1"
    portal_port = int(values.get("M_AGENT_PORTAL_PORT", "8790") or "8790")
    portal_base_url = values.get("M_AGENT_PORTAL_BASE_URL", "").strip()
    if not portal_base_url:
        portal_base_url = _default_portal_base_url(host=portal_host, port=portal_port)

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

    return WritingBotConfig(
        wecom_bot_id=bot_id,
        wecom_bot_secret=bot_secret,
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
        intake_dir=intake_dir,
        model_max_tokens=model_max_tokens,
        direct_report_critic_mode=normalize_direct_report_critic_mode(
            values.get("M_AGENT_DIRECT_REPORT_CRITIC_MODE")
        ),
        chat_log_enabled=parse_bool(values.get("M_AGENT_CHAT_LOG_ENABLED"), default=True),
        chat_log_dir=chat_log_dir,
        ops_events_dir=ops_events_dir,
        ops_heartbeat_dir=ops_heartbeat_dir,
        access_policy_path=access_policy_path,
        user_registry_path=user_registry_path,
        portal_host=portal_host,
        portal_port=portal_port,
        portal_base_url=portal_base_url,
        portal_token_ttl_seconds=int(values.get("M_AGENT_PORTAL_TOKEN_TTL", "1800") or "1800"),
        intake_ttl_seconds=int(values.get("M_AGENT_WRITING_INTAKE_TTL", "1800") or "1800"),
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
        task_worker_count=max(
            1,
            int(values.get("M_AGENT_WRITING_TASK_WORKERS", "1") or "1"),
        ),
        task_poll_seconds=max(
            0.01,
            float(values.get("M_AGENT_WRITING_TASK_POLL_SECONDS", "0.25") or "0.25"),
        ),
        task_recovery_seconds=max(
            0.1,
            float(values.get("M_AGENT_WRITING_TASK_RECOVERY_SECONDS", "5") or "5"),
        ),
        task_lease_seconds=max(
            30,
            int(values.get("M_AGENT_WRITING_TASK_LEASE_SECONDS", "120") or "120"),
        ),
        search_api_key=search_api_key,
        search_api_base_url=search_api_base_url,
        runtime_mode=runtime.mode,
        data_root=runtime.data_root,
    )
