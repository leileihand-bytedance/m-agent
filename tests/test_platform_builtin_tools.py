from pathlib import Path
import json
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.platform.builtin_tools import (
    LLMWriter,
    _fetch_url,
    policy_materials,
    policy_research,
    policy_search,
    search_web,
    read_web_page,
    _validate_public_web_url,
)
from app.policy_knowledge.store import PolicyKnowledgeStore


def test_read_web_page_extracts_title_and_paragraphs():
    html = """
    <html>
      <head><title>测试标题</title></head>
      <body>
        <nav>导航内容</nav>
        <p>第一段正文。</p>
        <p>第二段正文。</p>
        <script>ignore()</script>
      </body>
    </html>
    """

    result = read_web_page("https://example.com/news", fetcher=lambda url: html)

    assert result["url"] == "https://example.com/news"
    assert result["title"] == "测试标题"
    assert result["text"] == "第一段正文。\n第二段正文。"


def test_read_web_page_prefers_open_graph_title_over_site_decorated_title():
    html = """
    <html>
      <head>
        <title>微众科技助力数字经济发展 - 专题栏目 - 点新闻</title>
        <meta property="og:title" content="微众科技助力数字经济发展">
      </head>
      <body><article><p>微众科技持续推进国际化技术输出。</p></article></body>
    </html>
    """

    result = read_web_page("https://example.com/clean-title", fetcher=lambda _: html)

    assert result["title"] == "微众科技助力数字经济发展"


def test_read_web_page_rejects_non_http_urls():
    try:
        read_web_page("file:///home/example/.env", fetcher=lambda url: "secret")
    except ValueError as exc:
        assert "Only http and https URLs are allowed" in str(exc)
    else:
        raise AssertionError("ValueError was not raised")


def test_read_web_page_rejects_local_and_private_network_urls():
    blocked_urls = (
        "http://localhost/admin",
        "http://127.0.0.1:8000/",
        "http://10.0.0.8/private",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::1]/",
    )

    for url in blocked_urls:
        try:
            read_web_page(url, fetcher=lambda value: "secret")
        except ValueError as exc:
            assert "公网" in str(exc)
        else:
            raise AssertionError(f"private URL was not rejected: {url}")


def test_public_web_url_rejects_hostname_resolving_to_private_ip():
    resolver = lambda host, port, type=0: [(2, 1, 6, "", ("192.168.1.10", port))]

    try:
        _validate_public_web_url("https://news.example.com/article", resolver=resolver)
    except ValueError as exc:
        assert "公网" in str(exc)
    else:
        raise AssertionError("private DNS result was not rejected")


def test_fetch_url_validates_redirect_before_requesting_target():
    calls = []

    class Response:
        status_code = 302
        headers = {"location": "http://127.0.0.1/private"}

        def close(self):
            return None

    def requester(url, **kwargs):
        calls.append((url, kwargs))
        return Response()

    resolver = lambda host, port, type=0: [(2, 1, 6, "", ("93.184.216.34", port))]

    try:
        _fetch_url("https://public.example/start", requester=requester, resolver=resolver)
    except ValueError as exc:
        assert "公网" in str(exc)
    else:
        raise AssertionError("private redirect target was not rejected")

    assert [url for url, _ in calls] == ["https://public.example/start"]
    assert calls[0][1]["allow_redirects"] is False


def test_fetch_url_limits_streamed_response_body():
    class Response:
        status_code = 200
        headers = {"content-type": "text/html; charset=utf-8"}
        encoding = "utf-8"

        def iter_content(self, chunk_size=None):
            yield b"123"
            yield b"456"

        def close(self):
            return None

    resolver = lambda host, port, type=0: [(2, 1, 6, "", ("93.184.216.34", port))]

    try:
        _fetch_url(
            "https://public.example/page",
            requester=lambda url, **kwargs: Response(),
            resolver=resolver,
            max_bytes=5,
        )
    except ValueError as exc:
        assert "过大" in str(exc)
    else:
        raise AssertionError("oversized response was not rejected")


