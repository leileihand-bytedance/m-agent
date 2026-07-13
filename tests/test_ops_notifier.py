import asyncio
from dataclasses import dataclass, field
from typing import Any

from app.platform.ops.notifier import OpsNotifier


@dataclass
class _FakeWSClient:
    messages: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    async def send_message(self, chatid: str, body: dict[str, Any]) -> None:
        self.messages.append((chatid, body))


def test_ops_notifier_sends_text_message():
    client = _FakeWSClient()
    notifier = OpsNotifier(client, admin_user_id="test-user", cooldown_seconds=60)

    asyncio.run(notifier.notify("写作处理失败", "错误详情", cooldown_key="error-a"))

    assert client.messages == [
        (
            "test-user",
            {
                "msgtype": "markdown",
                "markdown": {"content": "【M-Agent 运维通知】\n\n写作处理失败\n\n错误详情"},
            },
        )
    ]


def test_ops_notifier_respects_cooldown():
    client = _FakeWSClient()
    notifier = OpsNotifier(client, admin_user_id="test-user", cooldown_seconds=60)

    asyncio.run(notifier.notify("写作处理失败", "第一次", cooldown_key="same-error"))
    asyncio.run(notifier.notify("写作处理失败", "第二次", cooldown_key="same-error"))

    assert len(client.messages) == 1


def test_ops_notifier_force_bypasses_cooldown():
    client = _FakeWSClient()
    notifier = OpsNotifier(client, admin_user_id="test-user", cooldown_seconds=60)

    asyncio.run(notifier.notify("日报", "第一次", cooldown_key="daily", force=True))
    asyncio.run(notifier.notify("日报", "第二次", cooldown_key="daily", force=True))

    assert len(client.messages) == 2
