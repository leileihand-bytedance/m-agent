from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

import yaml


_REGISTRY_PATH = Path(__file__).resolve().parent / "references" / "source-registry.yaml"


@lru_cache(maxsize=1)
def load_source_registry() -> dict[str, object]:
    """读取内参周报自有信源登记表；运行时不依赖审核模块。"""
    payload = yaml.safe_load(_REGISTRY_PATH.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        raise ValueError("内参周报信源登记表格式无效")
    return payload


def _host_from_url(url: str) -> str:
    return (urlparse(url).hostname or "").lower().removeprefix("www.")


def _host_matches_domain(host: str, domain: str, match: str = "suffix") -> bool:
    if not host or not domain:
        return False
    return host == domain if match == "exact" else (
        host == domain or host.endswith(f".{domain}")
    )


def _peer_entry_domains(entry: dict[str, object]) -> tuple[str, ...]:
    values: list[str] = []
    official_domain = str(entry.get("official_domain") or "").strip().lower()
    if official_domain:
        values.append(official_domain)
    additional_domains = entry.get("additional_domains", [])
    if isinstance(additional_domains, list):
        values.extend(
            str(domain).strip().lower()
            for domain in additional_domains
            if str(domain).strip()
        )
    source_urls = entry.get("source_urls", [])
    if isinstance(source_urls, list):
        values.extend(
            _host_from_url(str(url).strip())
            for url in source_urls
            if _host_from_url(str(url).strip())
        )
    return tuple(dict.fromkeys(values))


def peer_entities(category: str) -> frozenset[str]:
    payload = load_source_registry()
    peer_groups = payload.get("peer_entities", {})
    if not isinstance(peer_groups, dict):
        return frozenset()
    entries = peer_groups.get(category, [])
    values: set[str] = set()
    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or "").strip()
        if name:
            values.add(name)
        aliases = entry.get("aliases", [])
        if isinstance(aliases, list):
            values.update(str(alias).strip() for alias in aliases if str(alias).strip())
    return frozenset(values)


def peer_query_names(category: str) -> tuple[str, ...]:
    payload = load_source_registry()
    peer_groups = payload.get("peer_entities", {})
    if not isinstance(peer_groups, dict):
        return ()
    entries = peer_groups.get(category, [])
    if not isinstance(entries, list):
        return ()
    return tuple(
        str(entry.get("name") or "").strip()
        for entry in entries
        if isinstance(entry, dict) and str(entry.get("name") or "").strip()
    )


def section_domain_rules(section: str) -> tuple[tuple[str, str], ...]:
    """返回板块自己的域名匹配规则；同业同时包含已登记机构官网。"""
    payload = load_source_registry()
    values: list[tuple[str, str]] = []
    section_sources = payload.get("section_sources", {})
    if isinstance(section_sources, dict):
        entries = section_sources.get(section, [])
        for entry in entries if isinstance(entries, list) else []:
            if not isinstance(entry, dict):
                continue
            domain = str(entry.get("domain") or "").strip().lower()
            match = str(entry.get("match") or "suffix").strip().lower()
            if domain:
                values.append((domain, "exact" if match == "exact" else "suffix"))
    if section == "同业动向":
        peer_groups = payload.get("peer_entities", {})
        if isinstance(peer_groups, dict):
            for entries in peer_groups.values():
                for entry in entries if isinstance(entries, list) else []:
                    if not isinstance(entry, dict):
                        continue
                    for domain in _peer_entry_domains(entry):
                        values.append((domain, "suffix"))
    return tuple(dict.fromkeys(values))


def section_source_entry_urls(section: str) -> tuple[str, ...]:
    """返回板块登记的固定发现入口，供检索查询显式引用。"""
    payload = load_source_registry()
    section_sources = payload.get("section_sources", {})
    if not isinstance(section_sources, dict):
        return ()
    entries = section_sources.get(section, [])
    values: list[str] = []
    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, dict):
            continue
        entry_url = str(entry.get("entry_url") or "").strip()
        if entry_url:
            values.append(entry_url)
    return tuple(dict.fromkeys(values))


def section_source_feed_urls(section: str) -> tuple[str, ...]:
    """返回板块登记的官方结构化列表地址。"""
    return tuple(
        dict.fromkeys(
            spec["feed_url"]
            for spec in section_source_feed_specs(section)
            if spec.get("feed_url")
        )
    )