def test_read_web_page_extracts_baijiahao_article_body_not_author_name():
    html = """
    <html>
      <head><title>助力“无障碍”，普惠“零距离”微众银行持续推动无障碍金融服务</title></head>
      <body>
        <div id="header">
          <p class="_2gGWi">新京报</p>
        </div>
        <div data-testid="article">
          <div class="dpu8C"><span class="bjh-p">“奔向美好生活的路上，一个都不能少。”无障碍建设是国家和社会文明的重要标志。</span></div>
          <div class="dpu8C"><span class="bjh-p">微众银行持续服务视障、听障、老年人群等特殊群体金融需求。</span></div>
          <div class="dpu8C"><span class="bjh-p">截至2023年末，“微粒贷”已累计为听障客户提供服务超20万人次，发放贷款近9亿元。</span></div>
        </div>
      </body>
    </html>
    """

    result = read_web_page("https://baijiahao.baidu.com/s?id=123", fetcher=lambda url: html)

    assert result["title"] == "助力“无障碍”，普惠“零距离”微众银行持续推动无障碍金融服务"
    assert "新京报" not in result["text"]
    assert "无障碍建设是国家和社会文明的重要标志" in result["text"]
    assert "累计为听障客户提供服务超20万人次" in result["text"]


def test_search_web_calls_configured_search_api_and_normalizes_results():
    calls = []

    def requester(url, payload, headers, timeout):
        calls.append((url, payload, headers, timeout))
        return json.dumps(
            {
                "organic": [
                    {
                        "link": "https://example.com/a",
                        "title": "标题 A",
                        "snippet": "摘要 A",
                    },
                    {
                        "link": "https://www.gov.cn/policy",
                        "title": "官方标题",
                        "snippet": "官方摘要",
                    },
                ]
            },
            ensure_ascii=False,
        )

    results = search_web(
        "小微企业融资",
        api_key="test-key",
        base_url="https://api.minimaxi.com/anthropic",
        requester=requester,
        max_results=5,
    )

    assert calls[0][0] == "https://api.minimaxi.com/v1/coding_plan/search"
    assert calls[0][1] == {"q": "小微企业融资"}
    assert calls[0][2]["Authorization"] == "Bearer test-key"
    assert results[0]["url"] == "https://www.gov.cn/policy"
    assert results[0]["source"] == "official"
    assert results[1]["url"] == "https://example.com/a"


def test_search_web_calls_deepseek_native_web_search_and_normalizes_results():
    calls = []

    def requester(url, payload, headers, timeout):
        calls.append((url, payload, headers, timeout))
        return json.dumps(
            {
                "content": [
                    {
                        "type": "server_tool_use",
                        "id": "srvtoolu_001",
                        "name": "web_search",
                        "input": {"query": "微众银行 2026年7月"},
                    },
                    {
                        "type": "web_search_tool_result",
                        "tool_use_id": "srvtoolu_001",
                        "content": [
                            {
                                "type": "web_search_result",
                                "url": "https://example.com/webank",
                                "title": "微众银行相关新闻",
                                "page_age": "2026-07-15",
                                "encrypted_content": "encrypted",
                            },
                            {
                                "type": "web_search_result",
                                "url": "https://www.gov.cn/policy",
                                "title": "官方报道",
                                "page_age": "2026-07-14",
                                "encrypted_content": "encrypted",
                            },
                            {
                                "type": "web_search_result",
                                "url": "javascript:alert(1)",
                                "title": "不安全链接",
                            },
                        ],
                    },
                ]
            },
            ensure_ascii=False,
        )

    results = search_web(
        "微众银行 2026年7月",
        api_key="deepseek-key",
        base_url="https://api.deepseek.com/anthropic",
        model_name="deepseek-v4-flash",
        requester=requester,
        max_results=5,
    )

    assert calls[0][0] == "https://api.deepseek.com/anthropic/v1/messages"
    assert calls[0][1]["model"] == "deepseek-v4-flash"
    assert calls[0][1]["messages"] == [
        {
            "role": "user",
            "content": "请使用 web_search 工具联网搜索以下查询，并返回相关搜索结果：微众银行 2026年7月",
        }
    ]
    assert calls[0][1]["tools"] == [
        {"type": "web_search_20250305", "name": "web_search", "max_uses": 1}
    ]
    assert calls[0][2]["x-api-key"] == "deepseek-key"
    assert calls[0][2]["anthropic-version"] == "2023-06-01"
    assert results == [
        {
            "url": "https://www.gov.cn/policy",
            "title": "官方报道",
            "snippet": "",
            "source": "official",
        },
        {
            "url": "https://example.com/webank",
            "title": "微众银行相关新闻",
            "snippet": "",
            "source": "media",
        },
    ]


