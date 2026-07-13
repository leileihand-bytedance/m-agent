"""直报写作 Bot 配置"""

from dataclasses import dataclass
import ipaddress
from pathlib import Path
import re
import socket
import subprocess

from app.platform.config import normalize_direct_report_critic_mode, parse_bool

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
    values = parse_env_file(env_path)
    skills_dir = Path(values.get("M_AGENT_SKILLS_DIR", str(ROOT / "skills")) or str(ROOT / "skills"))
    if not skills_dir.is_absolute():
        skills_dir = ROOT / skills_dir
    jobs_dir = Path(values.get("M_AGENT_PLATFORM_JOBS_DIR", str(ROOT / "data/platform/jobs")) or str(ROOT / "data/platform/jobs"))
    if not jobs_dir.is_absolute():
        jobs_dir = ROOT / jobs_dir
    policy_path_raw = values.get("M_AGENT_PLATFORM_POLICY", "").strip()
    access_policy_path = Path(policy_path_raw) if policy_path_raw else None
    if access_policy_path and not access_policy_path.is_absolute():
        access_policy_path = ROOT / access_policy_path
    chat_log_dir = Path(values.get("M_AGENT_CHAT_LOG_DIR", str(ROOT / "data/platform/chat_logs")) or str(ROOT / "data/platform/chat_logs"))
    if not chat_log_dir.is_absolute():
        chat_log_dir = ROOT / chat_log_dir
    ops_events_dir = Path(values.get("M_AGENT_OPS_EVENTS_DIR", str(ROOT / "data/platform/ops_events")) or str(ROOT / "data/platform/ops_events"))
    if not ops_events_dir.is_absolute():
        ops_events_dir = ROOT / ops_events_dir
    ops_heartbeat_dir = Path(values.get("M_AGENT_OPS_HEARTBEAT_DIR", str(ROOT / "data/platform/heartbeats")) or str(ROOT / "data/platform/heartbeats"))
    if not ops_heartbeat_dir.is_absolute():
        ops_heartbeat_dir = ROOT / ops_heartbeat_dir
    user_registry_path = Path(
        values.get("M_AGENT_USER_REGISTRY_PATH", str(ROOT / "data/review_users.yaml"))
        or str(ROOT / "data/review_users.yaml")
    )
    if not user_registry_path.is_absolute():
        user_registry_path = ROOT / user_registry_path
    model_max_tokens = int(values.get("M_AGENT_MODEL_MAX_TOKENS", "4096") or "4096")

    portal_host = values.get("M_AGENT_PORTAL_HOST", "127.0.0.1") or "127.0.0.1"
    portal_port = int(values.get("M_AGENT_PORTAL_PORT", "8790") or "8790")
    portal_base_url = values.get("M_AGENT_PORTAL_BASE_URL", "").strip()
    if not portal_base_url:
        portal_base_url = _default_portal_base_url(host=portal_host, port=portal_port)

    return WritingBotConfig(
        wecom_bot_id=values.get("WRITING_BOT_ID", ""),
        wecom_bot_secret=values.get("WRITING_BOT_SECRET", ""),
        model_name=values.get("MODEL_NAME", "MiniMax-M2.7") or "MiniMax-M2.7",
        anthropic_api_key=values.get("MODEL_API_KEY") or values.get("ANTHROPIC_API_KEY", ""),
        anthropic_base_url=values.get("MODEL_BASE_URL")
        or values.get("ANTHROPIC_BASE_URL", "https://api.minimaxi.com/anthropic")
        or "https://api.minimaxi.com/anthropic",
        skills_dir=skills_dir,
        jobs_dir=jobs_dir,
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
    )