def section_source_feed_specs(section: str) -> tuple[dict[str, str], ...]:
    """返回固定信源的采集参数；业务层只解释登记字段，不直接访问网络。"""
    payload = load_source_registry()
    section_sources = payload.get("section_sources", {})
    if not isinstance(section_sources, dict):
        return ()
    entries = section_sources.get(section, [])
    values: list[dict[str, str]] = []
    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, dict):
            continue
        feed_url = str(entry.get("feed_url") or "").strip()
        if not feed_url:
            continue
        spec = {
            str(key): str(value).strip()
            for key, value in entry.items()
            if value is not None and not isinstance(value, (dict, list)) and str(value).strip()
        }
        values.append(spec)
    return tuple(values)


def market_observation_topic_specs() -> tuple[dict[str, object], ...]:
    """返回市场观察的主题化检索配置，避免查询规则散落在工作流代码中。"""
    payload = load_source_registry()
    topics = payload.get("market_observation_topics", [])
    if not isinstance(topics, list):
        return ()
    values: list[dict[str, object]] = []
    for topic in topics:
        if not isinstance(topic, dict):
            continue
        topic_id = str(topic.get("id") or "").strip()
        name = str(topic.get("name") or "").strip()
        templates = topic.get("query_templates", [])
        if not topic_id or not name or not isinstance(templates, list):
            continue
        query_templates = tuple(
            str(template).strip()
            for template in templates
            if str(template).strip()
        )
        if not query_templates:
            continue
        values.append(
            {
                "id": topic_id,
                "name": name,
                "query_templates": query_templates,
            }
        )
    return tuple(values)


def peer_activity_topic_specs() -> tuple[dict[str, object], ...]:
    """返回同业动向按机构类型配置的检索主题。"""
    payload = load_source_registry()
    topics = payload.get("peer_activity_topics", [])
    if not isinstance(topics, list):
        return ()
    values: list[dict[str, object]] = []
    for topic in topics:
        if not isinstance(topic, dict):
            continue
        topic_id = str(topic.get("id") or "").strip()
        name = str(topic.get("name") or "").strip()
        category = str(topic.get("category") or "").strip()
        templates = topic.get("query_templates", [])
        try:
            chunk_size = int(topic.get("chunk_size") or 0)
        except (TypeError, ValueError):
            chunk_size = 0
        if (
            not topic_id
            or not name
            or not category
            or chunk_size <= 0
            or not isinstance(templates, list)
        ):
            continue
        query_templates = tuple(
            str(template).strip()
            for template in templates
            if str(template).strip()
        )
        if not query_templates:
            continue
        values.append(
            {
                "id": topic_id,
                "name": name,
                "category": category,
                "chunk_size": chunk_size,
                "query_templates": query_templates,
            }
        )
    return tuple(values)


def section_source_tier(url: str, section: str) -> str | None:
    """返回来源在指定板块登记的层级，供同分候选优先选择官方原始来源。"""
    host = _host_from_url(url)
    if not host:
        return None
    payload = load_source_registry()
    section_sources = payload.get("section_sources", {})
    if not isinstance(section_sources, dict):
        return None
    entries = section_sources.get(section, [])
    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, dict):
            continue
        domain = str(entry.get("domain") or "").strip().lower()
        match = str(entry.get("match") or "suffix").strip().lower()
        if _host_matches_domain(host, domain, match):
            tier = str(entry.get("tier") or "").strip().lower()
            return tier or None
    return None


def peer_source_tier(url: str) -> str | None:
    """机构官网和投资者关系页为一级，同业板块登记媒体按其层级返回。"""
    host = _host_from_url(url)
    if not host:
        return None
    payload = load_source_registry()
    peer_groups = payload.get("peer_entities", {})
    if isinstance(peer_groups, dict):
        for entries in peer_groups.values():
            for entry in entries if isinstance(entries, list) else []:
                if not isinstance(entry, dict):
                    continue
                if any(
                    _host_matches_domain(host, domain)
                    for domain in _peer_entry_domains(entry)
                ):
                    return "primary"
    return section_source_tier(url, "同业动向")


def registered_domains() -> frozenset[str]:
    payload = load_source_registry()
    values: set[str] = set()
    section_sources = payload.get("section_sources", {})
    if isinstance(section_sources, dict):
        for entries in section_sources.values():
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if isinstance(entry, dict):
                    domain = str(entry.get("domain") or "").strip().lower()
                    if domain:
                        values.add(domain)
    peer_groups = payload.get("peer_entities", {})
    if isinstance(peer_groups, dict):
        for entries in peer_groups.values():
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if isinstance(entry, dict):
                    for domain in _peer_entry_domains(entry):
                        values.add(domain)
    return frozenset(values)
