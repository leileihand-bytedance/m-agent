from datetime import date
from pathlib import Path

import pytest

from app.platform.tools import ToolGateway
from skills.shenyinxie_news.schema import ArticleAssessment, ShenyinxieNewsResult
from skills.shenyinxie_news.workflow import run


def _search_result(url: str, title: str, snippet: str = "") -> dict[str, str]:
    return {"url": url, "title": title, "snippet": snippet, "source": "media"}


def _web_page(
    title: str,
    body: str,
    publish_date: str = "",
    site: str = "",
    canonical_url: str = "",
) -> dict[str, str]:
    return {
        "url": "",
        "title": title,
        "text": body,
        "publish_date": publish_date,
        "site": site,
        "canonical_url": canonical_url or "",
        "date_extracted_from": "meta:article:published_time" if publish_date else "",
    }


def _make_gateway(*, search_results, web_pages, assessments=None, assessment_errors=None):
    """构造 mock ToolGateway。"""
    calls: list[tuple[str, object]] = []

    def search(query, max_results=5):
        calls.append(("search", query))
        for prefix, results in search_results.items():
            if query.startswith(prefix):
                return results
        return []

    def web_reader(url):
        calls.append(("web_reader", url))
        return web_pages.get(url, _web_page(title="未知", body=""))

    def llm_writer(payload):
        url = payload["candidate_url"]
        calls.append(("llm_writer", url))
        if assessment_errors and url in assessment_errors:
            raise RuntimeError("候选判断失败")
        if assessments and url in assessments:
            return assessments[url]
        return ArticleAssessment(
            decision="full_text",
            is_positive_achievement=True,
            subject_strength="primary",
            reason="全文聚焦微众银行正面成果。",
            achievement_types=["业务成果"],
        )

    return (
        ToolGateway(
            allowed_tools=("search", "web_reader", "llm_writer"),
            tools={"search": search, "web_reader": web_reader, "llm_writer": llm_writer},
        ),
        calls,
    )


def test_workflow_returns_three_selected_articles(tmp_path):
    today = date(2026, 7, 29)

    urls = [
        "https://people.com.cn/1",
        "https://people.com.cn/2",
        "https://sztqb.sznews.com/3",
        "https://sztqb.sznews.com/4",
    ]

    search_results = {
        "微众银行": [
            _search_result(urls[0], "微众银行发布年报"),
            _search_result(urls[1], "微众银行推出新服务"),
        ],
        "深圳前海微众银行": [
            _search_result(urls[2], "微众银行深圳动态"),
            _search_result(urls[3], "微众银行合作"),
        ],
    }

    web_pages = {
        urls[0]: _web_page(
            title="微众银行发布年报",
            body="2026年7月15日，微众银行发布年报，营收增长20%。" * 10,
            publish_date="2026-07-15",
            site="people.com.cn",
            canonical_url=urls[0],
        ),
        urls[1]: _web_page(
            title="微众银行推出新服务",
            body="2026年7月16日，微众银行宣布推出普惠金融新服务。" * 10,
            publish_date="2026-07-16",
            site="people.com.cn",
            canonical_url=urls[1],
        ),
        urls[2]: _web_page(
            title="微众银行深圳动态",
            body="2026年7月17日，深圳前海微众银行参与地方金融合作。" * 10,
            publish_date="2026-07-17",
            site="sztqb.sznews.com",
            canonical_url=urls[2],
        ),
        urls[3]: _web_page(
            title="微众银行合作",
            body="2026年7月18日，微众银行与某机构签署合作协议。" * 10,
            publish_date="2026-07-18",
            site="sztqb.sznews.com",
            canonical_url=urls[3],
        ),
    }

    gateway, calls = _make_gateway(search_results=search_results, web_pages=web_pages)

    result = run({"output_dir": str(tmp_path / "output"), "today": today}, gateway)

    assert isinstance(result, ShenyinxieNewsResult)
    assert result.needs_clarification is False
    assert len(result.articles) == 3
    assert len(result.sources) == 3
    assert result.output_file != ""
    assert Path(result.output_file).exists()
    assert "深圳银行业协会工作动态" in result.title
    assert "动态一" in result.body
    assert "微众银行" in result.body
    # 验证搜索和网页读取都被调用
    assert any(c[0] == "search" for c in calls)
    assert any(c[0] == "web_reader" for c in calls)


def test_workflow_returns_zero_when_no_qualified_candidates(tmp_path):
    today = date(2026, 7, 29)

    url = "https://people.com.cn/old"
    search_results = {"微众银行": [_search_result(url, "旧闻")]}
    web_pages = {
        url: _web_page(
            title="旧闻",
            body="2026年6月1日，微众银行旧闻。" * 10,
            publish_date="2026-06-01",
            site="people.com.cn",
            canonical_url=url,
        ),
    }

    gateway, _ = _make_gateway(search_results=search_results, web_pages=web_pages)

    result = run({"output_dir": str(tmp_path / "output"), "today": today}, gateway)

    assert result.needs_clarification is False
    assert len(result.articles) == 0
    assert "未检索到" in result.message


