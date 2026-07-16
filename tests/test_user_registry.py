"""用户注册表测试."""

from __future__ import annotations

import tempfile
from pathlib import Path

import yaml

from app.platform.user_registry import UserRegistry, RegistrationFlow
from app.review.user_registry import UserRegistry as ReviewUserRegistry


def test_registry_load_and_save():
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "users.yaml"
        path.write_text(yaml.safe_dump({"u1": "Tom"}), encoding="utf-8")

        registry = UserRegistry(path)
        assert registry.get_name("u1") == "Tom"
        assert not registry.is_registered("u2")

        registry.register("u2", "Jerry")
        assert registry.is_registered("u2")

        # 验证持久化
        reloaded = UserRegistry(path)
        assert reloaded.get_name("u2") == "Jerry"


def test_review_registry_import_keeps_compatibility():
    assert ReviewUserRegistry is UserRegistry


def test_registration_flow_disabled_by_default():
    registry = _empty_registry()
    flow = RegistrationFlow(registry, require_registration=False)

    assert not flow.should_ask_name("new_user")
    is_reg, reply = flow.handle_name_message("new_user", "Tom")
    assert not is_reg


def test_registration_flow_asks_name_first_time():
    registry = _empty_registry()
    flow = RegistrationFlow(registry, require_registration=True)

    assert flow.should_ask_name("new_user")
    reply = flow.ask_name_message()
    assert reply == (
        "欢迎使用智能审核BOT！\n"
        "这是你第一次使用，先互相认识一下吧，请先告诉我你的英文名（例如：Jack）。"
    )


def test_registration_flow_registers_valid_name():
    registry = _empty_registry()
    flow = RegistrationFlow(registry, require_registration=True)

    is_reg, reply = flow.handle_name_message("new_user", "Tom")
    assert is_reg
    assert reply == (
        "你好，Tom：\n"
        "我可以帮你审内参、半月报，或者其他文字材料，"
        "直接发文字、docx或html给我就可以。"
        "另外请注意，涉及行内数据请务必脱敏哦。"
    )
    assert registry.is_registered("new_user")


def test_registration_flow_rejects_invalid_name():
    registry = _empty_registry()
    flow = RegistrationFlow(registry, require_registration=True)

    # 包含 @ 符号,不是有效名字
    is_reg, reply = flow.handle_name_message("new_user", "Tom@123")
    assert is_reg  # 仍被识别为注册尝试
    assert "2-30 个字符" in reply
    assert not registry.is_registered("new_user")


def test_registration_flow_accepts_chinese_name():
    registry = _empty_registry()
    flow = RegistrationFlow(registry, require_registration=True)

    is_reg, reply = flow.handle_name_message("new_user", "小明")
    assert is_reg
    assert reply == (
        "你好，小明：\n"
        "我可以帮你审内参、半月报，或者其他文字材料，"
        "直接发文字、docx或html给我就可以。"
        "另外请注意，涉及行内数据请务必脱敏哦。"
    )
    assert registry.is_registered("new_user")


def test_registration_flow_does_not_register_common_greeting():
    registry = _empty_registry()
    flow = RegistrationFlow(registry, require_registration=True)

    is_reg, reply = flow.handle_name_message("new_user", "你好")

    assert is_reg
    assert reply == flow.ask_name_message()
    assert not registry.is_registered("new_user")


def test_registration_flow_registered_user_normal():
    registry = _empty_registry()
    flow = RegistrationFlow(registry, require_registration=True)
    registry.register("old_user", "Alice")

    assert not flow.should_ask_name("old_user")
    is_reg, reply = flow.handle_name_message("old_user", "Some content")
    assert not is_reg


def _empty_registry() -> UserRegistry:
    with tempfile.TemporaryDirectory() as tmpdir:
        return UserRegistry(Path(tmpdir) / "users.yaml")
