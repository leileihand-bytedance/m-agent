import re
import json
from collections.abc import Callable
from contextlib import contextmanager
from datetime import date, datetime
import ipaddress
from pathlib import Path
import socket
from typing import Any
from urllib.parse import urljoin, urlparse
import urllib.request
import zipfile
import xml.etree.ElementTree as ET

from app.platform.documents import DocumentService


MAX_WEB_REDIRECTS = 5
MAX_WEB_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_WEB_INDEX_LINKS = 500
MAX_WEB_INDEX_RECORD_VALUE_CHARS = 1000


def read_web_page(
    url: str,
    fetcher: Callable[[str], str] | None = None,
) -> dict[str, object]:
    if fetcher:
        _validate_public_web_url(url, resolver=None)
        html = fetcher(url)
    else:
        html = _fetch_url(url)
    json_page = _extract_json_index(url, html)
    if json_page is not None:
        return json_page
    return _extract_page_text(url, html)


def _extract_json_index(url: str, body: str) -> dict[str, object] | None:
    """把公开 JSON 索引归一为文字和链接元数据，不跟随或读取其中链接。"""
    try:
        payload = json.loads(body)
    except (TypeError, ValueError):
        return None

    article_page = _extract_json_article(url, payload)
    if article_page is not None:
        return article_page

    records = _find_json_records(payload)
    normalized_records = [_normalize_json_record(record) for record in records]

    links: list[dict[str, str]] = []
    for record in normalized_records:
        raw_url = next(
            (
                str(record.get(key) or "").strip()
                for key in ("URL", "url", "href", "link", "docUrl")
                if str(record.get(key) or "").strip()
            ),
            "",
        )
        if not raw_url:
            continue
        resolved_url = urljoin(url, raw_url)
        if urlparse(resolved_url).scheme not in {"http", "https"}:
            continue
        title = next(
            (
                str(record.get(key) or "").strip()
                for key in ("TITLE", "title", "name", "docSubtitle", "docTitle")
                if str(record.get(key) or "").strip()
            ),
            "",
        )
        publish_date = next(
            (
                str(record.get(key) or "").strip()
                for key in (
                    "DOCRELPUBTIME",
                    "publish_date",
                    "published_at",
                    "date",
                    "publishDate",
                    "publishedTimeStr",
                )
                if str(record.get(key) or "").strip()
            ),
            "",
        )
        links.append(
            {
                "title": title,
                "url": resolved_url,
                "publish_date": publish_date,
            }
        )
        if len(links) >= MAX_WEB_INDEX_LINKS:
            break

    text = "\n".join(
        " ".join(value for value in (item["publish_date"], item["title"], item["url"]) if value)
        for item in links
    )[:4000]
    return {
        "url": url,
        "title": "",
        "text": text,
        "publish_date": "",
        "site": _extract_site(url),
        "canonical_url": url,
        "date_extracted_from": "",
        "links": links,
        "records": normalized_records,
    }


def _find_json_records(payload: object, *, depth: int = 0) -> list[dict[str, object]]:
    """查找常见公开列表接口中的记录数组，兼容 data.rows/data.results 等嵌套。"""
    if depth > 5:
        return []
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("items", "results", "rows", "data", "list"):
        if key not in payload:
            continue
        records = _find_json_records(payload[key], depth=depth + 1)
        if records:
            return records
    return []


def _normalize_json_record(record: dict[str, object]) -> dict[str, str]:
    """仅保留列表记录的标量字段，避免把大段嵌套内容透传给业务层。"""
    normalized: dict[str, str] = {}
    for key, value in record.items():
        if value is None or isinstance(value, (dict, list)):
            continue
        text = str(value).strip()
        if not text:
            continue
        normalized[str(key)] = text[:MAX_WEB_INDEX_RECORD_VALUE_CHARS]
    return normalized


