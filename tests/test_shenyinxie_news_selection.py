from datetime import date

import pytest

from app.platform.tools import ToolGateway
from skills.shenyinxie_news.schema import NewsCandidate
from skills.shenyinxie_news.selection import (
    MediaSource,
    MediaWhitelist,
    apply_rule_relevance,
    dedupe_by_url,
    dedupe_same_article,
    extract_publish_date,
    hard_gate,
    is_body_readable,
    is_date_in_period,
    score_candidates_rule_based,
    select_top_candidates,
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
