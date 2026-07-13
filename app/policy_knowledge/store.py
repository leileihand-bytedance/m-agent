from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


class PolicyKnowledgeStore:
    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def upsert_documents(self, documents: list[dict[str, object]]) -> int:
        if not documents:
            return 0

        with self._connect() as conn:
            for document in documents:
                source = str(document.get("source") or "")
                doc_id = str(document.get("doc_id") or "")
                if not source or not doc_id:
                    continue
                text = str(document.get("text") or "")
                title = str(document.get("title") or "")
                content_hash = hashlib.sha256(f"{title}\n{text}".encode("utf-8")).hexdigest()
                conn.execute(
                    """
                    insert into policy_documents (
                        source, category, item_id, doc_id, title, publish_date, url,
                        text, original_links_json, metadata_json, content_hash, fetched_at,
                        theme_tags_json, region_tags_json, audience_tags_json,
                        source_weight, is_enabled, disabled_reason, review_note
                    ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    on conflict(source, doc_id) do update set
                        category = case
                            when excluded.category = 'policy_original' then excluded.category
                            when policy_documents.category = 'policy_original' then policy_documents.category
                            when excluded.category = 'policy_interpretation' then excluded.category
                            when policy_documents.category = 'policy_interpretation' then policy_documents.category
                            else excluded.category
                        end,
                        item_id = case
                            when excluded.category = 'policy_original' then excluded.item_id
                            when policy_documents.category = 'policy_original' then policy_documents.item_id
                            when excluded.category = 'policy_interpretation' then excluded.item_id
                            when policy_documents.category = 'policy_interpretation' then policy_documents.item_id
                            else excluded.item_id
                        end,
                        title = excluded.title,
                        publish_date = excluded.publish_date,
                        url = case
                            when excluded.category = 'policy_original' then excluded.url
                            when policy_documents.category = 'policy_original' then policy_documents.url
                            when excluded.category = 'policy_interpretation' then excluded.url
                            when policy_documents.category = 'policy_interpretation' then policy_documents.url
                            else excluded.url
                        end,
                        text = excluded.text,
                        original_links_json = excluded.original_links_json,
                        metadata_json = excluded.metadata_json,
                        content_hash = excluded.content_hash,
                        fetched_at = excluded.fetched_at,
                        theme_tags_json = excluded.theme_tags_json,
                        region_tags_json = excluded.region_tags_json,
                        audience_tags_json = excluded.audience_tags_json,
                        source_weight = excluded.source_weight,
                        is_enabled = excluded.is_enabled,
                        disabled_reason = excluded.disabled_reason,
                        review_note = excluded.review_note
                    """,
                    (
                        source,
                        str(document.get("category") or ""),
                        str(document.get("item_id") or ""),
                        doc_id,
                        title,
                        str(document.get("publish_date") or ""),
                        str(document.get("url") or ""),
                        text,
                        json.dumps(document.get("original_links") or [], ensure_ascii=False),
                        json.dumps(document.get("metadata") or {}, ensure_ascii=False),
                        content_hash,
                        datetime.now(timezone.utc).isoformat(),
                        json.dumps(document.get("theme_tags") or [], ensure_ascii=False),
                        json.dumps(document.get("region_tags") or [], ensure_ascii=False),
                        json.dumps(document.get("audience_tags") or [], ensure_ascii=False),
                        int(document.get("source_weight") or 0),
                        int(document.get("is_enabled", 1)),
                        str(document.get("disabled_reason") or ""),
                        str(document.get("review_note") or ""),
                    ),
                )
        return len(documents)

    def count_documents(self) -> int:
        with self._connect() as conn:
            row = conn.execute("select count(*) from policy_documents").fetchone()
        return int(row[0])

    def search(self, query: str, *, limit: int = 5, category: str | None = None) -> list[dict[str, object]]:
        query = query.strip()
        if not query:
            return []

        terms = _query_terms(query)
        if not terms:
            return []

        sql = """
            select source, category, item_id, doc_id, title, publish_date, url, text,
                   original_links_json, metadata_json, theme_tags_json, region_tags_json,
                   audience_tags_json, source_weight, is_enabled, disabled_reason, review_note
            from policy_documents
        """
        params: tuple[str, ...] = ()
        if category:
            sql += " where category = ? and is_enabled = 1"
            params = (category,)
        else:
            sql += " where is_enabled = 1"
        sql += " order by publish_date desc, doc_id desc"

        scored: list[tuple[int, dict[str, object]]] = []
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        for row in rows:
            document = _row_to_dict(row)
            score = _score_document(document, terms)
            if score <= 0:
                continue
            document["score"] = score
            document["snippet"] = _make_snippet(str(document["text"]), terms)
            scored.append((score, document))

        scored.sort(key=lambda item: (item[0], str(item[1]["publish_date"])), reverse=True)
        return [document for _, document in scored[:limit]]

    def list_documents(self, *, category: str | None = None, limit: int | None = None) -> list[dict[str, object]]:
        sql = """
            select source, category, item_id, doc_id, title, publish_date, url, text,
                   original_links_json, metadata_json, theme_tags_json, region_tags_json,
                   audience_tags_json, source_weight, is_enabled, disabled_reason, review_note
            from policy_documents
        """
        params: tuple[object, ...] = ()
        if category:
            sql += " where category = ?"
            params = (category,)
        sql += " order by publish_date desc, doc_id desc"
        if limit is not None:
            sql += " limit ?"
            params = (*params, int(limit))

        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [_row_to_dict(row) for row in rows]

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                create table if not exists policy_documents (
                    source text not null,
                    category text not null,
                    item_id text not null,
                    doc_id text not null,
                    title text not null,
                    publish_date text not null,
                    url text not null,
                    text text not null,
                    original_links_json text not null,
                    metadata_json text not null,
                    content_hash text not null,
                    fetched_at text not null,
                    theme_tags_json text not null default '[]',
                    region_tags_json text not null default '[]',
                    audience_tags_json text not null default '[]',
                    source_weight integer not null default 0,
                    is_enabled integer not null default 1,
                    disabled_reason text not null default '',
                    review_note text not null default '',
                    primary key (source, doc_id)
                )
                """
            )
            _ensure_column(conn, "theme_tags_json", "text not null default '[]'")
            _ensure_column(conn, "region_tags_json", "text not null default '[]'")
            _ensure_column(conn, "audience_tags_json", "text not null default '[]'")
            _ensure_column(conn, "source_weight", "integer not null default 0")
            _ensure_column(conn, "is_enabled", "integer not null default 1")
            _ensure_column(conn, "disabled_reason", "text not null default ''")
            _ensure_column(conn, "review_note", "text not null default ''")
            conn.execute(
                "create index if not exists idx_policy_documents_publish_date on policy_documents(publish_date)"
            )
            conn.execute(
                "create index if not exists idx_policy_documents_category on policy_documents(category)"
            )
            conn.execute(
                "create index if not exists idx_policy_documents_enabled on policy_documents(is_enabled)"
            )


def _row_to_dict(row: sqlite3.Row) -> dict[str, object]:
    metadata = json.loads(row["metadata_json"] or "{}")
    text = row["text"]
    if _looks_like_navigation_stub(row["source"], text):
        summary = str(metadata.get("summary") or "").strip()
        if summary:
            text = summary
    return {
        "source": row["source"],
        "category": row["category"],
        "item_id": row["item_id"],
        "doc_id": row["doc_id"],
        "title": row["title"],
        "publish_date": row["publish_date"],
        "url": row["url"],
        "text": text,
        "original_links": json.loads(row["original_links_json"] or "[]"),
        "metadata": metadata,
        "theme_tags": json.loads(row["theme_tags_json"] or "[]"),
        "region_tags": json.loads(row["region_tags_json"] or "[]"),
        "audience_tags": json.loads(row["audience_tags_json"] or "[]"),
        "source_weight": int(row["source_weight"] or 0),
        "is_enabled": int(row["is_enabled"] or 1),
        "disabled_reason": row["disabled_reason"] or "",
        "review_note": row["review_note"] or "",
    }


def _query_terms(query: str) -> list[str]:
    known_terms = [
        "小微企业",
        "普惠金融",
        "数字金融",
        "科技金融",
        "人工智能",
        "金融服务",
        "风险防控",
        "严监管",
        "强监管",
        "消费投诉",
        "消费者权益",
        "银行业",
        "保险业",
        "信用贷款",
        "续贷",
        "首贷",
        "广东",
        "深圳",
        "国务院",
        "国务院办公厅",
        "消费",
        "宏观经济",
        "扩大内需",
        "稳增长",
        "优化营商环境",
        "促进消费",
        "扩大消费",
        "服务消费",
        "消费品以旧换新",
        "实体经济",
        "民营经济",
        "中小企业",
        "制造业",
        "战略性新兴产业",
        "新兴产业",
        "科技创新",
        "新质生产力",
        "未来产业",
        "低空经济",
        "量子科技",
    ]
    terms = [term for term in known_terms if term in query]
    terms.extend(token for token in re.split(r"[\s,，、;；]+", query) if len(token.strip()) >= 2)
    terms.extend(re.findall(r"[A-Za-z0-9]+", query))
    if len(query) <= 16:
        terms.append(query)
    terms = list(dict.fromkeys(term for term in terms if term.strip()))
    if any(term in terms for term in ("促进消费", "扩大消费", "服务消费", "消费品以旧换新")):
        terms = [term for term in terms if term != "消费"]
    return terms


def _score_document(document: dict[str, object], terms: list[str]) -> int:
    title = str(document["title"])
    text = str(document["text"])
    match_score = 0
    for term in terms:
        match_score += title.count(term) * 6
        match_score += text.count(term) * 2
    if match_score <= 0:
        return 0

    score = match_score + int(document.get("source_weight") or 0)
    category = str(document["category"])
    if category == "policy_original":
        score += 12
    elif category == "policy_interpretation":
        score += 3
    if document.get("source") == "govcn" and category == "policy_original":
        score += 8
    return score


def _ensure_column(conn: sqlite3.Connection, column_name: str, definition: str) -> None:
    columns = {row["name"] for row in conn.execute("pragma table_info(policy_documents)").fetchall()}
    if column_name in columns:
        return
    conn.execute(f"alter table policy_documents add column {column_name} {definition}")


def _make_snippet(text: str, terms: list[str], *, max_length: int = 180) -> str:
    first_index = min((text.find(term) for term in terms if term in text), default=0)
    start = max(first_index - 40, 0)
    snippet = text[start : start + max_length].strip()
    if start > 0:
        snippet = "..." + snippet
    if len(text) > start + max_length:
        snippet += "..."
    return snippet


def _looks_like_navigation_stub(source: str, text: str) -> bool:
    compact = " ".join(str(text).replace("\u3000", " ").split())
    if source != "govcn":
        return False
    return compact.startswith("首页 | 简 | 繁 | EN") and len(compact) < 200
