from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.platform.demo import run_message


def test_demo_run_message_executes_direct_report_with_injected_tools():
    result = run_message(
        "帮我根据这个链接写直报：https://example.com/news",
        tools={
            "web_reader": lambda url: {
                "title": "网页标题",
                "text": "网页正文，包含可供直报写作的核心事实。",
                "url": url,
            },
            "llm_writer": lambda payload: {
                "title": "直报标题",
                "body": "直报正文",
            },
        },
        skills_dir=Path("skills"),
    )

    assert result.skill_id == "direct_report"
    assert result.output["title"] == "直报标题"
    assert result.output["sources"] == ["https://example.com/news"]
