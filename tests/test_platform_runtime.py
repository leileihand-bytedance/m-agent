from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.platform.registry import SkillRegistry
from app.platform.router import route_message
from app.platform.runtime import PlatformRuntime


def test_runtime_executes_routed_direct_report_skill():
    registry = SkillRegistry.from_directory(Path("skills"))
    route = route_message("请根据这个链接写直报：https://example.com/news", registry)
    runtime = PlatformRuntime(
        registry=registry,
        tools={
            "web_reader": lambda url: {
                "title": "微众银行服务小微企业",
                "text": "微众银行通过数字化方式提升小微企业金融服务可得性。",
                "url": url,
            },
            "llm_writer": lambda payload: {
                "title": "微众银行提升小微企业金融服务可得性",
                "body": "微众银行围绕小微企业融资需求，持续完善数字化服务能力。",
            },
        },
    )

    result = runtime.run(route)

    assert result.skill_id == "direct_report"
    assert result.output["title"] == "微众银行提升小微企业金融服务可得性"
    assert result.output["sources"] == ["https://example.com/news"]


def test_runtime_returns_clarification_for_unknown_route():
    registry = SkillRegistry.from_directory(Path("skills"))
    route = route_message("帮我处理一下", registry)
    runtime = PlatformRuntime(registry=registry, tools={})

    result = runtime.run(route)

    assert result.skill_id is None
    assert result.needs_clarification is True
    assert "写直报" in result.message