def _extract_json_article(url: str, payload: object) -> dict[str, object] | None:
    """归一公开 JSON 原文接口，支持标题、发布日期和 HTML/纯文本正文。"""
    article = payload
    if isinstance(payload, dict) and isinstance(payload.get("data"), dict):
        article = payload["data"]
    if not isinstance(article, dict):
        return None

    raw_body = next(
        (
            str(article.get(key) or "").strip()
            for key in ("docClob", "contentHtml", "content", "body", "text")
            if str(article.get(key) or "").strip()
        ),
        "",
    )
    if not raw_body:
        return None
    title = next(
        (
            str(article.get(key) or "").strip()
            for key in ("docTitle", "docSubtitle", "title", "name")
            if str(article.get(key) or "").strip()
        ),
        "",
    )
    raw_publish_date = next(
        (
            str(article.get(key) or "").strip()
            for key in (
                "publishDate",
                "publishedTimeStr",
                "DOCRELPUBTIME",
                "publish_date",
                "published_at",
                "date",
            )
            if str(article.get(key) or "").strip()
        ),
        "",
    )
    publish_date = _parse_date(raw_publish_date)
    return {
        "url": url,
        "title": title,
        "text": _html_fragment_text(raw_body)[:4000],
        "publish_date": publish_date.isoformat() if publish_date else "",
        "site": _extract_site(url),
        "canonical_url": url,
        "date_extracted_from": "json:publishDate" if publish_date else "",
    }


def _html_fragment_text(value: str) -> str:
    try:
        from bs4 import BeautifulSoup
    except ImportError as exc:
        raise RuntimeError("缺少 beautifulsoup4，无法解析网页。") from exc
    soup = BeautifulSoup(value, "lxml")
    for tag in soup.find_all(["script", "style", "nav", "footer", "header"]):
        tag.decompose()
    paragraphs = _extract_article_paragraphs(soup)
    if paragraphs:
        return "\n".join(paragraphs)
    return soup.get_text("\n", strip=True)


def _validate_public_web_url(
    url: str,
    *,
    resolver: Callable[..., list[tuple[Any, ...]]] | None = socket.getaddrinfo,
) -> None:
    _resolve_public_web_target(url, resolver=resolver)