def test_search_web_rejects_unsupported_search_provider():
    try:
        search_web(
            "微众银行",
            api_key="test-key",
            base_url="https://search.example.com/anthropic",
            model_name="example-model",
            requester=lambda url, payload, headers, timeout: "{}",
        )
    except RuntimeError as exc:
        assert "不支持联网搜索" in str(exc)
    else:
        raise AssertionError("RuntimeError was not raised")


def test_search_web_requires_http_api_host():
    try:
        search_web(
            "小微企业融资",
            api_key="test-key",
            base_url="file:///home/example/.env",
            requester=lambda url, payload, headers, timeout: "{}",
        )
    except ValueError as exc:
        assert "Only http and https search API URLs are allowed" in str(exc)
    else:
        raise AssertionError("ValueError was not raised")


def test_search_web_reports_missing_search_config():
    try:
        search_web(
            "小微企业融资",
            api_key="",
            base_url="https://api.minimaxi.com/anthropic",
            requester=lambda url, payload, headers, timeout: "{}",
        )
    except RuntimeError as exc:
        assert "搜索 API 配置" in str(exc)
    else:
        raise AssertionError("RuntimeError was not raised")


def test_read_web_page_extracts_publish_date_and_metadata():
    html = """
    <html>
      <head>
        <title>微众银行推出新服务</title>
        <link rel="canonical" href="https://news.example.com/article/123">
        <meta property="article:published_time" content="2026-07-15T08:30:00+08:00">
      </head>
      <body>
        <article>
          <p>2026年7月15日，微众银行宣布推出全新服务。</p>
        </article>
      </body>
    </html>
    """

    result = read_web_page("https://www.example.com/article/123", fetcher=lambda url: html)

    assert result["url"] == "https://www.example.com/article/123"
    assert result["canonical_url"] == "https://news.example.com/article/123"
    assert result["site"] == "news.example.com"
    assert result["publish_date"] == "2026-07-15"
    assert "article:published_time" in result["date_extracted_from"]


def test_read_web_page_extracts_citation_publication_date_used_by_research_sites():
    html = """
    <html>
      <head>
        <title>Competition in retail digital payments</title>
        <meta name="citation_publication_date" content="2026-07-13">
        <meta name="DC.date" content="2026-07-13">
      </head>
      <body>
        <main><p>This bulletin studies competition in retail digital payments.</p></main>
      </body>
    </html>
    """

    result = read_web_page("https://www.bis.org/publ/bisbull127.htm", fetcher=lambda _: html)

    assert result["publish_date"] == "2026-07-13"
    assert result["date_extracted_from"] == "meta:citation_publication_date"


def test_read_web_page_uses_verified_people_daily_issue_path_over_stale_meta():
    html = """
    <html>
      <head>
        <title>科技创新助推数字化金融普惠发展</title>
        <meta name="publishdate" content="2013-07-17">
      </head>
      <body>
        <a href="../../../layout/202607/11/node_07.html">07版</a>
        <url>http://paper.people.com.cn/rmrb/pc/content/202607/11/content_30168032.html</url>
        <p>微众银行通过科技创新推动数字普惠金融发展。</p>
      </body>
    </html>
    """
    url = "https://paper.people.com.cn/rmrb/pc/content/202607/11/content_30168032.html"

    result = read_web_page(url, fetcher=lambda _: html)

    assert result["publish_date"] == "2026-07-11"
    assert result["date_extracted_from"] == "people-daily:verified-issue-path"


def test_read_web_page_falls_back_to_time_element_for_date():
    html = """
    <html>
      <head><title>另一篇报道</title></head>
      <body>
        <article>
          <time datetime="2026-07-14">昨日</time>
          <p>微众银行相关内容。</p>
        </article>
      </body>
    </html>
    """

    result = read_web_page("https://www.example.com/b", fetcher=lambda url: html)

    assert result["publish_date"] == "2026-07-14"
    assert result["date_extracted_from"] == "time:datetime"


def test_read_web_page_extracts_visible_dotted_date_from_time_element():
    html = """
    <html>
      <head><title>微众科技助力数字经济协同发展</title></head>
      <body>
        <article>
          <time class="publish-date">2026.06.24 16:27</time>
          <p>微众科技依托金融科技能力服务沿线国家数字化转型。</p>
        </article>
      </body>
    </html>
    """

    result = read_web_page("https://www.example.com/dotted-date", fetcher=lambda _: html)

    assert result["publish_date"] == "2026-06-24"
    assert result["date_extracted_from"] == "time:text"


