from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


class BankKnowledgeStore:
    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def replace_source_entries(self, source_file: str, entries: list[dict[str, object]]) -> int:
        with self._connect() as conn:
            conn.execute("delete from bank_entries where source_file = ?", (source_file,))
            for entry in entries:
                entry_id = str(entry.get("entry_id") or "").strip()
                title = str(entry.get("title") or "").strip()
                text = str(entry.get("text") or "").strip()
                if not entry_id or not title or not text:
                    continue
                conn.execute(
                    """
                    insert into bank_entries (
                        entry_id, source_file, source_type, section, title, text,
                        themes_json, entity_type, usage_type, source_page,
                        metadata_json, content_hash, updated_at
                    ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        entry_id,
                        source_file,
                        str(entry.get("source_type") or ""),
                        str(entry.get("section") or ""),
                        title,
                        text,
                        json.dumps(entry.get("themes") or [], ensure_ascii=False),
                        str(entry.get("entity_type") or ""),
                        str(entry.get("usage_type") or ""),
                        str(entry.get("source_page") or ""),
                        json.dumps(entry.get("metadata") or {}, ensure_ascii=False),
                        hashlib.sha256(f"{title}\n{text}".encode("utf-8")).hexdigest(),
                        datetime.now(timezone.utc).isoformat(),
                    ),
                )
        return len(entries)

    def count_entries(self) -> int:
        with self._connect() as conn:
            row = conn.execute("select count(*) from bank_entries").fetchone()
        return int(row[0])

    def search(self, query: str, *, limit: int = 5, themes: list[str] | None = None) -> list[dict[str, object]]:
        query = query.strip()
        if not query:
            return []
        terms = _query_terms(query)
        if not terms:
            return []

        with self._connect() as conn:
            rows = conn.execute(
                """
                select entry_id, source_file, source_type, section, title, text,
                       themes_json, entity_type, usage_type, source_page, metadata_json
                from bank_entries
                order by updated_at desc, entry_id asc
                """
            ).fetchall()

        theme_filter = set(themes or [])
        scored: list[tuple[int, dict[str, object]]] = []
        for row in rows:
            entry = _row_to_dict(row)
            entry_themes = set(entry["themes"])
            if theme_filter and not (entry_themes & theme_filter):
                continue
            score = _score_entry(entry, terms)
            if score <= 0:
                continue
            entry["score"] = score
            entry["snippet"] = _make_snippet(str(entry["text"]), terms)
            scored.append((score, entry))

        scored.sort(key=lambda item: item[0], reverse=True)
        return [entry for _, entry in scored[:limit]]

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                create table if not exists bank_entries (
                    entry_id text primary key,
                    source_file text not null,
                    source_type text not null,
                    section text not null,
                    title text not null,
                    text text not null,
                    themes_json text not null,
                    entity_type text not null,
                    usage_type text not null,
                    source_page text not null,
                    metadata_json text not null,
                    content_hash text not null,
                    updated_at text not null
                )
                """
            )
            conn.execute("create index if not exists idx_bank_entries_source on bank_entries(source_file)")
            conn.execute("create index if not exists idx_bank_entries_type on bank_entries(entity_type)")


def _row_to_dict(row: sqlite3.Row) -> dict[str, object]:
    return {
        "entry_id": row["entry_id"],
        "source_file": row["source_file"],
        "source_type": row["source_type"],
        "section": row["section"],
        "title": row["title"],
        "text": row["text"],
        "themes": json.loads(row["themes_json"] or "[]"),
        "entity_type": row["entity_type"],
        "usage_type": row["usage_type"],
        "source_page": row["source_page"],
        "metadata": json.loads(row["metadata_json"] or "{}"),
    }


def _query_terms(query: str) -> list[str]:
    known_terms = [
        "微众银行",
        "深圳前海微众银行",
        "微业贷",
        "微粒贷",
        "微贸贷",
        "国补商户",
        "微业贷国补商户专享贷款",
        "小微企业",
        "普惠金融",
        "科技金融",
        "数字金融",
        "数字银行",
        "人工智能",
        "大模型",
        "智能体",
        "AI",
        "消费者权益",
        "消保",
        "反诈",
        "无障碍",
        "绿色金融",
        "新能源汽车",
        "科创企业",
        "种子贷",
        "战新未来产业贷",
        "科技创新专项担保",
        "银税互动",
        "首贷",
        "稳外贸",
        "促消费",
        "乡村振兴",
    ]
    terms = [term for term in known_terms if term in query]
    terms.extend(token for token in re.split(r"[\s,，、;；。:：]+", query) if len(token.strip()) >= 2)
    terms = list(dict.fromkeys(term.strip() for term in terms if term.strip()))
    if "消费" in terms and any(term in terms for term in ("促消费", "国补商户", "新能源汽车")):
        terms = [term for term in terms if term != "消费"]
    return terms


def _score_entry(entry: dict[str, object], terms: list[str]) -> int:
    title = str(entry["title"])
    text = str(entry["text"])
    score = 0
    for term in terms:
        score += title.count(term) * 8
        score += text.count(term) * 3
    if score <= 0:
        return 0

    entity_type = str(entry.get("entity_type") or "")
    if entity_type in {"product", "capability", "metric", "standard_expression"}:
        score += 6
    if str(entry.get("usage_type") or "") == "writing_material":
        score += 4
    return score


def _make_snippet(text: str, terms: list[str], *, max_length: int = 220) -> str:
    first_index = min((text.find(term) for term in terms if term in text), default=0)
    start = max(first_index - 50, 0)
    snippet = text[start : start + max_length].strip()
    if start > 0:
        snippet = "..." + snippet
    if len(text) > start + max_length:
        snippet += "..."
    return snippet
