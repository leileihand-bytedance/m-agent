from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.platform.registry import SkillRegistry
from app.platform.router import route_message


def test_router_matches_direct_report_from_natural_language():
    registry = SkillRegistry.from_directory(Path("skills"))

    route = route_message("帮我根据这个链接写一篇报送材料：https://example.com/a", registry)

    assert route.skill_id == "direct_report"
    assert route.needs_clarification is False
    assert route.inputs["urls"] == ["https://example.com/a"]


def test_router_asks_when_intent_is_unknown():
    registry = SkillRegistry.from_directory(Path("skills"))

    route = route_message("帮我处理一下这个东西", registry)

    assert route.skill_id is None
    assert route.needs_clarification is True
    assert "写直报" in route.message


def test_router_matches_writer1_for_normal_brief():
    registry = SkillRegistry.from_directory(Path("skills"))

    route = route_message("帮我根据这个链接写简报：https://example.com/a", registry)

    assert route.skill_id == "writer1"
    assert route.needs_clarification is False
    assert route.inputs["urls"] == ["https://example.com/a"]


def test_router_prefers_writer2_for_multi_material_brief():
    registry = SkillRegistry.from_directory(Path("skills"))

    route = route_message("帮我把这几个链接写成多素材简报：https://example.com/a https://example.com/b", registry)

    assert route.skill_id == "writer2"
    assert route.needs_clarification is False


def test_router_uses_writer2_for_brief_with_multiple_links_even_without_explicit_keyword():
    registry = SkillRegistry.from_directory(Path("skills"))

    route = route_message("请根据这两个链接写简报：https://example.com/a https://example.com/b", registry)

    assert route.skill_id == "writer2"
    assert route.needs_clarification is False


def test_router_matches_research_synthesis_before_multi_material_brief():
    registry = SkillRegistry.from_directory(Path("skills"))

    route = route_message("请把各部门材料按调研提纲整合成综合调研材料", registry)

    assert route.skill_id == "research_synthesis"
    assert route.needs_clarification is False


def test_router_matches_research_synthesis_with_explicit_research_outline_wording():
    registry = SkillRegistry.from_directory(Path("skills"))

    route = route_message("请按调研提纲整合各部门上传的素材", registry)

    assert route.skill_id == "research_synthesis"
    assert route.needs_clarification is False


def test_router_matches_research_synthesis_for_natural_research_summary_wording():
    registry = SkillRegistry.from_directory(Path("skills"))

    route = route_message("帮我把下面的调研材料做个汇总", registry)

    assert route.skill_id == "research_synthesis"
    assert route.needs_clarification is False


def test_router_matches_rewrite_for_inline_text_polish():
    registry = SkillRegistry.from_directory(Path("skills"))

    route = route_message("帮我润色这段：这段话现在有点口语化，需要更正式一些。", registry)

    assert route.skill_id == "rewrite"
    assert route.needs_clarification is False


def test_router_matches_rewrite_for_inline_text_without_word_ruse():
    registry = SkillRegistry.from_directory(Path("skills"))

    route = route_message("帮我把下面这段更正式一点：这段话现在有点口语化，需要更规范一些。", registry)

    assert route.skill_id == "rewrite"
    assert route.needs_clarification is False


def test_router_matches_rewrite_when_material_comes_before_request():
    registry = SkillRegistry.from_directory(Path("skills"))

    route = route_message(
        "这是一段新的材料文字，不要沿着上一稿继续改。\n\n帮我整体润色一下",
        registry,
    )

    assert route.skill_id == "rewrite"
    assert route.needs_clarification is False


def test_router_matches_shenyinxie_news_from_trigger_words():
    registry = SkillRegistry.from_directory(Path("skills"))

    route = route_message("生成深银协动态", registry)

    assert route.skill_id == "shenyinxie_news"
    assert route.needs_clarification is False


def test_router_matches_internal_weekly_from_trigger_words():
    registry = SkillRegistry.from_directory(Path("skills"))

    route = route_message("生成本周内参周报", registry)

    assert route.skill_id == "internal_weekly"
    assert route.needs_clarification is False


def test_router_matches_current_day_market_summary_update_to_internal_weekly():
    registry = SkillRegistry.from_directory(Path("skills"))

    route = route_message("生成一下今天的资本市场综述", registry)

    assert route.skill_id == "internal_weekly"
    assert route.needs_clarification is False