def test_read_web_page_extracts_json_ld_date_before_script_cleanup():
    html = """
    <html>
      <head>
        <title>微众科技成果报道</title>
        <script type="application/ld+json">
          {"@type": "NewsArticle", "datePublished": "2026-06-24T16:27:39+08:00"}
        </script>
      </head>
      <body>
        <article><p>微众科技持续推进数字金融技术输出。</p></article>
      </body>
    </html>
    """

    result = read_web_page("https://www.example.com/json-ld-date", fetcher=lambda _: html)

    assert result["publish_date"] == "2026-06-24"
    assert result["date_extracted_from"] == "json-ld:datePublished"


def test_read_web_page_ignores_header_clock_when_article_has_publish_date():
    html = """
    <html>
      <head><title>微众银行成果报道</title></head>
      <body>
        <header><time>2026.07.19 10:30</time></header>
        <article><p>本报2026年6月24日讯，微众银行发布最新成果。</p></article>
      </body>
    </html>
    """

    result = read_web_page("https://www.example.com/header-clock", fetcher=lambda _: html)

    assert result["publish_date"] == "2026-06-24"
    assert "text:" in result["date_extracted_from"]


def test_read_web_page_extracts_date_from_text_when_no_meta():
    html = """
    <html>
      <head><title>第三篇报道</title></head>
      <body>
        <article>
          <p>本报2026年7月13日讯，微众银行……</p>
        </article>
      </body>
    </html>
    """

    result = read_web_page("https://www.example.com/c", fetcher=lambda url: html)

    assert result["publish_date"] == "2026-07-13"
    assert "text:" in result["date_extracted_from"]


def test_read_web_page_returns_empty_date_when_not_found():
    html = """
    <html>
      <head><title>无日期报道</title></head>
      <body>
        <article>
          <p>微众银行相关内容，但没有日期。</p>
        </article>
      </body>
    </html>
    """

    result = read_web_page("https://www.example.com/d", fetcher=lambda url: html)

    assert result["publish_date"] == ""
    assert result["date_extracted_from"] == ""
    assert result["canonical_url"] == "https://www.example.com/d"
    assert result["site"] == "example.com"


def test_policy_search_reads_local_policy_knowledge_base(tmp_path):
    db_path = tmp_path / "policies.sqlite3"
    store = PolicyKnowledgeStore(db_path)
    store.upsert_documents(
        [
            {
                "source": "nfra",
                "category": "policy_interpretation",
                "item_id": "917",
                "doc_id": "1001",
                "title": "小微企业金融服务政策解读",
                "publish_date": "2026-05-19 18:35:07",
                "url": "https://www.nfra.gov.cn/example",
                "text": "监管部门要求提升小微企业金融服务质效。",
                "original_links": [],
                "metadata": {},
            }
        ]
    )

    results = policy_search("小微企业金融服务", db_path=db_path, limit=5)

    assert results[0]["title"] == "小微企业金融服务政策解读"
    assert results[0]["source"] == "nfra"
    assert "监管部门" in results[0]["snippet"]


def test_policy_search_can_filter_policy_original(tmp_path):
    db_path = tmp_path / "policies.sqlite3"
    store = PolicyKnowledgeStore(db_path)
    store.upsert_documents(
        [
            {
                "source": "nfra",
                "category": "policy_interpretation",
                "item_id": "917",
                "doc_id": "1001",
                "title": "小微企业金融服务政策解读",
                "publish_date": "2026-05-19",
                "url": "https://www.nfra.gov.cn/interpretation",
                "text": "小微企业金融服务政策解读。",
                "original_links": [],
                "metadata": {},
            },
            {
                "source": "nfra",
                "category": "policy_original",
                "item_id": "",
                "doc_id": "1002",
                "title": "关于提升小微企业金融服务质效的通知",
                "publish_date": "2026-05-18",
                "url": "https://www.nfra.gov.cn/original",
                "text": "提升小微企业金融服务质效，优化融资供给。",
                "original_links": [],
                "metadata": {},
            },
        ]
    )

    results = policy_search("小微企业金融服务", db_path=db_path, limit=5, category="policy_original")

    assert len(results) == 1
    assert results[0]["category"] == "policy_original"
    assert results[0]["title"] == "关于提升小微企业金融服务质效的通知"


