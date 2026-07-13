"""配置管理"""

from pathlib import Path
from dataclasses import dataclass
import json

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ENV_PATH = ROOT / ".env"


@dataclass(frozen=True)
class AppConfig:
    wecom_bot_id: str
    wecom_bot_secret: str
    model_name: str
    anthropic_api_key: str
    anthropic_base_url: str
    data_dir: Path


def load_leader_mapping() -> dict[str, list[str]]:
    """加载领导映射配置"""
    mapping_path = ROOT / "data" / "leader-mapping.json"
    if mapping_path.exists():
        return json.loads(mapping_path.read_text(encoding="utf-8"))
    return {}


def resolve_leader(identifier: str, mapping: dict[str, list[str]]) -> str | None:
    """解析领导标识

    Args:
        identifier: 用户输入的标识（可能是编号如"01"，或名称如"老李"）
        mapping: 领导映射 {"01": ["老李", "NQ", ...], ...}

    Returns:
        领导编号（如"01"），或 None
    """
    # 直接用编号查找（如"01"）
    if identifier in mapping:
        return identifier

    # 反向查找：名称对应的编号
    for key, names in mapping.items():
        if identifier in names:
            return key

    return None


def get_leader_names(leader_id: str, mapping: dict[str, list[str]]) -> list[str]:
    """获取领导的所有名称"""
    return mapping.get(leader_id, [])