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
