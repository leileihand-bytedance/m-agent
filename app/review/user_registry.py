"""审核模块用户注册表兼容入口.

实际实现已迁移到 app.platform.user_registry, 审核 Bot 原导入路径保持不变.
"""

from app.platform.user_registry import DEFAULT_REGISTRY_PATH, RegistrationFlow, UserRegistry


__all__ = ["DEFAULT_REGISTRY_PATH", "RegistrationFlow", "UserRegistry"]
