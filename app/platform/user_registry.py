"""共享用户名称注册表.

当前用于把企业微信 userid 映射为便于排查问题的用户名.
默认数据文件位于 M-Agent-Files/runtime/users/, 兼容原审核 Bot 的导入接口.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from app.platform.data_paths import DataPaths


_PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REGISTRY_PATH = DataPaths.from_values({}, project_root=_PROJECT_ROOT).user_registry

# 支持中文、英文、数字、下划线、短横线，长度 2-30
_NAME_RE = re.compile(r"^[\w一-龥\-]{2,30}$")
_PURE_CJK_RE = re.compile(r"^[一-龥]{2,30}$")

_COMMON_GREETING_TEXTS = {
    "hi",
    "hello",
    "哈喽",
    "你好",
    "你好呀",
    "您好",
    "您好呀",
    "在吗",
    "在么",
    "在不在",
    "有人吗",
    "收到",
    "好的",
    "谢谢",
    "辛苦了",
}

_REVIEW_REQUEST_KEYWORDS = (
    "审核",
    "审一下",
    "审下",
    "帮我审",
    "帮我看",
    "材料",
    "文档",
    "文件",
    "报告",
    "内参",
    "半月报",
)


class UserRegistry:
    """用户名称注册表."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or DEFAULT_REGISTRY_PATH
        self._users: dict[str, str] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            self._users = {}
            return

        raw = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
        if not isinstance(raw, dict):
            self._users = {}
            return

        self._users = {
            str(userid).strip(): str(name).strip()
            for userid, name in raw.items()
            if isinstance(name, str) and name.strip()
        }

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            yaml.safe_dump(self._users, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )

    def get_name(self, userid: str) -> str | None:
        """获取用户名, 未登记返回 None."""
        return self._users.get(userid)

    def register(self, userid: str, english_name: str) -> None:
        """注册或更新用户名."""
        self._users[userid.strip()] = english_name.strip()
        self.save()

    def is_registered(self, userid: str) -> bool:
        """判断用户是否已登记."""
        return userid in self._users

    def list_users(self) -> dict[str, str]:
        """返回所有登记用户."""
        return dict(self._users)


class RegistrationFlow:
    """用户注册流程封装."""

    def __init__(self, registry: UserRegistry, *, require_registration: bool = False) -> None:
        self.registry = registry
        self.require_registration = require_registration

    def should_ask_name(self, userid: str) -> bool:
        """是否需要向用户索要用户名."""
        if not self.require_registration:
            return False
        return not self.registry.is_registered(userid)

    def _should_prompt_name_again(self, cleaned: str) -> bool:
        normalized = re.sub(r"\s+", "", cleaned).lower()
        if normalized in _COMMON_GREETING_TEXTS:
            return True
        if any(keyword in cleaned for keyword in _REVIEW_REQUEST_KEYWORDS):
            return True
        if any(mark in cleaned for mark in "，。！？；：,.!?;:"):
            return True
        if _PURE_CJK_RE.fullmatch(cleaned) and len(cleaned) > 4:
            return True
        return False

    def handle_name_message(self, userid: str, text: str) -> tuple[bool, str]:
        """处理用户发送的疑似用户名消息.

        Returns:
            (is_name_registration, reply_message)
        """
        if not self.require_registration:
            return False, ""

        if self.registry.is_registered(userid):
            return False, ""

        cleaned = text.strip()
        if self._should_prompt_name_again(cleaned):
            return True, self.ask_name_message()
        if not _NAME_RE.match(cleaned):
            return True, (
                "请发送一个有效的名字（2-30 个字符，支持中文、英文、数字、下划线或短横线）。"
            )

        self.registry.register(userid, cleaned)
        return True, (
            f"你好，{cleaned}：\n"
            "我可以帮你审内参、半月报，或者其他文字材料，"
            "直接发文字、docx或html给我就可以。"
            "另外请注意，涉及行内数据请务必脱敏哦。"
        )

    def ask_name_message(self) -> str:
        """返回索要名称的提示语."""
        return (
            "欢迎使用智能审核BOT！\n"
            "这是你第一次使用，先互相认识一下吧，请先告诉我你的英文名（例如：Jack）。"
        )
