from datetime import date

import pytest

from app.platform.tools import ToolGateway
from skills.shenyinxie_news.schema import ArticleAssessment, NewsCandidate
from skills.shenyinxie_news.selection import (
    MediaSource,
    MediaWhitelist,
    apply_editorial_assessment,
    apply_rule_relevance,
    dedupe_by_url,
    dedupe_same_article,
    extract_publish_date,
    generate_expanded_search_queries,
    generate_fallback_search_queries,
    generate_primary_search_queries,
    hard_gate,
    is_body_readable,
    is_date_in_period,
    score_candidates_rule_based,
    select_top_candidates,
    strip_trailing_media_title_suffix,
    validate_excerpt_paragraphs,
)


@pytest.fixture
def whitelist():
    return MediaWhitelist(
        [
            MediaSource(name="人民网", category="中央级媒体", domains=["people.com.cn"], tier=1),
            MediaSource(name="深圳特区报", category="深圳主流党媒", domains=["sztqb.sznews.com"], tier=2),
        ]
    )


def test_extract_publish_date_from_iso():
    page = {"publish_date": "2026-07-15"}
    assert extract_publish_date(page) == date(2026, 7, 15)


def test_extract_publish_date_returns_none_when_empty():
    assert extract_publish_date({"publish_date": ""}) is None


def test_is_date_in_period():
    assert is_date_in_period(date(2026, 7, 15), date(2026, 7, 1), date(2026, 7, 31))
    assert not is_date_in_period(date(2026, 6, 30), date(2026, 7, 1), date(2026, 7, 31))


def test_is_body_readable():
    assert is_body_readable("微众银行今日宣布推出新服务。" * 20)
    assert not is_body_readable("微众银行")


def test_hard_gate_passes(whitelist):
    candidate = NewsCandidate(
        url="https://people.com.cn/article/1",
        canonical_url="https://people.com.cn/article/1",
        title="微众银行推出新服务",
        site="people.com.cn",
        media_name="人民网",
        media_tier=1,
        publish_date="2026-07-15",
        body="微众银行今日宣布推出新服务。" * 30,
    )
    ok, reason = hard_gate(candidate, date(2026, 7, 1), date(2026, 7, 31), whitelist)
    assert ok
    assert reason == ""


def test_hard_gate_rejects_domain_not_in_whitelist(whitelist):
    candidate = NewsCandidate(
        url="https://example.com/article/1",
        canonical_url="https://example.com/article/1",
        title="微众银行推出新服务",
        site="example.com",
        publish_date="2026-07-15",
        body="微众银行今日宣布推出新服务。" * 30,
    )
    ok, reason = hard_gate(candidate, date(2026, 7, 1), date(2026, 7, 31), whitelist)
    assert not ok
    assert "白名单" in reason


def test_hard_gate_rejects_out_of_period(whitelist):
    candidate = NewsCandidate(
        url="https://people.com.cn/article/1",
        canonical_url="https://people.com.cn/article/1",
        title="微众银行推出新服务",
        site="people.com.cn",
        publish_date="2026-06-15",
        body="微众银行今日宣布推出新服务。" * 30,
    )
    ok, reason = hard_gate(candidate, date(2026, 7, 1), date(2026, 7, 31), whitelist)
    assert not ok
    assert "范围" in reason


def test_hard_gate_rejects_unreadable_body(whitelist):
    candidate = NewsCandidate(
        url="https://people.com.cn/article/1",
        canonical_url="https://people.com.cn/article/1",
        title="微众银行推出新服务",
        site="people.com.cn",
        publish_date="2026-07-15",
        body="微众银行",
    )
    ok, reason = hard_gate(candidate, date(2026, 7, 1), date(2026, 7, 31), whitelist)
    assert not ok
    assert "正文" in reason


def test_dedupe_by_url():
    candidates = [
        NewsCandidate(url="https://a.com/1", canonical_url="https://a.com/1", title="A", site="a.com", body="x"),
        NewsCandidate(url="https://a.com/1/", canonical_url="https://a.com/1", title="A2", site="a.com", body="y"),
        NewsCandidate(url="https://a.com/2", canonical_url="https://a.com/2", title="B", site="a.com", body="z"),
    ]
    result = dedupe_by_url(candidates)
    assert len(result) == 2


