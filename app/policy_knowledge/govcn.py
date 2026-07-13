from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from html import unescape
from urllib.parse import urlencode, urlparse


BASE_URL = "https://sousuo.www.gov.cn"
SEARCH_ENDPOINT = f"{BASE_URL}/search-gov/data"

DEFAULT_TOPICS: dict[str, list[str]] = {
    "macro_economy": ["宏观经济", "扩大内需", "稳增长", "优化营商环境"],
    "consumption": ["促进消费", "扩大消费", "服务消费", "消费品以旧换新"],
    "real_economy": ["实体经济", "民营经济", "中小企业", "制造业"],
    "strategic_emerging": ["战略性新兴产业", "新兴产业", "科技创新", "新质生产力"],
    "future_industries": ["未来产业", "人工智能", "低空经济", "量子科技"],
}


@dataclass(frozen=True)
class GovcnSearchResult:
    topic: str
    keyword: str
    row: dict[str, object]


class GovcnClient:
    def __init__(
        self,
        *,
        json_requester: Callable[[str], dict[str, object]] | None = None,
        html_requester: Callable[[str], str] | None = None,
    ):
        self._json_requester = json_requester or _default_json_requester
        self._html_requester = html_requester or _default_html_requester

    def search_documents(self, *, query: str, page_index: int, page_size: int = 10) -> list[dict[str, object]]:
        params = {
            "t": "zhengcelibrary_gw",
            "q": query,
            "p": page_index,
            "n": page_size,
            "timetype": "timeqb",
            "sort": "score",
            "sortType": 1,
            "searchfield": "quanwen",
        }
        payload = self._json_requester(f"{SEARCH_ENDPOINT}?{urlencode(params)}")
        if str(payload.get("code")) != "200":
            raise RuntimeError(f"国务院政策文件库接口返回异常：{payload.get('message') or payload.get('code')}")
        search_vo = payload.get("searchVO") if isinstance(payload.get("searchVO"), dict) else {}
        rows = search_vo.get("listVO") if isinstance(search_vo, dict) else []
        return [row for row in rows if isinstance(row, dict)]

    def fetch_page(self, url: str) -> str:
        return self._html_requester(url)


def fetch_govcn_policy_documents(
    *,
    client: GovcnClient | None = None,
    topics: Mapping[str, list[str]] | None = None,
    max_pages: int = 1,
    page_size: int = 10,
) -> list[dict[str, object]]:
    client = client or GovcnClient()
    selected_topics = topics or DEFAULT_TOPICS
    documents: list[dict[str, object]] = []
    seen_urls: set[str] = set()
    seen_doc_ids: set[str] = set()

    for topic, keywords in selected_topics.items():
        for keyword in keywords:
            for page_index in range(1, max_pages + 1):
                rows = client.search_documents(query=keyword, page_index=page_index, page_size=page_size)
                if not rows:
                    break
                for row in rows:
                    url = str(row.get("url") or "").strip()
                    if not _is_govcn_policy_url(url) or url in seen_urls:
                        continue
                    doc_id = _document_id(row, url)
                    if doc_id in seen_doc_ids:
                        continue
                    html = client.fetch_page(url)
                    document = _normalize_document(
                        GovcnSearchResult(topic=topic, keyword=keyword, row=row),
                        doc_id=doc_id,
                        url=url,
                        html=html,
                    )
                    if not str(document["text"]).strip():
                        continue
                    documents.append(document)
                    seen_urls.add(url)
                    seen_doc_ids.add(doc_id)

    return documents