def _resolve_public_web_target(
    url: str,
    *,
    resolver: Callable[..., list[tuple[Any, ...]]] | None = socket.getaddrinfo,
) -> tuple[str, int, tuple[str, ...]]:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("Only http and https URLs are allowed")
    hostname = (parsed.hostname or "").strip().lower().rstrip(".")
    if not hostname:
        raise ValueError("网址缺少有效主机名")
    if hostname == "localhost" or hostname.endswith((".localhost", ".local", ".internal")):
        raise ValueError("只允许读取公网 http/https 地址")

    try:
        literal_ip = ipaddress.ip_address(hostname)
    except ValueError:
        literal_ip = None
    if literal_ip is not None:
        if not literal_ip.is_global:
            raise ValueError("只允许读取公网 http/https 地址")
        return hostname, parsed.port or (443 if parsed.scheme == "https" else 80), (str(literal_ip),)

    if resolver is None:
        return hostname, parsed.port or (443 if parsed.scheme == "https" else 80), ()
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        infos = resolver(hostname, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise ValueError("网址无法解析或访问") from exc
    addresses = tuple(sorted({str(info[4][0]) for info in infos if len(info) >= 5 and info[4]}))
    if not addresses:
        raise ValueError("网址无法解析或访问")
    try:
        if any(not ipaddress.ip_address(address).is_global for address in addresses):
            raise ValueError("只允许读取公网 http/https 地址")
    except ValueError as exc:
        if "公网" in str(exc):
            raise
        raise ValueError("网址无法解析或访问") from exc
    return hostname, port, addresses


def search_web(
    query: str,
    *,
    api_key: str,
    base_url: str,
    model_name: str = "",
    max_results: int = 5,
    requester: Callable[[str, dict[str, object], dict[str, str], int], str] | None = None,
) -> list[dict[str, str]]:
    if not api_key:
        raise RuntimeError("缺少搜索 API 配置，无法调用搜索工具。")

    provider = _search_provider(base_url)
    if provider == "deepseek":
        if not model_name:
            raise RuntimeError("缺少 DeepSeek 搜索模型配置，无法调用联网搜索。")
        search_url = _deepseek_messages_url(base_url)
        headers = {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
        payload: dict[str, object] = {
            "model": model_name,
            "max_tokens": 1024,
            "messages": [
                {
                    "role": "user",
                    "content": f"请使用 web_search 工具联网搜索以下查询，并返回相关搜索结果：{query}",
                }
            ],
            "tools": [
                {
                    "type": "web_search_20250305",
                    "name": "web_search",
                    "max_uses": 1,
                }
            ],
        }
        request = requester or _default_search_request
        raw_text = request(search_url, payload, headers, 30)
        return _extract_deepseek_search_results(raw_text, max_results=max_results)

    if provider != "minimax":
        raise RuntimeError("当前搜索 API 供应商不支持联网搜索。")

    api_host = _search_api_host(base_url)
    search_url = f"{api_host}/v1/coding_plan/search"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "MM-API-Source": "Minimax-MCP",
    }
    request = requester or _default_search_request
    raw_text = request(search_url, {"q": query}, headers, 30)
    payload = json.loads(raw_text or "{}")
    organic = payload.get("organic", [])
    if not isinstance(organic, list):
        return []

    results: list[dict[str, str]] = []
    for item in organic:
        if not isinstance(item, dict):
            continue
        url = str(item.get("link", "") or item.get("url", ""))
        if not url:
            continue
        results.append(
            {
                "url": url,
                "title": str(item.get("title", "")),
                "snippet": str(item.get("snippet", "")),
                "source": _classify_search_source(url),
            }
        )
        if len(results) >= max_results:
            break

    results.sort(key=lambda item: 0 if item["source"] == "official" else 1)
    return results[:max_results]


def _extract_deepseek_search_results(raw_text: str, *, max_results: int) -> list[dict[str, str]]:
    payload = json.loads(raw_text or "{}")
    content = payload.get("content", [])
    if not isinstance(content, list):
        return []

    results: list[dict[str, str]] = []
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "web_search_tool_result":
            continue
        search_items = block.get("content", [])
        if not isinstance(search_items, list):
            continue
        for item in search_items:
            if not isinstance(item, dict) or item.get("type") != "web_search_result":
                continue
            url = str(item.get("url", "")).strip()
            parsed = urlparse(url)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                continue
            results.append(
                {
                    "url": url,
                    "title": str(item.get("title", "")),
                    "snippet": "",
                    "source": _classify_search_source(url),
                }
            )

    results.sort(key=lambda item: 0 if item["source"] == "official" else 1)
    return results[:max_results]


def policy_search(
    query: str,
    *,
    db_path: str | Path,
    limit: int = 5,
    category: str | None = None,
) -> list[dict[str, object]]:
    from app.policy_knowledge.store import PolicyKnowledgeStore

    store = PolicyKnowledgeStore(db_path)
    return store.search(query, limit=limit, category=category)


def policy_materials(
    *,
    user_instruction: str,
    materials: list[object],
    db_path: str | Path,
    limit: int = 3,
) -> list[dict[str, object]]:
    from app.policy_knowledge.materials import build_policy_materials

    return build_policy_materials(
        user_instruction=user_instruction,
        materials=materials,
        db_path=db_path,
        limit=limit,
    )


def policy_research(
    *,
    user_instruction: str,
    materials: list[object],
    db_path: str | Path,
    usage_profile: str,
    limit: int = 3,
) -> dict[str, object]:
    from app.policy_research.service import research_policy_attachment

    result = research_policy_attachment(
        user_instruction=user_instruction,
        materials=[item for item in materials if isinstance(item, dict)],
        db_path=db_path,
        usage_profile=usage_profile,
        limit=limit,
    )
    return result.model_dump()


def bank_search(
    query: str,
    *,
    db_path: str | Path,
    limit: int = 5,
    themes: list[str] | None = None,
) -> list[dict[str, object]]:
    from app.bank_knowledge.store import BankKnowledgeStore

    store = BankKnowledgeStore(db_path)
    return store.search(query, limit=limit, themes=themes)


def bank_materials(
    *,
    user_instruction: str,
    materials: list[object],
    db_path: str | Path,
    limit: int = 3,
) -> list[dict[str, object]]:
    from app.bank_knowledge.materials import build_bank_materials

    return build_bank_materials(
        user_instruction=user_instruction,
        materials=materials,
        db_path=db_path,
        limit=limit,
    )


def read_word_file(path: str, *, allowed_root: str | Path) -> dict[str, str]:
    file_path = _resolve_allowed_file(path, allowed_root)
    if file_path.suffix.lower() != ".docx":
        raise ValueError("仅支持读取 .docx 文件")

    with zipfile.ZipFile(file_path) as archive:
        xml_text = archive.read("word/document.xml").decode("utf-8")

    root = ET.fromstring(xml_text)
    namespace = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    paragraphs: list[str] = []
    for paragraph in root.findall(".//w:p", namespace):
        texts = [node.text or "" for node in paragraph.findall(".//w:t", namespace)]
        line = "".join(texts).strip()
        if line:
            paragraphs.append(line)

    return {
        "path": str(file_path),
        "title": file_path.name,
        "text": "\n".join(paragraphs)[:12000],
    }


def read_document_file(
    path: str,
    *,
    allowed_root: str | Path,
    work_dir: str | Path,
    max_file_bytes: int = 50 * 1024 * 1024,
    ocr_scanned_pages: bool = False,
    render_pages: bool = False,
) -> dict[str, object]:
    """安全解析任务内 Word/PDF/PPTX，并把完整标准结果写入 work 目录。"""
    artifact = DocumentService(max_file_bytes=max_file_bytes).parse(
        path,
        allowed_root=allowed_root,
        work_dir=work_dir,
        ocr_scanned_pages=ocr_scanned_pages,
        render_pages=render_pages,
    )
    return artifact.to_material()


def read_pdf_file(
    path: str,
    *,
    allowed_root: str | Path,
    extractor: Callable[[Path], str] | None = None,
) -> dict[str, str]:
    file_path = _resolve_allowed_file(path, allowed_root)
    if file_path.suffix.lower() != ".pdf":
        raise ValueError("仅支持读取 .pdf 文件")

    text = extractor(file_path) if extractor else _extract_pdf_text(file_path)
    return {
        "path": str(file_path),
        "title": file_path.name,
        "text": text[:12000],
    }


def _search_api_host(base_url: str) -> str:
    parsed = urlparse(base_url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("Only http and https search API URLs are allowed")
    normalized = base_url.rstrip("/")
    if normalized.endswith("/anthropic"):
        normalized = normalized[: -len("/anthropic")]
    return normalized


def _search_provider(base_url: str) -> str:
    parsed = urlparse(base_url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("Only http and https search API URLs are allowed")
    hostname = (parsed.hostname or "").lower().rstrip(".")
    if hostname == "deepseek.com" or hostname.endswith(".deepseek.com"):
        return "deepseek"
    if hostname == "minimaxi.com" or hostname.endswith(".minimaxi.com"):
        return "minimax"
    if hostname == "minimax.io" or hostname.endswith(".minimax.io"):
        return "minimax"
    return "unsupported"


def _deepseek_messages_url(base_url: str) -> str:
    parsed = urlparse(base_url)
    return f"{parsed.scheme}://{parsed.netloc}/anthropic/v1/messages"


def _default_search_request(url: str, payload: dict[str, object], headers: dict[str, str], timeout: int) -> str:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8")


def _classify_search_source(url: str) -> str:
    official_domains = ("gov.cn", "pbc.gov.cn", "pboc.gov.cn", "cbirc.gov.cn", "csrc.gov.cn", "safe.gov.cn")
    return "official" if any(domain in url for domain in official_domains) else "media"


def _resolve_allowed_file(path: str, allowed_root: str | Path) -> Path:
    root = Path(allowed_root).resolve()
    file_path = Path(path).resolve()
    if root != file_path and root not in file_path.parents:
        raise ValueError("不允许读取当前任务目录之外的文件")
    if not file_path.exists() or not file_path.is_file():
        raise FileNotFoundError(f"文件不存在：{file_path}")
    return file_path


def _extract_pdf_text(path: Path) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise RuntimeError("缺少 pypdf，无法读取 PDF。") from exc

    reader = PdfReader(str(path))
    parts = [page.extract_text() or "" for page in reader.pages]
    return "\n".join(part.strip() for part in parts if part.strip())


def _fetch_url(
    url: str,
    *,
    requester: Callable[..., Any] | None = None,
    resolver: Callable[..., list[tuple[Any, ...]]] = socket.getaddrinfo,
    max_bytes: int = MAX_WEB_RESPONSE_BYTES,
) -> str:
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    current_url = url
    for redirect_count in range(MAX_WEB_REDIRECTS + 1):
        hostname, port, addresses = _resolve_public_web_target(current_url, resolver=resolver)
        with _open_web_response(
            current_url,
            hostname=hostname,
            port=port,
            addresses=addresses,
            headers=headers,
            requester=requester,
        ) as response:
            status_code = int(getattr(response, "status_code", 0))
            if status_code in {301, 302, 303, 307, 308}:
                if redirect_count >= MAX_WEB_REDIRECTS:
                    raise RuntimeError("网页重定向次数过多")
                location = _response_header(response, "location")
                if not location:
                    raise RuntimeError("网页重定向缺少目标地址")
                current_url = urljoin(current_url, location)
                continue
            if status_code != 200:
                raise RuntimeError(f"网页读取失败，HTTP 状态码：{status_code}")
            return _read_limited_response_body(response, max_bytes=max_bytes)
    raise RuntimeError("网页重定向次数过多")


@contextmanager
def _open_web_response(
    url: str,
    *,
    hostname: str,
    port: int,
    addresses: tuple[str, ...],
    headers: dict[str, str],
    requester: Callable[..., Any] | None,
):
    request_kwargs = {
        "headers": headers,
        "impersonate": "chrome110",
        "timeout": 15,
        "allow_redirects": False,
        "stream": True,
        "proxy": "",
    }
    if requester is not None:
        response = requester(url, **request_kwargs)
        try:
            yield response
        finally:
            close = getattr(response, "close", None)
            if callable(close):
                close()
        return

    try:
        from curl_cffi import requests as curl_requests
        from curl_cffi.const import CurlOpt
    except ImportError as exc:
        raise RuntimeError("缺少 curl-cffi，无法读取网页。") from exc

    curl_options: dict[Any, object] = {}
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        formatted_addresses = ",".join(f"[{address}]" if ":" in address else address for address in addresses)
        curl_options[CurlOpt.RESOLVE] = [f"{hostname}:{port}:{formatted_addresses}"]

    with curl_requests.Session(curl_options=curl_options) as session:
        response = session.get(url, **request_kwargs)
        try:
            yield response
        finally:
            response.close()


def _response_header(response: Any, name: str) -> str:
    headers = getattr(response, "headers", {}) or {}
    direct = headers.get(name) or headers.get(name.title())
    if direct:
        return str(direct)
    for key, value in getattr(headers, "items", lambda: ())():
        if str(key).lower() == name.lower():
            return str(value)
    return ""


def _read_limited_response_body(response: Any, *, max_bytes: int) -> str:
    content_length = _response_header(response, "content-length")
    if content_length:
        try:
            if int(content_length) > max_bytes:
                raise ValueError("网页内容过大，无法安全读取")
        except ValueError as exc:
            if "过大" in str(exc):
                raise

    body = bytearray()
    iterator = getattr(response, "iter_content", None)
    if callable(iterator):
        chunks = iterator(chunk_size=64 * 1024)
    else:
        content = getattr(response, "content", b"")
        if not content:
            content = str(getattr(response, "text", "")).encode("utf-8")
        chunks = (content,)
    for chunk in chunks:
        if not chunk:
            continue
        encoded = chunk if isinstance(chunk, bytes) else str(chunk).encode("utf-8")
        if len(body) + len(encoded) > max_bytes:
            raise ValueError("网页内容过大，无法安全读取")
        body.extend(encoded)

    encoding = getattr(response, "encoding", "") or "utf-8"
    try:
        return bytes(body).decode(str(encoding), errors="replace")
    except LookupError:
        return bytes(body).decode("utf-8", errors="replace")


def _extract_page_text(url: str, html: str) -> dict[str, object]:
    try:
        from bs4 import BeautifulSoup
    except ImportError as exc:
        raise RuntimeError("缺少 beautifulsoup4，无法解析网页。") from exc

    soup = BeautifulSoup(html, "lxml")
    # 部分权威站点会把公共页头片段连同 </html> 一起嵌入正文模板。
    # lxml 遇到这个提前闭合标签会丢弃后续文章，而 html.parser 能保留它。
    if not soup.find("p") and re.search(r"<p(?:\s|>)", html, flags=re.IGNORECASE):
        fallback_soup = BeautifulSoup(html, "html.parser")
        if fallback_soup.find("p"):
            soup = fallback_soup
    title = _extract_page_title(soup)
    canonical_url = _extract_canonical_url(soup, url)
    site = _extract_site(canonical_url or url)
    links = _extract_dated_html_links(soup, url)
    publish_date, date_source = _extract_verified_people_daily_issue_date(soup, canonical_url)
    if publish_date is None:
        # 日期元数据常位于 JSON-LD script 中，必须在正文清洗前提取。
        publish_date, date_source = _extract_publish_date(
            soup,
            include_visible_text=False,
        )

    for tag in soup.find_all(["script", "style", "nav", "footer", "header"]):
        tag.decompose()

    if publish_date is None:
        publish_date, date_source = _extract_publish_date(soup)

    paragraphs = _extract_article_paragraphs(soup)
    text_parts = [item for item in paragraphs if item]

    if not text_parts:
        main = soup.find("article") or soup.find("main") or soup.body
        if main:
            text_parts = [main.get_text("\n", strip=True)]

    return {
        "url": url,
        "title": title,
        "text": "\n".join(text_parts)[:4000],
        "publish_date": publish_date.isoformat() if publish_date else "",
        "site": site,
        "canonical_url": canonical_url,
        "date_extracted_from": date_source,
        "links": links,
    }


def _extract_dated_html_links(soup: Any, page_url: str) -> list[dict[str, str]]:
    """提取公开列表页中带发布日期的文章链接，不主动跟随链接。"""
    links: list[dict[str, str]] = []
    seen: set[str] = set()
    for anchor in soup.find_all("a", href=True):
        raw_url = str(anchor.get("href") or "").strip()
        if not raw_url or raw_url.startswith(("#", "javascript:", "mailto:")):
            continue
        resolved_url = urljoin(page_url, raw_url)
        if urlparse(resolved_url).scheme not in {"http", "https"}:
            continue
        title = str(anchor.get("title") or anchor.get_text(" ", strip=True)).strip()
        if not title:
            continue
        container = anchor.find_parent(["li", "tr"]) or anchor.parent
        container_text = container.get_text(" ", strip=True) if container else ""
        publish_date, _ = _extract_date_from_text(container_text)
        if publish_date is None or resolved_url in seen:
            continue
        seen.add(resolved_url)
        links.append(
            {
                "title": title,
                "url": resolved_url,
                "publish_date": publish_date.isoformat(),
            }
        )
        if len(links) >= MAX_WEB_INDEX_LINKS:
            break
    return links


def _extract_canonical_url(soup: Any, fallback_url: str) -> str:
    canonical = soup.find("link", rel="canonical")
    if canonical and canonical.get("href"):
        href = str(canonical["href"]).strip()
        if href:
            return urljoin(fallback_url, href)
    return fallback_url


def _extract_page_title(soup: Any) -> str:
    for attr, value in (("property", "og:title"), ("name", "twitter:title")):
        node = soup.find("meta", {attr: value})
        if node and node.get("content"):
            title = str(node["content"]).strip()
            if title:
                return title
    return soup.title.get_text(strip=True) if soup.title else ""


def _extract_site(url: str) -> str:
    parsed = urlparse(url)
    hostname = (parsed.hostname or "").strip().lower().lstrip("www.")
    return hostname


def _extract_verified_people_daily_issue_date(soup: Any, url: str) -> tuple[date | None, str]:
    """人民日报旧版页面的 publishdate 固定为旧值，需用页面内重复的期号路径校验。"""
    parsed_url = urlparse(url)
    hostname = (parsed_url.hostname or "").lower()
    if hostname != "paper.people.com.cn":
        return None, ""

    match = re.search(r"/(?:pc|pad)/content/(\d{4})(\d{2})/(\d{2})/", parsed_url.path)
    if match is None:
        return None, ""
    year, month, day = (int(item) for item in match.groups())
    try:
        issue_date = date(year, month, day)
    except ValueError:
        return None, ""

    issue_path = f"{year:04d}{month:02d}/{day:02d}"
    page_markup = str(soup)
    if f"layout/{issue_path}" not in page_markup and f"content/{issue_path}" not in page_markup:
        return None, ""
    return issue_date, "people-daily:verified-issue-path"


def _extract_publish_date(
    soup: Any,
    *,
    include_visible_text: bool = True,
) -> tuple[date | None, str]:
    """从 HTML 中提取发布日期，返回 (date, source_description)。"""
    # 1. OpenGraph / article meta
    meta_selectors = [
        ('meta', 'property', 'article:published_time', 'article:published_time'),
        ('meta', 'property', 'og:published_time', 'og:published_time'),
        ('meta', 'name', 'publishdate', 'meta:publishdate'),
        ('meta', 'name', 'pubdate', 'meta:pubdate'),
        ('meta', 'name', 'published_time', 'meta:published_time'),
        ('meta', 'name', 'release_date', 'meta:release_date'),
        ('meta', 'name', 'citation_publication_date', 'meta:citation_publication_date'),
        ('meta', 'name', 'DC.date', 'meta:DC.date'),
    ]
    for tag, attr, value, source in meta_selectors:
        node = soup.find(tag, {attr: value})
        if node and node.get("content"):
            parsed = _parse_date(str(node["content"]))
            if parsed:
                return parsed, source

    # 2. JSON-LD datePublished
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(data, dict):
            for key in ("datePublished", "dateModified"):
                parsed = _parse_date(str(data.get(key, "")))
                if parsed:
                    return parsed, f"json-ld:{key}"
            graph = data.get("@graph", [])
            if isinstance(graph, list):
                for item in graph:
                    if isinstance(item, dict):
                        for key in ("datePublished", "dateModified"):
                            parsed = _parse_date(str(item.get(key, "")))
                            if parsed:
                                return parsed, f"json-ld:{key}"

    if not include_visible_text:
        return None, ""

    # 3. <time datetime="..."> 或可见日期文本
    for time_node in soup.find_all("time"):
        datetime_attr = time_node.get("datetime")
        if datetime_attr:
            parsed = _parse_date(str(datetime_attr))
            if parsed:
                return parsed, "time:datetime"
        time_text = time_node.get_text(" ", strip=True)
        parsed, _ = _extract_date_from_text(time_text)
        if parsed:
            return parsed, "time:text"

    # 4. 中文/常见日期文本（仅出现在 body 区域）
    body = (
        soup.select_one("#UCAP-CONTENT")
        or soup.find("article")
        or soup.find("main")
        or soup.body
    )
    if body:
        body_text = body.get_text(" ", strip=True)
        parsed, pattern = _extract_date_from_text(body_text)
        if parsed:
            return parsed, f"text:{pattern}"

    return None, ""


_DATE_PATTERNS = [
    (r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", "%Y-%m-%d"),
    (r"(\d{4})\.(\d{1,2})\.(\d{1,2})", "%Y-%m-%d"),
    (r"(\d{4})年(\d{1,2})月(\d{1,2})日", "%Y-%m-%d"),
    (r"(\d{4})年(\d{1,2})月(\d{1,2})号", "%Y-%m-%d"),
]


_DATE_FORMATS = [
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M:%S%z",
    "%Y-%m-%dT%H:%M:%S.%f",
    "%Y-%m-%d %H:%M:%S",
    "%Y%m%d",
]


def _parse_date(value: str) -> date | None:
    value = value.strip()[:30]
    if not value or value.lower() in {"none", "null"}:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def _extract_date_from_text(text: str) -> tuple[date | None, str]:
    for pattern, _ in _DATE_PATTERNS:
        match = re.search(pattern, text)
        if match:
            year, month, day = match.groups()
            try:
                return date(int(year), int(month), int(day)), pattern
            except ValueError:
                continue
    return None, ""


def _extract_article_paragraphs(soup: Any) -> list[str]:
    # Baijiahao正文在 span.bjh-p 中，普通 p 标签常常只有作者或来源。
    baijiahao_nodes = soup.select('[data-testid="article"] .bjh-p')
    if baijiahao_nodes:
        return _dedupe_paragraphs(node.get_text(" ", strip=True) for node in baijiahao_nodes)

    article_nodes = soup.select("article p, [data-testid='article'] p")
    if article_nodes:
        return _dedupe_paragraphs(node.get_text(" ", strip=True) for node in article_nodes)

    return _dedupe_paragraphs(p.get_text(" ", strip=True) for p in soup.find_all("p"))


def _dedupe_paragraphs(paragraphs: Any) -> list[str]:
    seen: set[str] = set()
    cleaned: list[str] = []
    for item in paragraphs:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        cleaned.append(text)
    return cleaned


class LLMWriter:
    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        skill_dir: Path,
        client: Any | None = None,
    ):
        self.api_key = api_key
        self.base_url = base_url
        self.model = model
        self.skill_dir = skill_dir
        self.client = client

    def write(self, payload: dict[str, object]) -> dict[str, str]:
        prompt = self._build_prompt(payload)
        client = self.client or self._create_client()
        response = client.messages.create(
            model=self.model,
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}],
        )
        text = self._response_text(response)
        return self._parse_title_body(text)

    def _create_client(self) -> Any:
        if not self.api_key:
            raise RuntimeError("缺少 ANTHROPIC_API_KEY，无法调用写作模型。")
        try:
            import anthropic
        except ImportError as exc:
            raise RuntimeError("缺少 anthropic，无法调用写作模型。") from exc
        return anthropic.Anthropic(api_key=self.api_key, base_url=self.base_url)

    def _build_prompt(self, payload: dict[str, object]) -> str:
        skill_text = (self.skill_dir / "SKILL.md").read_text(encoding="utf-8")
        draft_prompt = (self.skill_dir / "prompts" / "draft.md").read_text(encoding="utf-8")
        materials = payload.get("materials", [])
        material_text = self._format_materials(materials if isinstance(materials, list) else [])
        planning_note = str(payload.get("planning_note", "") or "").strip()
        planning_block = f"## 写作规划\n\n{planning_note}\n\n---\n\n" if planning_note else ""
        return f"""{draft_prompt}

---

## Skill 规则

{skill_text}

---

## 用户要求

{payload.get("instruction", "")}

---

{planning_block}## 用户材料

{material_text}

---

请输出：
标题：...

正文：...
"""

    def _format_materials(self, materials: list[object]) -> str:
        sections: list[str] = []
        for idx, item in enumerate(materials, 1):
            if not isinstance(item, dict):
                continue
            sections.append(
                f"【材料{idx}】\n"
                f"标题：{item.get('title', '')}\n"
                f"来源：{item.get('url', '')}\n"
                f"材料类型：{item.get('source', '')}\n"
                f"政策分类：{item.get('category', '')}\n"
                f"发布日期：{item.get('publish_date', '')}\n"
                f"正文：{item.get('text', '')}"
            )
        return "\n\n".join(sections)

    def _response_text(self, response: Any) -> str:
        parts: list[str] = []
        for block in getattr(response, "content", []):
            text = getattr(block, "text", "")
            if text:
                parts.append(text)
        return "\n".join(parts).strip()

    def _parse_title_body(self, text: str) -> dict[str, str]:
        title_match = re.search(r"标题[:：]\s*(.+)", text)
        body_match = re.search(r"正文[:：]\s*([\s\S]+)", text)
        if title_match and body_match:
            return {
                "title": title_match.group(1).strip(),
                "body": body_match.group(1).strip(),
            }

        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if not lines:
            return {"title": "", "body": ""}
        return {"title": lines[0], "body": "\n".join(lines[1:]).strip()}