def test_policy_search_uses_govcn_summary_when_stored_text_is_navigation(tmp_path):
    db_path = tmp_path / "policies.sqlite3"
    store = PolicyKnowledgeStore(db_path)
    store.upsert_documents(
        [
            {
                "source": "govcn",
                "category": "policy_original",
                "item_id": "",
                "doc_id": "gov-001",
                "title": "国务院关于促进服务消费高质量发展的意见",
                "publish_date": "2026-01-29",
                "url": "https://www.gov.cn/zhengce/zhengceku/example.htm",
                "text": "首页 | 简 | 繁 | EN | 登录 个人中心 退出 | 邮箱 | 无障碍 EN https://www.gov.cn/",
                "original_links": [],
                "metadata": {
                    "summary": "国务院部署促进服务消费高质量发展，扩大消费供给，培育消费新增长点。"
                },
            }
        ]
    )

    results = policy_search("促进服务消费", db_path=db_path, limit=5, category="policy_original")

    assert len(results) == 1
    assert "扩大消费供给" in results[0]["snippet"]
    assert "首页 | 简" not in results[0]["snippet"]


def test_policy_search_does_not_let_generic_consumption_overrank_specific_policy(tmp_path):
    db_path = tmp_path / "policies.sqlite3"
    store = PolicyKnowledgeStore(db_path)
    store.upsert_documents(
        [
            {
                "source": "nfra",
                "category": "policy_original",
                "item_id": "",
                "doc_id": "nfra-consumer",
                "title": "金融消费者权益保护办法",
                "publish_date": "2026-04-24",
                "url": "https://www.nfra.gov.cn/consumer",
                "text": "消费者 消费者 消费者 消费者 消费者 消费者。",
                "original_links": [],
                "metadata": {},
            },
            {
                "source": "govcn",
                "category": "policy_original",
                "item_id": "",
                "doc_id": "gov-service-consumption",
                "title": "国务院办公厅关于印发加快培育服务消费新增长点工作方案的通知",
                "publish_date": "2026-01-29",
                "url": "https://www.gov.cn/service-consumption",
                "text": "加快培育服务消费新增长点，释放服务消费潜力。",
                "original_links": [],
                "metadata": {},
            },
        ]
    )

    results = policy_search("服务消费 新增长点", db_path=db_path, limit=2, category="policy_original")

    assert results[0]["source"] == "govcn"
    assert "服务消费" in results[0]["title"]


def test_policy_materials_builds_small_relevant_policy_pack(tmp_path):
    db_path = tmp_path / "policies.sqlite3"
    store = PolicyKnowledgeStore(db_path)
    store.upsert_documents(
        [
            {
                "source": "nfra",
                "category": "policy_original",
                "item_id": "",
                "doc_id": "small-micro",
                "title": "关于提升小微企业金融服务质效的通知",
                "publish_date": "2026-05-18",
                "url": "https://www.nfra.gov.cn/small-micro",
                "text": "提升小微企业金融服务质效，优化融资供给，支持普惠金融发展。",
                "original_links": [],
                "metadata": {},
            },
            {
                "source": "govcn",
                "category": "policy_original",
                "item_id": "",
                "doc_id": "service-consumption",
                "title": "国务院办公厅关于印发加快培育服务消费新增长点工作方案的通知",
                "publish_date": "2026-01-29",
                "url": "https://www.gov.cn/service-consumption",
                "text": "加快培育服务消费新增长点，释放服务消费潜力。",
                "original_links": [],
                "metadata": {},
            },
        ]
    )

    result = policy_materials(
        user_instruction="根据材料写直报",
        materials=[
            {
                "title": "微众银行优化小微企业融资服务",
                "text": "微众银行通过数字化方式提升小微企业融资服务效率，扩大普惠金融覆盖面。",
                "url": "https://example.com/news",
            }
        ],
        db_path=db_path,
        limit=2,
    )

    assert len(result) == 1
    assert result[0]["title"] == "关于提升小微企业金融服务质效的通知"
    assert result[0]["source"] == "policy_knowledge"
    assert result[0]["category"] == "policy_original"
    assert "相关性说明" in result[0]["text"]
    assert "小微企业" in result[0]["matched_terms"]