def test_workflow_handles_search_failure(tmp_path):
    today = date(2026, 7, 29)

    def failing_search(query, max_results=5):
        raise RuntimeError("搜索服务不可用")

    gateway = ToolGateway(
        allowed_tools=("search", "web_reader", "llm_writer"),
        tools={
            "search": failing_search,
            "web_reader": lambda url: _web_page(title="", body=""),
            "llm_writer": lambda x: x,
        },
    )

    result = run({"output_dir": str(tmp_path / "output"), "today": today}, gateway)

    assert result.needs_clarification is False
    assert "无法完成联网检索" in result.message


def test_workflow_downgrades_to_one_article(tmp_path):
    today = date(2026, 7, 29)

    url = "https://people.com.cn/1"
    search_results = {"微众银行": [_search_result(url, "微众银行发布年报")]}
    web_pages = {
        url: _web_page(
            title="微众银行发布年报",
            body="2026年7月16日，微众银行发布年报，营收增长20%。" * 10,
            publish_date="2026-07-16",
            site="people.com.cn",
            canonical_url=url,
        ),
    }

    gateway, _ = _make_gateway(search_results=search_results, web_pages=web_pages)

    result = run({"output_dir": str(tmp_path / "output"), "today": today}, gateway)

    assert len(result.articles) == 1
    assert "动态一" in result.body
    assert "动态二" not in result.body


def test_workflow_extracts_only_weizhong_paragraphs_from_roundup(tmp_path):
    today = date(2026, 7, 16)
    url = "https://people.com.cn/roundup"
    paragraph_one = (
        "微众银行连续两年实施利润分配，相关方案已经股东会批准，"
        "体现出该行持续稳健的经营能力和较好的盈利基础。"
    )
    paragraph_two = (
        "该行完成现金股利派发，并继续保持稳健的资本补充安排，"
        "在实施利润分配的同时兼顾后续业务发展需要。"
    )
    body = "\n\n".join(
        [
            "多家民营银行近期披露经营情况。",
            paragraph_one,
            paragraph_two,
            "其他银行也分别披露利润分配和资本补充安排。",
        ]
    )
    search_results = {"微众银行": [_search_result(url, "民营银行也分红，微众等连续派现")]}
    web_pages = {
        url: _web_page(
            title="民营银行也分红，微众等连续派现",
            body=body,
            publish_date="2026-07-10",
            site="people.com.cn",
            canonical_url=url,
        )
    }
    assessments = {
        url: ArticleAssessment(
            decision="extract",
            is_positive_achievement=True,
            subject_strength="substantial",
            suggested_title="微众银行连续两年实施利润分配",
            excerpt_paragraphs=[paragraph_one, paragraph_two],
            achievement_types=["经营成果"],
            reason="综合稿中存在可独立成立的微众银行成果段落。",
        )
    }
    gateway, calls = _make_gateway(
        search_results=search_results,
        web_pages=web_pages,
        assessments=assessments,
    )

    result = run({"output_dir": str(tmp_path / "output"), "today": today}, gateway)

    assert len(result.articles) == 1
    assert result.articles[0].title == "微众银行连续两年实施利润分配"
    assert result.articles[0].body == f"{paragraph_one}\n\n{paragraph_two}"
    assert result.articles[0].content_mode == "extract"
    assert "【原报道标题】民营银行也分红，微众等连续派现" in result.body
    assert "【摘编说明】说明：本文根据原报道中微众银行相关内容摘编。" in result.body
    assert any(call == ("llm_writer", url) for call in calls)


def test_workflow_searches_expanded_sources_only_when_primary_full_text_is_insufficient(tmp_path):
    today = date(2026, 7, 16)
    primary_url = "https://people.com.cn/primary"
    expanded_url = "https://www.cnr.cn/expanded"
    search_results = {
        "微众银行 2026年7月1日至2026年7月15日": [
            _search_result(primary_url, "微众银行发布普惠金融成果")
        ],
        "微众银行 央广网": [
            _search_result(expanded_url, "微众银行科技创新取得新成果")
        ],
    }
    web_pages = {
        primary_url: _web_page(
            title="微众银行发布普惠金融成果",
            body="微众银行发布普惠金融成果，服务实体经济成效持续提升。" * 15,
            publish_date="2026-07-10",
            site="people.com.cn",
            canonical_url=primary_url,
        ),
        expanded_url: _web_page(
            title="微众银行科技创新取得新成果",
            body="微众银行科技创新取得新成果，数字金融服务能力继续增强。" * 15,
            publish_date="2026-07-12",
            site="cnr.cn",
            canonical_url=expanded_url,
        ),
    }
    gateway, calls = _make_gateway(search_results=search_results, web_pages=web_pages)

    result = run({"output_dir": str(tmp_path / "output"), "today": today}, gateway)

    assert {article.original_url for article in result.articles} == {primary_url, expanded_url}
    search_calls = [query for kind, query in calls if kind == "search"]
    assert any(query.startswith("微众银行 央广网") for query in search_calls)