def test_dedupe_same_article_keeps_higher_authority():
    candidates = [
        NewsCandidate(
            url="https://people.com.cn/original",
            canonical_url="https://people.com.cn/original",
            title="微众银行发布年报",
            site="people.com.cn",
            media_name="人民网",
            media_tier=1,
            publish_date="2026-07-15",
            body="微众银行今日发布年报，营收增长显著。" * 10,
        ),
        NewsCandidate(
            url="https://sztqb.sznews.com/repost",
            canonical_url="https://sztqb.sznews.com/repost",
            title="微众银行发布年报",
            site="sztqb.sznews.com",
            media_name="深圳特区报",
            media_tier=2,
            publish_date="2026-07-15",
            body="微众银行今日发布年报，营收增长显著。" * 10,
        ),
    ]
    result = dedupe_same_article(candidates)
    assert len(result) == 1
    assert result[0].url == "https://people.com.cn/original"


def test_dedupe_same_article_ignores_trailing_media_title_suffix():
    body = "微众银行党委坚持党建引领和科技创新，持续推动金融高质量发展。" * 20
    candidates = [
        NewsCandidate(
            url="http://m.eeo.com.cn/original",
            canonical_url="http://m.eeo.com.cn/original",
            title="微众银行：以党建引领金融高质量发展-经济观察网",
            site="m.eeo.com.cn",
            media_name="经济观察报",
            media_tier=2,
            publish_date="2026-07-01",
            body="2026-07-01 20:30\n" + body,
        ),
        NewsCandidate(
            url="https://m.21jingji.com/repost",
            canonical_url="https://m.21jingji.com/repost",
            title="微众银行：以党建引领金融高质量发展 - 21世纪经济报道",
            site="m.21jingji.com",
            media_name="21世纪经济报道",
            media_tier=2,
            publish_date="2026-07-01",
            body=body,
        ),
    ]

    result = dedupe_same_article(candidates)

    assert len(result) == 1
    assert result[0].url == "http://m.eeo.com.cn/original"


def test_strip_trailing_media_title_suffix_preserves_article_title():
    assert (
        strip_trailing_media_title_suffix(
            "微众银行：以党建引领金融高质量发展 - 21世纪经济报道"
        )
        == "微众银行：以党建引领金融高质量发展"
    )
    assert strip_trailing_media_title_suffix("微众银行发布新成果") == "微众银行发布新成果"


def test_apply_rule_relevance():
    candidates = [
        NewsCandidate(
            url="https://a.com/1",
            canonical_url="https://a.com/1",
            title="微众银行推出新服务",
            site="a.com",
            body="微众银行今日宣布推出新服务。" * 10,
        ),
        NewsCandidate(
            url="https://a.com/2",
            canonical_url="https://a.com/2",
            title="行业综述",
            site="a.com",
            body="（ lengthy industry overview ... ）" * 30 + "顺带提到微众银行。",
        ),
    ]
    apply_rule_relevance(candidates)
    assert candidates[0].is_core_subject is True
    assert candidates[1].is_core_subject is False


def test_roundup_title_containing_weizhong_is_not_enough_for_full_text():
    body = "\n\n".join(
        [
            "全国多家民营银行陆续披露利润分配方案，行业整体仍以留存利润补充资本为主。",
            "微众银行连续两年实施利润分配，相关方案已经股东会批准并完成现金股利派发。",
            "其他多家民营银行也披露了分红安排，部分机构则继续保留利润补充资本。",
        ]
    )
    candidate = NewsCandidate(
        url="https://people.com.cn/roundup",
        canonical_url="https://people.com.cn/roundup",
        title="民营银行也分红，微众等已连续两年派现",
        site="people.com.cn",
        body=body,
    )

    apply_rule_relevance([candidate])

    assert candidate.is_core_subject is not True


