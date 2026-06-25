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