def clean_policy_page_text(html: str) -> str:
    try:
        from bs4 import BeautifulSoup
    except ImportError as exc:
        raise RuntimeError("缺少 beautifulsoup4，无法清洗国务院政策网页正文。") from exc

    soup = BeautifulSoup(unescape(html), "html.parser")
    for tag in soup.find_all(["script", "style", "meta", "xml", "nav", "header", "footer"]):
        tag.decompose()

    parts: list[str] = []
    for selector in ("#UCAP-CONTENT", ".trs_editor_view", ".TRS_Editor", "article", ".article", ".pages_content"):
        containers = soup.select(selector)
        for container in containers:
            paragraphs = [p.get_text(" ", strip=True) for p in container.find_all("p")]
            if paragraphs:
                parts = paragraphs
                break
            text = container.get_text("\n", strip=True)
            if text:
                parts = [text]
                break
        if parts:
            break

    if not parts:
        body = soup.find("body") or soup
        parts = [p.get_text(" ", strip=True) for p in body.find_all("p")]
        if not parts:
            parts = [body.get_text("\n", strip=True)]

    return "\n".join(_compact_spaces(part) for part in parts if _compact_spaces(part))


def _normalize_document(
    result: GovcnSearchResult,
    *,
    doc_id: str,
    url: str,
    html: str,
) -> dict[str, object]:
    row = result.row
    summary = _clean_inline_html(str(row.get("summary") or ""))
    text = clean_policy_page_text(html)
    if _looks_like_navigation_stub(text) and summary:
        text = summary
    return {
        "source": "govcn",
        "category": "policy_original",
        "item_id": "",
        "doc_id": doc_id,
        "title": _clean_inline_html(str(row.get("title") or "")),
        "publish_date": _normalize_publish_date(str(row.get("pubtimeStr") or "")),
        "url": url,
        "text": text,
        "original_links": [],
        "metadata": {
            "source_name": "国务院政策文件库",
            "topic": result.topic,
            "keyword": result.keyword,
            "govcn_id": str(row.get("id") or ""),
            "puborg": _clean_inline_html(str(row.get("puborg") or "")),
            "pcode": _clean_inline_html(str(row.get("pcode") or "")),
            "childtype": _clean_inline_html(str(row.get("childtype") or "")),
            "summary": summary,
        },
    }


def _default_json_requester(url: str) -> dict[str, object]:
    try:
        from curl_cffi import requests as curl_requests
    except ImportError as exc:
        raise RuntimeError("缺少 curl-cffi，无法抓取国务院政策文件库。") from exc

    response = curl_requests.get(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json,text/plain,*/*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Referer": "https://sousuo.www.gov.cn/zcwjk/policyDocumentLibrary",
        },
        impersonate="chrome110",
        timeout=45,
    )
    if response.status_code != 200:
        raise RuntimeError(f"国务院政策文件库请求失败，HTTP {response.status_code}")
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError("国务院政策文件库返回了无效 JSON")
    return payload


def _default_html_requester(url: str) -> str:
    try:
        from curl_cffi import requests as curl_requests
    except ImportError as exc:
        raise RuntimeError("缺少 curl-cffi，无法抓取国务院政策网页。") from exc

    response = curl_requests.get(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        },
        impersonate="chrome110",
        timeout=45,
    )
    if response.status_code != 200:
        raise RuntimeError(f"国务院政策网页请求失败，HTTP {response.status_code}")
    return response.text


def _clean_inline_html(value: str) -> str:
    if not value:
        return ""
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        text = re.sub(r"<[^>]+>", "", unescape(value))
        return _compact_spaces(text)

    soup = BeautifulSoup(unescape(value), "html.parser")
    return _compact_spaces(soup.get_text("", strip=True))


def _document_id(row: dict[str, object], url: str) -> str:
    row_id = str(row.get("id") or "").strip()
    if row_id:
        return row_id
    match = re.search(r"content_(\d+)", url)
    if match:
        return match.group(1)
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]


def _is_govcn_policy_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return False
    if parsed.netloc not in {"www.gov.cn", "sousuo.www.gov.cn"}:
        return False
    return "/zhengce/" in parsed.path


def _normalize_publish_date(value: str) -> str:
    value = value.strip()
    if not value:
        return ""
    for fmt in ("%Y.%m.%d", "%Y-%m-%d", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(value, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return value


def _looks_like_navigation_stub(text: str) -> bool:
    compact = _compact_spaces(text)
    if not compact:
        return True
    if compact.startswith("首页 | 简 | 繁 | EN") and len(compact) < 200:
        return True
    return False


def _compact_spaces(value: str) -> str:
    return " ".join(value.replace("\u3000", " ").split())