def test_primary_positive_assessment_keeps_full_text():
    body = "微众银行发布普惠金融年度成果。" * 20
    candidate = NewsCandidate(
        url="https://people.com.cn/feature",
        canonical_url="https://people.com.cn/feature",
        title="微众银行发布普惠金融年度成果",
        site="people.com.cn",
        body=body,
    )
    assessment = ArticleAssessment(
        decision="full_text",
        is_positive_achievement=True,
        subject_strength="primary",
        reason="全文聚焦微众银行普惠金融成果。",
        achievement_types=["普惠金融成果"],
    )

    selected = apply_editorial_assessment(candidate, assessment)

    assert selected is candidate
    assert selected.body == body
    assert selected.content_mode == "full_text"
    assert selected.source_title == candidate.title


def test_substantial_positive_assessment_accepts_exact_ordered_excerpt():
    paragraph_one = "微众银行持续推进数字普惠金融服务，进一步扩大对小微企业的服务覆盖。"
    paragraph_two = "该行依托金融科技降低服务成本，并形成了可核验的普惠金融服务成果。"
    body = "\n\n".join(
        [
            "多家银行近期披露数字普惠金融实践。",
            paragraph_one,
            paragraph_two,
            "其他银行也介绍了各自的服务举措。",
        ]
    )
    candidate = NewsCandidate(
        url="https://people.com.cn/roundup",
        canonical_url="https://people.com.cn/roundup",
        title="银行业数字普惠金融实践观察",
        site="people.com.cn",
        body=body,
    )
    assessment = ArticleAssessment(
        decision="extract",
        is_positive_achievement=True,
        subject_strength="substantial",
        suggested_title="微众银行持续提升数字普惠金融服务质效",
        excerpt_paragraphs=[paragraph_one, paragraph_two],
        achievement_types=["普惠金融成果"],
        reason="综合稿包含可独立成立的微众银行成果段落。",
    )

    selected = apply_editorial_assessment(candidate, assessment)

    assert selected is candidate
    assert selected.title == "微众银行持续提升数字普惠金融服务质效"
    assert selected.body == f"{paragraph_one}\n\n{paragraph_two}"
    assert selected.content_mode == "extract"
    assert selected.source_title == "银行业数字普惠金融实践观察"
    assert "摘编" in selected.editor_note


def test_dividend_roundup_is_not_reportable_even_when_model_marks_it_positive():
    paragraph_one = "微众银行连续两年实施利润分配，相关方案已经股东会批准。"
    paragraph_two = "该行本次派发现金股利，并继续保持稳健的资本补充安排。"
    candidate = NewsCandidate(
        url="https://stcn.com/dividend-roundup",
        canonical_url="https://stcn.com/dividend-roundup",
        title="民营银行也分红，微众等已连续两年派现",
        source_title="民营银行也分红，微众等已连续两年派现",
        site="stcn.com",
        body="\n\n".join((paragraph_one, paragraph_two)),
    )
    assessment = ArticleAssessment(
        decision="extract",
        is_positive_achievement=True,
        subject_strength="substantial",
        suggested_title="微众银行连续两年实施现金分红",
        excerpt_paragraphs=[paragraph_one, paragraph_two],
        achievement_types=["经营成果"],
        reason="模型认为分红反映经营情况。",
    )

    assert apply_editorial_assessment(candidate, assessment) is None


def test_reportable_technology_story_is_not_rejected_for_incidental_dividend_mention():
    body = "微众银行发布金融科技创新成果，并在报道末尾披露年度分红安排。" * 10
    candidate = NewsCandidate(
        url="https://people.com.cn/technology-feature",
        canonical_url="https://people.com.cn/technology-feature",
        title="微众银行金融科技创新成果落地并披露分红安排",
        site="people.com.cn",
        body=body,
    )
    assessment = ArticleAssessment(
        decision="full_text",
        is_positive_achievement=True,
        subject_strength="primary",
        achievement_types=["科技创新成果"],
        reason="全文核心是金融科技创新成果，分红只是附带信息。",
    )

    assert apply_editorial_assessment(candidate, assessment) is candidate


