"""管理员通知测试."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import pytest

from app.review.notification import AdminNotifier, NotificationConfig
from app.platform.ops.events import OpsEventLogger, read_ops_events


@dataclass
class _FakeWSClient:
    messages: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    fail_next: bool = False

    async def send_message(self, chatid: str, body: dict[str, Any]) -> None:
        if self.fail_next:
            raise RuntimeError("send_message failed")
        self.messages.append((chatid, body))


@pytest.fixture
def notifier():
    client = _FakeWSClient()
    config = NotificationConfig(
        admin_user_id="admin123",
        admin_name="test-user",
        cooldown_seconds=60,
        direct_message_enabled=True,
    )
    return AdminNotifier(client, config), client


def test_notifier_direct_message_can_be_enabled(notifier):
    n, client = notifier
    asyncio.run(n.notify("测试异常", "详情", RuntimeError("boom")))

    assert len(client.messages) == 1
    chatid, body = client.messages[0]
    assert chatid == "admin123"
    assert body["msgtype"] == "markdown"
    assert "测试异常" in body["markdown"]["content"]
    assert "test-user" in body["markdown"]["content"]


def test_notifier_defaults_to_ops_event_only():
    client = _FakeWSClient()
    n = AdminNotifier(client, NotificationConfig(admin_user_id="admin123", admin_name="test-user"))

    asyncio.run(n.notify("测试异常", "详情", RuntimeError("boom")))

    assert client.messages == []


def test_notifier_respects_cooldown(notifier):
    n, client = notifier
    exc = RuntimeError("boom")

    asyncio.run(n.notify("测试异常", "详情1", exc))
    asyncio.run(n.notify("测试异常", "详情2", exc))

    # 同一种异常在冷却期内只发一次
    assert len(client.messages) == 1


def test_notifier_different_error_types_bypass_cooldown(notifier):
    n, client = notifier

    asyncio.run(n.notify("测试异常", "详情", RuntimeError("boom")))
    asyncio.run(n.notify("测试异常", "详情", ValueError("bad")))

    assert len(client.messages) == 2


def test_notifier_force_sends_even_in_cooldown(notifier):
    n, client = notifier
    exc = RuntimeError("boom")

    asyncio.run(n.notify("测试异常", "详情1", exc))
    asyncio.run(n.notify("测试异常", "详情2", exc, force=True))

    assert len(client.messages) == 2


def test_notifier_no_admin_user_id_does_not_send():
    client = _FakeWSClient()
    n = AdminNotifier(client, NotificationConfig(admin_user_id="", admin_name=""))

    asyncio.run(n.notify("测试异常", "详情", RuntimeError("boom")))

    assert len(client.messages) == 0


def test_notifier_send_failure_does_not_raise():
    client = _FakeWSClient(fail_next=True)
    n = AdminNotifier(
        client,
        NotificationConfig(admin_user_id="admin", admin_name="", direct_message_enabled=True),
    )

    # 不应抛异常
    asyncio.run(n.notify("测试异常", "详情", RuntimeError("boom")))

    assert len(client.messages) == 0


def test_notifier_records_ops_event_even_without_admin_user_id(tmp_path):
    client = _FakeWSClient()
    ops_logger = OpsEventLogger(tmp_path / "ops_events")
    n = AdminNotifier(
        client,
        NotificationConfig(admin_user_id="", admin_name=""),
        event_logger=ops_logger,
        source="review_bot",
    )

    asyncio.run(n.notify("文件审核异常 - 解析", "发送人: user-001\n错误: parse failed", RuntimeError("parse failed")))

    events = read_ops_events(tmp_path / "ops_events", __import__("datetime").date.today())
    assert client.messages == []
    assert len(events) == 1
    assert events[0].source == "review_bot"
    assert events[0].severity == "error"
    assert events[0].subject == "文件审核异常 - 解析"
    assert "parse failed" in events[0].detail
