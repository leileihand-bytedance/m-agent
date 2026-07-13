from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from html import unescape
from urllib.parse import parse_qs, urlencode, urljoin, urlparse


BASE_URL = "https://www.nfra.gov.cn"
LIST_ENDPOINT = f"{BASE_URL}/cbircweb/DocInfo/SelectDocByItemIdAndChild"
DETAIL_ENDPOINT = f"{BASE_URL}/cbircweb/DocInfo/SelectByDocId"


@dataclass(frozen=True)
class NfraSource:
    category: str
    item_id: str
    name: str


NFRA_SOURCES = (
    NfraSource(category="policy_interpretation", item_id="917", name="政策解读"),
    NfraSource(category="regulatory_update", item_id="915", name="监管动态"),
)


class NfraClient:
    def __init__(self, requester: Callable[[str], dict[str, object]] | None = None):
        self._requester = requester or _default_json_requester

    def list_documents(self, *, item_id: str, page_index: int, page_size: int) -> list[dict[str, object]]:
        query = urlencode({"itemId": item_id, "pageSize": page_size, "pageIndex": page_index})
        payload = self._requester(f"{LIST_ENDPOINT}?{query}")
        if payload.get("rptCode") != 200:
            raise RuntimeError(f"NFRA list API failed: {payload.get('msg') or payload.get('rptCode')}")
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        rows = data.get("rows") if isinstance(data, dict) else []
        return [row for row in rows if isinstance(row, dict)]

    def get_document(self, doc_id: str | int) -> dict[str, object]:
        query = urlencode({"docId": str(doc_id)})
        payload = self._requester(f"{DETAIL_ENDPOINT}?{query}")
        if payload.get("rptCode") != 200:
            raise RuntimeError(f"NFRA detail API failed: {payload.get('msg') or payload.get('rptCode')}")
        data = payload.get("data")
        if not isinstance(data, dict):
            raise RuntimeError("NFRA detail API returned invalid data")
        return data


def fetch_recent_nfra_documents(
    *,
    client: NfraClient | None = None,
    today: date | None = None,
    days: int = 92,
    page_size: int = 18,
    max_pages: int = 30,
) -> list[dict[str, object]]:
    client = client or NfraClient()
    cutoff = (today or date.today()) - timedelta(days=days)
    documents: list[dict[str, object]] = []
    fetched_original_doc_ids: set[str] = set()

    for source in NFRA_SOURCES:
        for page_index in range(1, max_pages + 1):
            rows = client.list_documents(item_id=source.item_id, page_index=page_index, page_size=page_size)
            if not rows:
                break

            should_stop_source = False
            for row in rows:
                publish_date = _parse_publish_date(str(row.get("publishDate", "")))
                if publish_date and publish_date.date() < cutoff:
                    should_stop_source = True
                    continue

                doc_id = str(row.get("docId", "")).strip()
                if not doc_id:
                    continue

                detail = client.get_document(doc_id)
                document = _normalize_document(source, row, detail)
                documents.append(document)
                if source.category == "policy_interpretation":
                    for original_link in document["original_links"]:
                        original_doc_id = str(original_link.get("doc_id", "")).strip()
                        if not original_doc_id or original_doc_id in fetched_original_doc_ids:
                            continue
                        fetched_original_doc_ids.add(original_doc_id)
                        try:
                            original_detail = client.get_document(original_doc_id)
                        except Exception as exc:
                            metadata = document.setdefault("metadata", {})
                            if isinstance(metadata, dict):
                                errors = metadata.setdefault("original_fetch_errors", [])
                                if isinstance(errors, list):
                                    errors.append(
                                        {
                                            "doc_id": original_doc_id,
                                            "url": str(original_link.get("url", "")),
                                            "error": f"{type(exc).__name__}: {exc}",
                                        }
                                    )
                            continue
                        documents.append(
                            _normalize_original_document(
                                detail=original_detail,
                                original_link=original_link,
                                linked_from=document,
                            )
                        )

            if should_stop_source:
                break

    return documents


