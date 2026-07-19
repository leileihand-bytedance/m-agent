from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml


_REGISTRY_PATH = Path(__file__).resolve().parent / "references" / "source-registry.yaml"


@lru_cache(maxsize=1)
def load_source_registry() -> dict[str, object]:
    """读取内参周报自有信源登记表；运行时不依赖审核模块。"""
    payload = yaml.safe_load(_REGISTRY_PATH.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        raise ValueError("内参周报信源登记表格式无效")
    return payload


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
                    domain = str(entry.get("official_domain") or "").strip().lower()
                    if domain:
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
                    domain = str(entry.get("official_domain") or "").strip().lower()
                    if domain:
                        values.add(domain)
    return frozenset(values)