def test_workflow_expands_when_primary_full_text_contains_duplicate_reports(tmp_path):
    today = date(2026, 7, 16)
    first_url = "https://people.com.cn/feature"
    repost_url = "https://xinhuanet.com/feature-repost"
    second_url = "https://people.com.cn/second"
    expanded_url = "https://www.cnr.cn/third"
    primary_results = [
        _search_result(first_url, "微众银行发布普惠金融年度成果"),
        _search_result(repost_url, "微众银行发布普惠金融年度成果"),
        _search_result(second_url, "微众银行科技创新取得进展"),
    ]
    search_results = {
        "微众银行 2026年7月1日至2026年7月15日": primary_results,
        "微众银行 央广网": [_search_result(expanded_url, "微众银行服务实体经济再获成果")],
    }
    duplicate_body = "微众银行发布普惠金融年度成果，服务小微企业的覆盖范围持续扩大。" * 15
    web_pages = {
        first_url: _web_page(
            title="微众银行发布普惠金融年度成果",
            body=duplicate_body,
            publish_date="2026-07-10",
            site="people.com.cn",
            canonical_url=first_url,
        ),
        repost_url: _web_page(
            title="微众银行发布普惠金融年度成果",
            body=duplicate_body,
            publish_date="2026-07-10",
            site="xinhuanet.com",
            canonical_url=repost_url,
        ),
        second_url: _web_page(
            title="微众银行科技创新取得进展",
            body="微众银行科技创新取得进展，数字金融服务能力持续增强。" * 15,
            publish_date="2026-07-11",
            site="people.com.cn",
            canonical_url=second_url,
        ),
        expanded_url: _web_page(
            title="微众银行服务实体经济再获成果",
            body="微众银行服务实体经济再获成果，普惠金融服务质效继续提升。" * 15,
            publish_date="2026-07-12",
            site="cnr.cn",
            canonical_url=expanded_url,
        ),
    }
    gateway, calls = _make_gateway(search_results=search_results, web_pages=web_pages)

    result = run({"output_dir": str(tmp_path / "output"), "today": today}, gateway)

    assert len(result.articles) == 3
    assert expanded_url in result.sources
    search_calls = [query for kind, query in calls if kind == "search"]
    assert any(query.startswith("微众银行 央广网") for query in search_calls)


def test_workflow_rejects_neutral_roundup_even_when_title_contains_weizhong(tmp_path):
    today = date(2026, 7, 16)
    url = "https://people.com.cn/neutral"
    search_results = {"微众银行": [_search_result(url, "民营银行观察：微众等机构披露数据")]}
    web_pages = {
        url: _web_page(
            title="民营银行观察：微众等机构披露数据",
            body="多家民营银行披露经营数据，其中包括微众银行。" * 20,
            publish_date="2026-07-10",
            site="people.com.cn",
            canonical_url=url,
        )
    }
    assessments = {
        url: ArticleAssessment(
            decision="reject",
            is_positive_achievement=False,
            subject_strength="mention",
            reason="中性行业盘点，微众银行仅为并列提及。",
        )
    }
    gateway, _ = _make_gateway(
        search_results=search_results,
        web_pages=web_pages,
        assessments=assessments,
    )

    result = run({"output_dir": str(tmp_path / "output"), "today": today}, gateway)

    assert result.articles == []
    assert "未检索到" in result.message


def test_workflow_continues_when_one_model_assessment_fails(tmp_path):
    today = date(2026, 7, 16)
    failed_url = "https://people.com.cn/fail"
    good_url = "https://people.com.cn/good"
    search_results = {
        "微众银行": [
            _search_result(failed_url, "微众银行候选一"),
            _search_result(good_url, "微众银行发布成果"),
        ]
    }
    web_pages = {
        failed_url: _web_page(
            title="微众银行候选一",
            body="微众银行候选内容。" * 20,
            publish_date="2026-07-10",
            site="people.com.cn",
            canonical_url=failed_url,
        ),
        good_url: _web_page(
            title="微众银行发布成果",
            body="微众银行发布普惠金融成果。" * 20,
            publish_date="2026-07-11",
            site="people.com.cn",
            canonical_url=good_url,
        ),
    }
    gateway, _ = _make_gateway(
        search_results=search_results,
        web_pages=web_pages,
        assessment_errors={failed_url},
    )

    result = run({"output_dir": str(tmp_path / "output"), "today": today}, gateway)

    assert len(result.articles) == 1
    assert result.articles[0].original_url == good_url


def test_workflow_reports_model_assessment_unavailable_when_all_candidates_fail(tmp_path):
    today = date(2026, 7, 16)
    url = "https://people.com.cn/fail"
    search_results = {"微众银行": [_search_result(url, "微众银行候选")]}
    web_pages = {
        url: _web_page(
            title="微众银行候选",
            body="微众银行发布业务成果。" * 20,
            publish_date="2026-07-10",
            site="people.com.cn",
            canonical_url=url,
        )
    }
    gateway, _ = _make_gateway(
        search_results=search_results,
        web_pages=web_pages,
        assessment_errors={url},
    )

    result = run({"output_dir": str(tmp_path / "output"), "today": today}, gateway)

    assert result.articles == []
    assert "候选内容判断" in result.message
