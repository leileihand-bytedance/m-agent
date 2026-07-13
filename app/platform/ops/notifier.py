from __future__ import annotations

import time
from typing import Any


class OpsNotifier:
    def __init__(self, ws_client: Any, *, admin_user_id: str, cooldown_seconds: int = 300):
        self._ws_client = ws_client
        self._admin_user_id = admin_user_id
        self._cooldown_seconds = cooldown_seconds
        self._last_notified: dict[str, float] = {}

    async def notify(
        self,
        subject: str,
        detail: str,
        *,
        cooldown_key: str = "",
        force: bool = False,
    ) -> bool:
        if not self._admin_user_id:
            return False
        key = cooldown_key or subject
        if not force and not self._can_send(key):
            return False
        body = {
            "msgtype": "markdown",
            "markdown": {"content": f"【M-Agent 运维通知】\n\n{subject}\n\n{detail}"},
        }
        try:
            await self._ws_client.send_message(self._admin_user_id, body)
            return True
        except Exception as exc:
            print(f"运维通知发送失败:{type(exc).__name__}: {exc}", flush=True)
            return False

    def _can_send(self, key: str) -> bool:
        now = time.time()
        last = self._last_notified.get(key, 0)
        if now - last < self._cooldown_seconds:
            return False
        self._last_notified[key] = now
        return True