def _normalize_original_document(
    *,
    detail: dict[str, object],
    original_link: dict[str, str],
    linked_from: dict[str, object],
) -> dict[str, object]:
    doc_id = str(detail.get("docId") or original_link.get("doc_id") or "").strip()
    title = str(detail.get("docTitle") or original_link.get("title") or "").strip()
    publish_date = str(detail.get("publishDate") or linked_from.get("publish_date") or "").strip()
    html = str(detail.get("docClob") or "")
    url = str(original_link.get("url") or "").strip()
    if not url:
        url = f"{BASE_URL}/cn/view/pages/governmentDetail.html?{urlencode({'docId': doc_id, 'generaltype': '1'})}"

    return {
        "source": "nfra",
        "category": "policy_original",
        "item_id": "",
        "doc_id": doc_id,
        "title": title,
        "publish_date": publish_date,
        "url": url,
        "text": clean_html_text(html),
        "original_links": [],
        "metadata": {
            "source_name": "政策原文",
            "doc_source": detail.get("docSource"),
            "generaltype": str(detail.get("generaltype") or "1"),
            "linked_from_doc_id": linked_from.get("doc_id"),
            "linked_from_title": linked_from.get("title"),
        },
    }


def _normalize_document(
    source: NfraSource,
    row: dict[str, object],
    detail: dict[str, object],
) -> dict[str, object]:
    doc_id = str(detail.get("docId") or row.get("docId") or "").strip()
    generaltype = str(detail.get("generaltype") or row.get("generaltype") or "0").strip()
    title = str(detail.get("docTitle") or row.get("docTitle") or row.get("docSubtitle") or "").strip()
    publish_date = str(detail.get("publishDate") or row.get("publishDate") or "").strip()
    html = str(detail.get("docClob") or "")
    text = clean_html_text(html)
    original_links = extract_original_links(html)
    url = (
        f"{BASE_URL}/cn/view/pages/ItemDetail.html?"
        f"{urlencode({'docId': doc_id, 'itemId': source.item_id, 'generaltype': generaltype})}"
    )

    return {
        "source": "nfra",
        "category": source.category,
        "item_id": source.item_id,
        "doc_id": doc_id,
        "title": title,
        "publish_date": publish_date,
        "url": url,
        "text": text,
        "original_links": original_links,
        "metadata": {
            "source_name": source.name,
            "doc_source": detail.get("docSource"),
            "generaltype": generaltype,
        },
    }


def clean_html_text(html: str) -> str:
    try:
        from bs4 import BeautifulSoup
    except ImportError as exc:
        raise RuntimeError("缺少 beautifulsoup4，无法清洗监管网页正文。") from exc

    soup = BeautifulSoup(unescape(html), "lxml")
    for tag in soup.find_all(["script", "style", "meta", "xml"]):
        tag.decompose()

    paragraphs = [p.get_text(" ", strip=True) for p in soup.find_all("p")]
    parts = [part for part in paragraphs if part]
    if not parts:
        main = soup.find("body") or soup
        parts = [main.get_text("\n", strip=True)]

    return "\n".join(_compact_spaces(part) for part in parts if _compact_spaces(part))


def extract_original_links(html: str) -> list[dict[str, str]]:
    try:
        from bs4 import BeautifulSoup
    except ImportError as exc:
        raise RuntimeError("缺少 beautifulsoup4，无法识别政策原文链接。") from exc

    soup = BeautifulSoup(unescape(html), "lxml")
    links: list[dict[str, str]] = []
    seen: set[str] = set()
    for anchor in soup.find_all("a"):
        href = str(anchor.get("href") or "").strip()
        if not href:
            continue
        absolute_url = urljoin(BASE_URL, href)
        parsed = urlparse(absolute_url)
        if parsed.netloc and not parsed.netloc.endswith("nfra.gov.cn"):
            continue
        if "governmentDetail.html" not in absolute_url and "ItemDetail.html" not in absolute_url:
            continue
        if absolute_url in seen:
            continue
        seen.add(absolute_url)
        doc_id = parse_qs(parsed.query).get("docId", [""])[0]
        links.append(
            {
                "url": absolute_url,
                "doc_id": doc_id,
                "title": anchor.get_text(" ", strip=True),
            }
        )
    return links


def _default_json_requester(url: str) -> dict[str, object]:
    try:
        from curl_cffi import requests as curl_requests
    except ImportError as exc:
        raise RuntimeError("缺少 curl-cffi，无法抓取监管政策。") from exc

    response = curl_requests.get(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json,text/plain,*/*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        },
        impersonate="chrome110",
        timeout=45,
    )
    if response.status_code != 200:
        raise RuntimeError(f"NFRA request failed, HTTP {response.status_code}")
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError("NFRA request returned invalid JSON")
    return payload


def _parse_publish_date(value: str) -> datetime | None:
    value = value.strip()
    if not value:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def _compact_spaces(value: str) -> str:
    return " ".join(value.replace("\u3000", " ").split())