@pytest.mark.parametrize(
    "paragraphs",
    [
        ["模型改写出来、并不存在于原文中的微众银行成果段落。"],
        ["第二段微众银行成果。", "第一段微众银行成果。"],
        ["包括微众银行在内的多家机构参加活动。"],
    ],
)
def test_excerpt_validation_rejects_missing_reordered_or_list_only_text(paragraphs):
    source = "第一段微众银行成果。\n\n第二段微众银行成果。\n\n包括微众银行在内的多家机构参加活动。"

    assert validate_excerpt_paragraphs(source, paragraphs) is None


def test_score_candidates_rule_based():
    candidates = [
        NewsCandidate(
            url="https://people.com.cn/1",
            canonical_url="https://people.com.cn/1",
            title="微众银行推出新服务",
            site="people.com.cn",
            media_name="人民网",
            media_tier=1,
            publish_date="2026-07-15",
            body="2026年7月15日，微众银行宣布推出新服务，覆盖超过1000万用户。" * 10,
        ),
    ]
    apply_rule_relevance(candidates)
    score_candidates_rule_based(candidates)
    assert candidates[0].total_score > 0
    assert candidates[0].authority_score == 9.0


def test_select_top_candidates_respects_target():
    candidates = [
        NewsCandidate(
            url=f"https://a.com/{i}",
            canonical_url=f"https://a.com/{i}",
            title=f"微众银行{'发布' if i % 2 == 0 else '推出'}{['年报', '新服务', '合作计划', '技术升级', '普惠金融'][i]}",
            site="a.com",
            media_name="人民网",
            media_tier=1,
            publish_date="2026-07-15",
            body=f"2026年7月15日，微众银行宣布{i}。" * 10,
        )
        for i in range(5)
    ]
    apply_rule_relevance(candidates)
    score_candidates_rule_based(candidates)
    selected = select_top_candidates(candidates, target=3)
    assert len(selected) == 3


def test_select_top_candidates_falls_back_to_one():
    candidates = [
        NewsCandidate(
            url="https://a.com/1",
            canonical_url="https://a.com/1",
            title="微众银行新闻",
            site="a.com",
            media_name="人民网",
            media_tier=1,
            publish_date="2026-07-15",
            body="2026年7月15日，微众银行宣布。" * 10,
        ),
    ]
    apply_rule_relevance(candidates)
    score_candidates_rule_based(candidates)
    selected = select_top_candidates(candidates, target=3)
    assert len(selected) == 1


def test_search_queries_include_exact_publication_period_and_are_staged():
    period_start = date(2026, 7, 1)
    period_end = date(2026, 7, 15)

    primary = generate_primary_search_queries(period_start, period_end)
    expanded = generate_expanded_search_queries(period_start, period_end)
    fallback = generate_fallback_search_queries(period_start, period_end)

    assert primary
    assert expanded
    assert fallback
    assert all(
        "2026年7月1日至2026年7月15日" in query
        for query in primary + expanded + fallback
    )
    assert all("2026年7月" in query for query in primary + expanded + fallback)
    assert not any("site:" in query for query in primary + expanded + fallback)
    assert not any("科技创新助推数字化金融普惠发展" in query for query in primary)
    assert any("普惠金融" in query and "小微企业" in query for query in primary)
    assert any("消费者权益" in query and "征信" in query for query in primary)
    assert any("社会责任" in query and "乡村振兴" in query for query in primary)
    assert any("获奖" in query and "荣誉" in query for query in primary)
    assert any("国际化" in query and "技术输出" in query for query in primary)
    assert any("央广网" in query for query in expanded)
    assert any("南方" in query for query in expanded)
    assert any("北青网" in query and "投资界" in query for query in fallback)


def test_fallback_queries_include_month_specific_financial_education_events():
    march = generate_fallback_search_queries(date(2026, 3, 16), date(2026, 3, 31))
    june = generate_fallback_search_queries(date(2026, 6, 16), date(2026, 6, 30))
    september = generate_fallback_search_queries(date(2026, 9, 1), date(2026, 9, 15))
    july = generate_fallback_search_queries(date(2026, 7, 1), date(2026, 7, 15))

    assert any("3.15" in query and "消费者权益保护" in query for query in march)
    assert any("6.14" in query and "信用记录关爱日" in query for query in june)
    assert any("金融教育宣传月" in query for query in september)
    assert not any("6.14" in query or "3.15" in query for query in july)
