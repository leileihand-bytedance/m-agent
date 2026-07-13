"""管理员异常通知.

通过企业微信主动发消息给管理员,报告 Bot 运行中的异常.
支持按异常类型冷却,避免刷屏.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from app.platform.ops.events import OpsEventLogger


@dataclass(frozen=True)
class NotificationConfig:
    """通知配置."""

    admin_user_id: str
    admin_name: str
    # 同一种异常通知的最小间隔(秒),默认 5 分钟
    cooldown_seconds: int = 300
    # 默认由独立运维 Bot 发送详细告警；仅在兼容旧部署时打开审核 Bot 直发。
    direct_message_enabled: bool = False


class AdminNotifier:
    """管理员通知器.

    使用 ws_client.send_message 主动发消息.
    对同一类异常做冷却,防止重复通知.
    """

    def __init__(
        self,
        ws_client: Any,
        config: NotificationConfig,
        *,
        event_logger: OpsEventLogger | None = None,
        source: str = "review_bot",
    ) -> None:
        self.ws_client = ws_client
        self.config = config
        self.event_logger = event_logger
        self.source = source
        # error_key -> last_notify_timestamp
        self._last_notified: dict[str, float] = {}

    def _make_error_key(self, subject: str, exc: BaseException | None = None) -> str:
        """生成异常冷却 key."""
        exc_type = type(exc).__name__ if exc is not None else "None"
        return f"{subject}:{exc_type}"

    def should_notify(self, error_key: str) -> bool:
        """检查是否应该发送通知(是否过了冷却期)."""
        now = time.time()
        last = self._last_notified.get(error_key, 0)
        if now - last < self.config.cooldown_seconds:
            return False
        self._last_notified[error_key] = now
        return True

    async def notify(
        self,
        subject: str,
        detail: str,
        exc: BaseException | None = None,
        *,
        force: bool = False,
    ) -> None:
        """发送异常通知给管理员.

        Args:
            subject: 异常主题,如"文件解析失败"
            detail: 详细信息
            exc: 异常对象,用于生成冷却 key
            force: 是否跳过冷却检查
        """
        self._record_ops_event(subject=subject, detail=detail, exc=exc)

        if not self.config.direct_message_enabled or not self.config.admin_user_id:
            return

        error_key = self._make_error_key(subject, exc)
        if not force and not self.should_notify(error_key):
            return

        content = (
            f"【M-Agent 审核 Bot 异常通知】\n\n"
            f"管理员 {self.config.admin_name}:\n\n"
            f"{subject}\n\n"
            f"{detail}"
        )

        body = {
            "msgtype": "markdown",
            "markdown": {"content": content},
        }

        try:
            await self.ws_client.send_message(self.config.admin_user_id, body)
        except Exception as notify_exc:
            # 通知失败不能影响主流程,只能打印
            print(
                f"通知管理员失败: admin={self.config.admin_user_id}, "
                f"error={notify_exc}",
                flush=True,
            )

    def _record_ops_event(self, *, subject: str, detail: str, exc: BaseException | None) -> None:
        if not self.event_logger:
            return
        try:
            error_detail = detail
            if exc is not None and str(exc) not in error_detail:
                error_detail = f"{detail}\n错误类型: {type(exc).__name__}\n错误: {exc}"
            self.event_logger.record(
                source=self.source,
                severity="error",
                subject=subject,
                detail=error_detail,
            )
        except Exception as event_exc:
            print(f"审核运维事件记录失败:{type(event_exc).__name__}: {event_exc}", flush=True)

    async def notify_text_review_error(
        self,
        sender: str,
        exc: BaseException,
    ) -> None:
        """文字审核异常通知."""
        await self.notify(
            subject="文字审核异常",
            detail=f"发送人: {sender}\n错误: {exc}",
            exc=exc,
        )

    async def notify_file_review_error(
        self,
        sender: str,
        filename: str,
        stage: str,
        exc: BaseException,
    ) -> None:
        """文件审核异常通知."""
        await self.notify(
            subject=f"文件审核异常 - {stage}",
            detail=f"发送人: {sender}\n文件名: {filename}\n阶段: {stage}\n错误: {exc}",
            exc=exc,
        )

    async def notify_send_failure(
        self,
        sender: str,
        msg_type: str,
        exc: BaseException,
    ) -> None:
        """发送结果给用户失败通知."""
        await self.notify(
            subject=f"审核结果发送失败 - {msg_type}",
            detail=f"发送人: {sender}\n消息类型: {msg_type}\n错误: {exc}",
            exc=exc,
        )
