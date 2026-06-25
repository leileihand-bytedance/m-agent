"""板块归位检测器测试."""
from app.review.section_entities import (
    REGULATORY_ENTITIES, PARTY_GOV_ENTITIES, BANKING_ENTITIES
)
from app.review.reviewer import check_section_mismatch


def test_regulatory_in_wrong_section():
    """金融监管总局会议放在党政要闻 → 应报错."""
    paragraphs = [
        "党政要闻",
        "金融监管总局部署2026年从严治党工作",  # 标题
        "1月15日，国家金融监管总局召开2026年监管工作会议...",  # 正文
    ]
    findings = check_section_mismatch(paragraphs)
    rule_ids = [f.rule_id for f in findings]
    assert "content-wrong-section" in rule_ids, f"应为content-wrong-section，实际{rule_ids}"


def test_state_council_in_regulatory():
    """国务院会议放在监管动态 → 应报错."""
    paragraphs = [
        "监管动态",
        "国务院总理李强主持召开国务院常务会议",  # 标题
        "1月14日，国务院总理李强主持召开国务院党组会议...",  # 正文
    ]
    findings = check_section_mismatch(paragraphs)
    rule_ids = [f.rule_id for f in findings]
    assert "content-wrong-section" in rule_ids, f"应为content-wrong-section，实际{rule_ids}"


def test_regulatory_in_correct_section():
    """金融监管总局会议放在监管动态 → 不报错."""
    paragraphs = [
        "监管动态",
        "金融监管总局部署2026年从严治党工作",
        "1月15日，国家金融监管总局召开2026年监管工作会议...",
    ]
    findings = check_section_mismatch(paragraphs)
    wrong_section = [f for f in findings if f.rule_id == "content-wrong-section"]
    assert len(wrong_section) == 0, f"不应报错，实际报了{len(wrong_section)}条"


def test_no_match_no_error():
    """无关键词匹配（市场观察兜底）→ 不报错."""
    paragraphs = [
        "监管动态",
        "本周A股主要指数集体走强",
        "本周A股主要指数集体走强，延续跨年强势行情...",
    ]
    findings = check_section_mismatch(paragraphs)
    wrong_section = [f for f in findings if f.rule_id == "content-wrong-section"]
    assert len(wrong_section) == 0


def test_pbc_in_regulatory():
    """人民银行会议放在监管动态 → 不报错."""
    paragraphs = [
        "监管动态",
        "人民银行公布2025年金融统计数据报告",
        "近日，中国人民银行官网公布2025年金融统计数据报告...",
    ]
    findings = check_section_mismatch(paragraphs)
    wrong_section = [f for f in findings if f.rule_id == "content-wrong-section"]
    assert len(wrong_section) == 0


def test_csrc_in_regulatory():
    """证监会会议放在监管动态 → 不报错."""
    paragraphs = [
        "监管动态",
        "证监会发布科创板股票做市交易规则",
        "证监会近日发布《科创板股票做市交易规则》...",
    ]
    findings = check_section_mismatch(paragraphs)
    wrong_section = [f for f in findings if f.rule_id == "content-wrong-section"]
    assert len(wrong_section) == 0


def test_citation_context_not_flagged():
    """正文引用'中国人民银行数据'不应作为主体报错."""
    paragraphs = [
        "市场观察",
        "需求疲软下中国新增贷款降至七年新低",
        "2025年全年，中国银行业新增贷款额创2018年以来最低水平。据中国人民银行1月15日公布的数据计算，12月金融机构新增人民币贷款...",
    ]
    findings = check_section_mismatch(paragraphs)
    wrong_section = [f for f in findings if f.rule_id == "content-wrong-section"]
    assert len(wrong_section) == 0, f"正文引用数据不应报错，实际报了{len(wrong_section)}条: {[f.description for f in wrong_section]}"


def test_news_title_only_checked():
    """正文含央行关键词但标题不含 → 不报错."""
    paragraphs = [
        "市场观察",
        "需求疲软下中国新增贷款降至七年新低",
        "2025年全年，中国人民银行...数据...",
    ]
    findings = check_section_mismatch(paragraphs)
    wrong_section = [f for f in findings if f.rule_id == "content-wrong-section"]
    assert len(wrong_section) == 0, f"正文关键词不应触发检测，实际报了{len(wrong_section)}条"


def test_consecutive_titles_deduped():
    """同一新闻的多个标题+正文只报一条."""
    paragraphs = [
        "监管动态",
        "国务院党组会议召开学习贯彻习近平总书记在二十届中央纪委五次全会上的重要讲话和全会精神",
        "1月14日，国务院总理、党组书记李强主持召开国务院党组会议...",
        "1月14日，国务院总理、党组书记李强主持召开国务院党组会议，学习贯彻习近平总书记在二十届中央纪委五次全会上的重要讲话和全会精神，部署进一步推动政府党风廉政建设和...",
    ]
    findings = check_section_mismatch(paragraphs)
    wrong_section = [f for f in findings if f.rule_id == "content-wrong-section"]
    # 只有一个标题段落含实体关键词（段37），应该只报1条
    assert len(wrong_section) == 1, f"应只报1条，实际报了{len(wrong_section)}条: {[f.paragraph_index for f in wrong_section]}"