def test_policy_materials_returns_empty_when_no_policy_theme_is_detected(tmp_path):
    db_path = tmp_path / "policies.sqlite3"
    store = PolicyKnowledgeStore(db_path)
    store.upsert_documents(
        [
            {
                "source": "nfra",
                "category": "policy_original",
                "item_id": "",
                "doc_id": "small-micro",
                "title": "关于提升小微企业金融服务质效的通知",
                "publish_date": "2026-05-18",
                "url": "https://www.nfra.gov.cn/small-micro",
                "text": "提升小微企业金融服务质效。",
                "original_links": [],
                "metadata": {},
            }
        ]
    )

    result = policy_materials(
        user_instruction="根据这个公司活动写一段内部通知",
        materials=[{"title": "团队活动", "text": "部门组织员工交流活动。"}],
        db_path=db_path,
        limit=2,
    )

    assert result == []


def test_policy_research_returns_primary_policy_from_local_store(tmp_path):
    db_path = tmp_path / "policies.sqlite3"
    store = PolicyKnowledgeStore(db_path)
    store.upsert_documents(
        [
            {
                "source": "nfra",
                "category": "policy_original",
                "item_id": "",
                "doc_id": "small-micro",
                "title": "关于提升小微企业金融服务质效的通知",
                "publish_date": "2026-05-18",
                "url": "https://www.nfra.gov.cn/small-micro",
                "text": "提升小微企业金融服务质效，优化融资供给，支持普惠金融发展。",
                "original_links": [],
                "metadata": {},
            }
        ]
    )

    result = policy_research(
        user_instruction="请根据材料写简报",
        materials=[
            {
                "title": "微众银行优化小微企业融资服务",
                "text": "微众银行通过数字化方式提升小微企业融资服务效率，扩大普惠金融覆盖面。",
                "url": "https://example.com/news",
            }
        ],
        db_path=db_path,
        usage_profile="brief",
        limit=3,
    )

    assert result["should_attach_policy"] is True
    assert result["primary_policy"]["title"] == "关于提升小微企业金融服务质效的通知"


class _FakeMessages:
    def __init__(self):
        self.last_request = None

    def create(self, **kwargs):
        self.last_request = kwargs
        block = type(
            "TextBlock",
            (),
            {
                "text": "标题：微众银行提升小微企业金融服务可得性\n\n正文：微众银行围绕小微企业融资需求，持续完善数字化服务能力。"
            },
        )()
        return type("Response", (), {"content": [block]})()


class _FakeClient:
    def __init__(self):
        self.messages = _FakeMessages()


def test_llm_writer_builds_prompt_and_parses_title_body():
    client = _FakeClient()
    writer = LLMWriter(
        api_key="test-key",
        base_url="https://example.com/anthropic",
        model="test-model",
        skill_dir=Path("skills/direct_report"),
        client=client,
    )

    result = writer.write(
        {
            "task": "direct_report",
            "instruction": "请写直报",
            "materials": [
                {
                    "title": "网页标题",
                    "text": "网页正文",
                    "url": "https://example.com/news",
                }
            ],
        }
    )

    assert result["title"] == "微众银行提升小微企业金融服务可得性"
    assert "小微企业" in result["body"]
    assert client.messages.last_request["model"] == "test-model"
    assert "直报写作 Skill" in client.messages.last_request["messages"][0]["content"]
    assert "网页正文" in client.messages.last_request["messages"][0]["content"]


def test_llm_writer_does_not_require_api_key_until_write():
    writer = LLMWriter(
        api_key="",
        base_url="https://example.com/anthropic",
        model="test-model",
        skill_dir=Path("skills/direct_report"),
    )

    try:
        writer.write({"task": "direct_report", "instruction": "", "materials": []})
    except RuntimeError as exc:
        assert "ANTHROPIC_API_KEY" in str(exc)
    else:
        raise AssertionError("RuntimeError was not raised")


def test_llm_writer_includes_planning_note_in_prompt():
    client = _FakeClient()
    writer = LLMWriter(
        api_key="test-key",
        base_url="https://example.com/anthropic",
        model="test-model",
        skill_dir=Path("skills/direct_report"),
        client=client,
    )

    writer.write(
        {
            "task": "direct_report",
            "instruction": "请写直报",
            "planning_note": "文体：直报\n开头策略：政策背景型",
            "materials": [{"title": "网页标题", "text": "网页正文", "url": "https://example.com/news"}],
        }
    )

    prompt = client.messages.last_request["messages"][0]["content"]
    assert "## 写作规划" in prompt
    assert "开头策略：政策背景型" in prompt
