from datetime import date
from pathlib import Path

import pytest

from app.platform.tools import ToolGateway
from skills.shenyinxie_news.schema import ShenyinxieNewsResult
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


def _make_gateway(*, search_results, web_pages):
    """构造 mock ToolGateway。"""
    calls: list[tuple[str, object]] = []

    def search(query, max_results=5):
        calls.append(("search", query))
        return search_results.get(query, [])

    def web_reader(url):
        calls.append(("web_reader", url))
        return web_pages.get(url, _web_page(title="未知", body=""))

    return (
        ToolGateway(
            allowed_tools=("search", "web_reader", "llm_writer"),
            tools={"search": search, "web_reader": web_reader, "llm_writer": lambda x: x},
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
