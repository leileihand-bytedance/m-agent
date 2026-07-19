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
    assert result.title == "微众银行2026年7月第2期信息动态"
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
    assert "均未通过发布日期或正文完整性核验" in result.message


def test_workflow_asks_for_half_month_when_instruction_omits_it(tmp_path):
    gateway, calls = _make_gateway(search_results={}, web_pages={})

    result = run(
        {
            "text": "生成深银协动态",
            "output_dir": str(tmp_path / "output"),
            "today": date(2026, 7, 17),
        },
        gateway,
    )

    assert result.needs_clarification is True
    assert "上半月" in result.message
    assert "下半月" in result.message
    assert calls == []


def test_workflow_honors_explicit_upper_half_month_instruction(tmp_path):
    today = date(2026, 7, 17)
    url = "https://paper.people.com.cn/rmrb/pc/content/202607/11/example.html"
    search_results = {
        "微众银行 新闻 报道": [
            _search_result(url, "科技创新助推数字化金融普惠发展")
        ]
    }
    web_pages = {
        url: _web_page(
            title="科技创新助推数字化金融普惠发展",
            body="微众银行通过科技创新推动数字普惠金融发展，服务实体经济质效持续提升。" * 15,
            publish_date="2026-07-11",
            site="paper.people.com.cn",
            canonical_url=url,
        )
    }
    gateway, calls = _make_gateway(search_results=search_results, web_pages=web_pages)

    result = run(
        {
            "text": "生成7月上半月的深银协动态",
            "output_dir": str(tmp_path / "output"),
            "today": today,
        },
        gateway,
    )

    assert result.period_start == "2026-07-01"
    assert result.period_end == "2026-07-15"
    assert result.title == "微众银行2026年7月第1期信息动态"
    assert Path(result.output_file).name == "【深银协】微众银行2026年7月第1期信息动态.docx"
    assert len(result.articles) == 1
    search_calls = [query for kind, query in calls if kind == "search"]
    assert any("2026年7月1日至2026年7月15日" in query for query in search_calls)


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
        "微众银行持续推进数字普惠金融服务，进一步扩大对小微企业的服务覆盖，"
        "形成了可核验的服务成效。"
    )
    paragraph_two = (
        "该行依托金融科技降低服务成本，并持续提升数字化服务能力，"
        "相关实践取得了明确进展。"
    )
    body = "\n\n".join(
        [
            "多家银行近期披露数字普惠金融实践。",
            paragraph_one,
            paragraph_two,
            "其他银行也分别介绍了服务举措。",
        ]
    )
    search_results = {"微众银行": [_search_result(url, "银行业数字普惠金融实践观察")]}
    web_pages = {
        url: _web_page(
            title="银行业数字普惠金融实践观察",
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
            suggested_title="微众银行持续提升数字普惠金融服务质效",
            excerpt_paragraphs=[paragraph_one, paragraph_two],
            achievement_types=["普惠金融成果"],
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
    assert result.articles[0].title == "微众银行持续提升数字普惠金融服务质效"
    assert result.articles[0].body == f"{paragraph_one}\n\n{paragraph_two}"
    assert result.articles[0].content_mode == "extract"
    assert "【原报道标题】银行业数字普惠金融实践观察" in result.body
    assert "【摘编说明】说明：本文根据原报道中微众银行相关内容摘编。" in result.body
    assert any(call == ("llm_writer", url) for call in calls)


def test_workflow_keeps_two_full_reports_instead_of_adding_third_excerpt(tmp_path):
    today = date(2026, 7, 16)
    first_url = "https://people.com.cn/full-one"
    second_url = "https://people.com.cn/full-two"
    excerpt_url = "https://people.com.cn/roundup"
    excerpt_one = "微众银行持续推进数字普惠金融服务，进一步扩大对小微企业的服务覆盖。"
    excerpt_two = "该行依托金融科技降低服务成本，并形成了可核验的普惠金融服务成果。"
    search_results = {
        "微众银行": [
            _search_result(first_url, "微众银行发布普惠金融年度成果"),
            _search_result(second_url, "微众银行科技创新取得新进展"),
            _search_result(excerpt_url, "银行业数字普惠金融实践观察"),
        ]
    }
    web_pages = {
        first_url: _web_page(
            title="微众银行发布普惠金融年度成果",
            body="微众银行发布普惠金融年度成果，服务小微企业的覆盖范围持续扩大。" * 15,
            publish_date="2026-07-10",
            site="people.com.cn",
            canonical_url=first_url,
        ),
        second_url: _web_page(
            title="微众银行科技创新取得新进展",
            body="微众银行科技创新取得新进展，数字金融服务能力持续增强。" * 15,
            publish_date="2026-07-11",
            site="people.com.cn",
            canonical_url=second_url,
        ),
        excerpt_url: _web_page(
            title="银行业数字普惠金融实践观察",
            body="\n\n".join(
                (
                    "多家银行近期介绍数字普惠金融实践和服务实体经济的阶段性进展。" * 4,
                    excerpt_one,
                    excerpt_two,
                    "报道还介绍了其他银行的相关服务举措和后续安排。" * 4,
                )
            ),
            publish_date="2026-07-12",
            site="people.com.cn",
            canonical_url=excerpt_url,
        ),
    }
    assessments = {
        excerpt_url: ArticleAssessment(
            decision="extract",
            is_positive_achievement=True,
            subject_strength="substantial",
            suggested_title="微众银行持续提升数字普惠金融服务质效",
            excerpt_paragraphs=[excerpt_one, excerpt_two],
            achievement_types=["普惠金融成果"],
            reason="综合稿中存在可独立成立的微众银行成果段落。",
        )
    }
    gateway, _ = _make_gateway(
        search_results=search_results,
        web_pages=web_pages,
        assessments=assessments,
    )

    result = run({"output_dir": str(tmp_path / "output"), "today": today}, gateway)

    assert [article.original_url for article in result.articles] == [first_url, second_url]
    assert excerpt_url not in result.sources
    assert result.message == "本期已整理 2 篇报道。"


def test_workflow_keeps_one_full_report_instead_of_adding_excerpt(tmp_path):
    today = date(2026, 7, 16)
    full_url = "https://people.com.cn/full-one"
    excerpt_url = "https://people.com.cn/roundup"
    excerpt_one = "微众银行持续推进数字普惠金融服务，进一步扩大对小微企业的服务覆盖。"
    excerpt_two = "该行依托金融科技降低服务成本，并形成了可核验的普惠金融服务成果。"
    search_results = {
        "微众银行": [
            _search_result(full_url, "微众银行发布普惠金融年度成果"),
            _search_result(excerpt_url, "银行业数字普惠金融实践观察"),
        ]
    }
    web_pages = {
        full_url: _web_page(
            title="微众银行发布普惠金融年度成果",
            body="微众银行发布普惠金融年度成果，服务小微企业的覆盖范围持续扩大。" * 15,
            publish_date="2026-07-10",
            site="people.com.cn",
            canonical_url=full_url,
        ),
        excerpt_url: _web_page(
            title="银行业数字普惠金融实践观察",
            body="\n\n".join(
                (
                    "多家银行近期介绍数字普惠金融实践和服务实体经济的阶段性进展。" * 4,
                    excerpt_one,
                    excerpt_two,
                    "报道还介绍了其他银行的相关服务举措和后续安排。" * 4,
                )
            ),
            publish_date="2026-07-12",
            site="people.com.cn",
            canonical_url=excerpt_url,
        ),
    }
    assessments = {
        excerpt_url: ArticleAssessment(
            decision="extract",
            is_positive_achievement=True,
            subject_strength="substantial",
            suggested_title="微众银行持续提升数字普惠金融服务质效",
            excerpt_paragraphs=[excerpt_one, excerpt_two],
            achievement_types=["普惠金融成果"],
            reason="综合稿中存在可独立成立的微众银行成果段落。",
        )
    }
    gateway, _ = _make_gateway(
        search_results=search_results,
        web_pages=web_pages,
        assessments=assessments,
    )

    result = run({"output_dir": str(tmp_path / "output"), "today": today}, gateway)

    assert [article.original_url for article in result.articles] == [full_url]
    assert excerpt_url not in result.sources
    assert result.message == "本期已整理 1 篇报道。"


def test_workflow_rejects_dividend_roundup_even_if_model_marks_it_positive(tmp_path):
    today = date(2026, 7, 16)
    url = "https://stcn.com/dividend-roundup"
    paragraph_one = "微众银行连续两年实施利润分配，相关方案已经股东会批准。"
    paragraph_two = "该行本次派发现金股利，并继续保持稳健的资本补充安排。"
    search_results = {"微众银行": [_search_result(url, "民营银行也分红，微众等连续派现")]}
    web_pages = {
        url: _web_page(
            title="民营银行也分红，微众等连续派现",
            body="\n\n".join((paragraph_one, paragraph_two)) * 3,
            publish_date="2026-07-10",
            site="stcn.com",
            canonical_url=url,
        )
    }
    assessments = {
        url: ArticleAssessment(
            decision="extract",
            is_positive_achievement=True,
            subject_strength="substantial",
            suggested_title="微众银行连续两年实施现金分红",
            excerpt_paragraphs=[paragraph_one, paragraph_two],
            achievement_types=["经营成果"],
            reason="模型认为分红反映经营情况。",
        )
    }
    gateway, _ = _make_gateway(
        search_results=search_results,
        web_pages=web_pages,
        assessments=assessments,
    )

    result = run({"output_dir": str(tmp_path / "output"), "today": today}, gateway)

    assert result.articles == []
    assert result.output_file == ""


def test_workflow_searches_expanded_sources_only_when_primary_full_text_is_insufficient(tmp_path):
    today = date(2026, 7, 16)
    primary_url = "https://people.com.cn/primary"
    expanded_url = "https://www.cnr.cn/expanded"
    search_results = {
        "微众银行 新闻 报道": [
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


def test_workflow_uses_verified_fallback_media_when_no_mainstream_full_report(tmp_path):
    today = date(2026, 7, 19)
    fallback_url = "https://m.pedaily.cn/99discoveries/132279"
    search_results = {
        "微众银行 北青网": [
            _search_result(
                fallback_url,
                "微众银行举办“守护信用，共赢未来”征信专场直播",
            )
        ]
    }
    web_pages = {
        fallback_url: _web_page(
            title="微众银行举办&quot;守护信用，共赢未来&quot;征信专场直播|投资界",
            body=(
                "微众银行承办金融明白人直播大讲堂，围绕征信知识、反诈案例和消费者权益保护"
                "开展金融教育宣传，吸引众多公众在线观看，取得了可核验的金融为民成效。"
            )
            * 10,
            publish_date="2026-06-16",
            site="news.pedaily.cn",
            canonical_url=fallback_url,
        )
    }
    gateway, calls = _make_gateway(search_results=search_results, web_pages=web_pages)

    result = run(
        {
            "text": "生成2026年6月下半月深银协动态",
            "output_dir": str(tmp_path / "output"),
            "today": today,
        },
        gateway,
    )

    assert [article.original_url for article in result.articles] == [fallback_url]
    assert result.articles[0].title == "微众银行举办\"守护信用，共赢未来\"征信专场直播"
    search_calls = [query for kind, query in calls if kind == "search"]
    assert any(query.startswith("微众银行 北青网") for query in search_calls)


def test_workflow_defers_tier_three_candidate_found_during_primary_search(tmp_path):
    today = date(2026, 7, 19)
    deferred_url = (
        "https://www.dotdotnews.com/a/202606/24/"
        "AP6a3b94fde4b04b6c5d31555f.html"
    )
    search_results = {
        "微众银行 微众科技": [
            _search_result(
                deferred_url,
                "微众科技助力‘一带一路’沿线国家数字经济协同发展",
            )
        ]
    }
    web_pages = {
        deferred_url: _web_page(
            title="微众科技助力‘一带一路’沿线国家数字经济协同发展 - 点新闻",
            body=(
                "微众银行科技子公司微众科技立足香港，依托金融科技能力，"
                "与多个国家和地区的企业开展合作，助力数字经济协同发展。"
            )
            * 12,
            publish_date="2026-06-24",
            site="dotdotnews.com",
            canonical_url=deferred_url,
        )
    }
    gateway, calls = _make_gateway(search_results=search_results, web_pages=web_pages)

    result = run(
        {
            "text": "生成2026年6月下半月深银协动态",
            "output_dir": str(tmp_path / "output"),
            "today": today,
        },
        gateway,
    )

    assert [article.original_url for article in result.articles] == [deferred_url]
    assert ("web_reader", deferred_url) in calls


def test_workflow_requests_ten_results_per_search_query(tmp_path):
    requested_limits: list[int] = []

    def search(query, max_results=5):
        requested_limits.append(max_results)
        return []

    gateway = ToolGateway(
        allowed_tools=("search", "web_reader", "llm_writer"),
        tools={
            "search": search,
            "web_reader": lambda url: _web_page(title="未知", body=""),
            "llm_writer": lambda payload: None,
        },
    )

    result = run(
        {
            "text": "生成2026年6月下半月深银协动态",
            "output_dir": str(tmp_path / "output"),
            "today": date(2026, 7, 19),
        },
        gateway,
    )

    assert result.articles == []
    assert requested_limits
    assert set(requested_limits) == {10}


def test_workflow_does_not_search_fallback_media_when_mainstream_full_report_exists(tmp_path):
    today = date(2026, 7, 16)
    primary_url = "https://people.com.cn/primary"
    search_results = {
        "微众银行 新闻 报道": [
            _search_result(primary_url, "微众银行发布普惠金融成果")
        ]
    }
    web_pages = {
        primary_url: _web_page(
            title="微众银行发布普惠金融成果",
            body="微众银行发布普惠金融成果，服务实体经济成效持续提升。" * 15,
            publish_date="2026-07-10",
            site="people.com.cn",
            canonical_url=primary_url,
        )
    }
    gateway, calls = _make_gateway(search_results=search_results, web_pages=web_pages)

    result = run({"output_dir": str(tmp_path / "output"), "today": today}, gateway)

    assert [article.original_url for article in result.articles] == [primary_url]
    search_calls = [query for kind, query in calls if kind == "search"]
    assert not any(query.startswith("微众银行 北青网") for query in search_calls)


def test_unlisted_primary_results_do_not_block_expanded_media_search(tmp_path):
    today = date(2026, 7, 16)
    expanded_url = "https://www.cnr.cn/expanded"
    unlisted_results = [
        _search_result(f"https://unlisted-{index}.example/article", f"无关候选{index}")
        for index in range(30)
    ]
    calls: list[tuple[str, object]] = []

    def search(query, max_results=5):
        calls.append(("search", query))
        if query.startswith("微众银行 新闻 报道"):
            return unlisted_results
        if query.startswith("微众银行 央广网"):
            return [_search_result(expanded_url, "微众银行科技创新取得新成果")]
        return []

    def web_reader(url):
        calls.append(("web_reader", url))
        if url == expanded_url:
            return _web_page(
                title="微众银行科技创新取得新成果",
                body="微众银行科技创新取得新成果，数字金融服务能力继续增强。" * 15,
                publish_date="2026-07-12",
                site="cnr.cn",
                canonical_url=expanded_url,
            )
        raise AssertionError("白名单外链接不应进入网页读取")

    gateway = ToolGateway(
        allowed_tools=("search", "web_reader", "llm_writer"),
        tools={
            "search": search,
            "web_reader": web_reader,
            "llm_writer": lambda payload: ArticleAssessment(
                decision="full_text",
                is_positive_achievement=True,
                subject_strength="primary",
                reason="全文聚焦微众银行正面成果。",
                achievement_types=["科技创新成果"],
            ),
        },
    )

    result = run({"output_dir": str(tmp_path / "output"), "today": today}, gateway)

    assert [article.original_url for article in result.articles] == [expanded_url]
    assert not any(
        kind == "web_reader" and str(value).startswith("https://unlisted-")
        for kind, value in calls
    )


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
        "微众银行 新闻 报道": primary_results,
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
    assert "均未达到微众银行正面新闻和成果的报送标准" in result.message


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
