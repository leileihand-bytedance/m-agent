from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.platform.tools import ToolGateway, ToolNotAllowedError


def test_tool_gateway_allows_declared_tool():
    gateway = ToolGateway(
        allowed_tools=("web_reader",),
        tools={"web_reader": lambda url: {"title": "标题", "text": "正文", "url": url}},
    )

    result = gateway.call("web_reader", "https://example.com/a")

    assert result["title"] == "标题"
    assert result["url"] == "https://example.com/a"


def test_tool_gateway_blocks_undeclared_tool():
    gateway = ToolGateway(
        allowed_tools=("web_reader",),
        tools={"shell": lambda command: command},
    )

    try:
        gateway.call("shell", "ls")
    except ToolNotAllowedError as exc:
        assert "shell" in str(exc)
    else:
        raise AssertionError("ToolNotAllowedError was not raised")
