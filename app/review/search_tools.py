"""搜索工具模块.

支持网页搜索和页面抓取。
其中主动搜索接口目前仅 MiniMax 通道可用；
切到 DeepSeek 等其他兼容通道时，会保留 LLM 能力并跳过该搜索接口。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from typing import Any

from .model_config import build_anthropic_client, resolve_review_llm_config
from .core.model_runtime import create_model_message

# 搜索数据源配置
SEARCH_SOURCES: dict[str, dict] = {
    "PBOC": {
        "keywords": ["中国人民银行", "央行", "人民银行"],
        "preferred_domains": ["pbc.gov.cn", "pboc.gov.cn"],
        "fallback_domains": ["xinhuanet.com", "people.com.cn"],
    },
    "CBIRC": {
        "keywords": ["金融监管总局", "银保监会", "cbirc"],
        "preferred_domains": ["cbirc.gov.cn"],
        "fallback_domains": ["xinhuanet.com", "people.com.cn"],
    },
    "CSRC": {
        "keywords": ["证监会", "中国证券监督管理委员会", "csrc"],
        "preferred_domains": ["csrc.gov.cn"],
        "fallback_domains": ["xinhuanet.com", "people.com.cn"],
    },
    "SAFE": {
        "keywords": ["外汇管理局", "外汇局", "safe.gov.cn"],
        "preferred_domains": ["safe.gov.cn"],
        "fallback_domains": ["xinhuanet.com", "people.com.cn"],
    },
    "STATE_COUNCIL": {
        "keywords": ["国务院", "国务院常务会议", "国常会", "国务院办公厅"],
        "preferred_domains": ["gov.cn", "www.gov.cn"],
        "fallback_domains": ["xinhuanet.com", "people.com.cn"],
    },
    "PARTY_LEADERS": {
        "keywords": ["习近平", "李强", "丁薛祥", "张国清", "何立峰"],
        "preferred_domains": ["gov.cn", "xinhuanet.com"],
        "fallback_domains": ["people.com.cn", "cpc.people.com.cn"],
    },
}


@dataclass
class SearchResult:
    """单条搜索结果。"""
    url: str
    title: str
    snippet: str    # 搜索结果摘要
    source: Literal["official", "media"]  # 来源类型


def get_client() -> tuple[Any, str]:
    """获取审核模块使用的 LLM 客户端。"""
    return build_anthropic_client()


def search_web(
    query: str,
    time_baseline: datetime | None = None,
    max_results: int = 5,
) -> list[SearchResult]:
    """搜索网页。

    通过供应商搜索 API 返回结构化结果。

    Args:
        query: 搜索关键词
        time_baseline: 时间基准（暂未使用，保留接口兼容）
        max_results: 最大返回结果数

    Returns:
        搜索结果列表（按官方 > 媒体排序）
    """
    config = resolve_review_llm_config()
    if not config.search_api_base_url:
        print("  ⚠️ 当前审核模型通道不支持主动搜索 API，已跳过网页搜索")
        return []

    import urllib.request
    import json as json_lib

    url = f"{config.search_api_base_url}/v1/coding_plan/search"
    headers = {
        "Authorization": f"Bearer {config.api_key}",
        "Content-Type": "application/json",
        "MM-API-Source": "Minimax-MCP",
    }
    payload = json_lib.dumps({"q": query}).encode("utf-8")

    req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
    results: list[SearchResult] = []

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json_lib.loads(resp.read().decode("utf-8"))
            for item in data.get("organic", [])[:max_results]:
                link = item.get("link", "")
                source: Literal["official", "media"] = "media"
                for domain in ["gov.cn", "pbc.gov.cn", "cbirc.gov.cn", "csrc.gov.cn", "safe.gov.cn"]:
                    if domain in link:
                        source = "official"
                        break
                results.append(SearchResult(
                    url=link,
                    title=item.get("title", ""),
                    snippet=item.get("snippet", ""),
                    source=source,
                ))
    except Exception as e:
        print(f"  ⚠️ 搜索 API 调用失败: {e}")

    # 按官方 > 媒体排序
    results.sort(key=lambda r: 0 if r.source == "official" else 1)
    return results[:max_results]


def fetch_page(url: str) -> str:
    """抓取网页正文。

    优先用 curl_cffi + BeautifulSoup 提取，失败则降级用 MiniMax LLM。

    Args:
        url: 页面 URL

    Returns:
        网页正文文本（提取后的主要内容）
    """
    text = _fetch_page_native(url)
    if text and len(text) > 100:
        return text

    # 降级：用 MiniMax LLM 提取
    return _fetch_page_llm(url)


def _fetch_page_native(url: str) -> str:
    """用 curl_cffi + BeautifulSoup 抓取网页正文。"""
    try:
        from curl_cffi import requests as curl_requests
        from bs4 import BeautifulSoup
    except ImportError:
        return ""

    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
    }
    try:
        resp = curl_requests.get(url, headers=headers, impersonate="chrome110", timeout=15)
        if resp.status_code != 200:
            return ""
        soup = BeautifulSoup(resp.text, "lxml")
        # 移除 script/style/nav/footer
        for tag in soup.find_all(["script", "style", "nav", "footer", "header"]):
            tag.decompose()
        # 提取所有段落文本
        ps = soup.find_all("p")
        if ps:
            text = "\n".join(p.get_text() for p in ps if p.get_text().strip())
            return text
        # 尝试找 article 或 main
        main = soup.find("article") or soup.find("main") or soup.find("div", class_=lambda x: x and "content" in x.lower() if x else False)
        if main:
            return main.get_text(separator="\n", strip=True)
        return ""
    except Exception:
        return ""


def _fetch_page_llm(url: str) -> str:
    """降级：用 MiniMax LLM 提取页面内容。"""
    client, model = get_client()
    try:
        response = create_model_message(
            client,
            metrics=None,
            stage="web_content_extraction",
            model=model,
            max_tokens=8192,
            messages=[{
                "role": "user",
                "content": f"""你是一个网页内容提取助手。请访问以下URL，提取页面的正文内容（不要导航栏、页脚、广告等干扰内容）。

URL：{url}

请直接返回页面的主要正文内容，越完整越好。"""
            }],
            timeout=60.0,
        )
        for block in response.content:
            if hasattr(block, "text") and block.text:
                return block.text
    except Exception:
        pass
    return ""


def identify_content_source(text: str) -> str | None:
    """识别内容主体类型。

    Args:
        text: 段落文本（标题 + 前100字）

    Returns:
        来源类型 key（如 "PBOC", "STATE_COUNCIL"），或 None（无法识别）
    """
    for source_key, config in SEARCH_SOURCES.items():
        for keyword in config["keywords"]:
            if keyword in text:
                return source_key
    return None


def _parse_plain_text_results(text: str) -> list[SearchResult]:
    """降级解析：解析纯文本格式的搜索结果。"""
    import re
    results: list[SearchResult] = []

    # 简单按行解析 URL + 标题 + 摘要
    lines = text.strip().split("\n")
    i = 0
    while i < len(lines) and len(results) < 5:
        line = lines[i].strip()
        if line.startswith("http"):
            url = line
            title = lines[i+1].strip() if i+1 < len(lines) else ""
            snippet = lines[i+2].strip() if i+2 < len(lines) else ""
            source: Literal["official", "media"] = "media"
            for domain in ["gov.cn", "pbc.gov.cn", "cbirc.gov.cn", "csrc.gov.cn"]:
                if domain in url:
                    source = "official"
                    break
            results.append(SearchResult(url=url, title=title, snippet=snippet, source=source))
            i += 3
        else:
            i += 1

    return results
