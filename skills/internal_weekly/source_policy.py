from __future__ import annotations

from datetime import date
from urllib.parse import urlparse

from skills.internal_weekly.dates import parse_flexible_date
from skills.internal_weekly.schema import WebCandidate
from skills.internal_weekly.source_registry import registered_domains


ALLOWED_DOMAINS = {
    "gov.cn",
    "people.com.cn",
    "xinhuanet.com",
    "pbc.gov.cn",
    "nfra.gov.cn",
    "csrc.gov.cn",
    "safe.gov.cn",
    "sse.com.cn",
    "szse.cn",
    "cnindex.com.cn",
    "hkex.com.hk",
    "hsi.com.hk",
    "spglobal.com",
    "nasdaq.com",
    "cs.com.cn",
    "cnstock.com",
    "stcn.com",
    "21jingji.com",
    "yicai.com",
    "caixin.com",
    "nbd.com.cn",
    "eastmoney.com",
    "apnews.com",
    "cnfin.com",
    "sfccn.com",
    "bjd.com.cn",
    "bis.org",
    "imf.org",
    "worldbank.org",
    "cf40.org.cn",
    "federalreserve.gov",
    "ecb.europa.eu",
} | set(registered_domains())
RESEARCH_DOMAINS = {
    "bis.org",
    "imf.org",
    "worldbank.org",
    "cf40.org.cn",
    "federalreserve.gov",
    "ecb.europa.eu",
    "fsb.org",
    "bankofengland.co.uk",
}


def hostname(url: str) -> str:
    return (urlparse(url).hostname or "").lower().removeprefix("www.")


def domain_allowed(url: str) -> bool:
    host = hostname(url)
    return any(host == domain or host.endswith(f".{domain}") for domain in ALLOWED_DOMAINS)


def is_research_source(url: str) -> bool:
    host = hostname(url)
    return any(host == domain or host.endswith(f".{domain}") for domain in RESEARCH_DOMAINS)


def date_in_period(value: str, period_start: date, period_end: date) -> bool:
    try:
        parsed = parse_flexible_date(value, default_year=period_end.year)
    except (TypeError, ValueError):
        return False
    return period_start <= parsed <= period_end


def candidate_allowed(
    candidate: WebCandidate,
    *,
    period_start: date | None = None,
    period_end: date | None = None,
    require_research: bool = False,
) -> tuple[bool, str]:
    if not domain_allowed(candidate.canonical_url or candidate.url):
        return False, "来源不在白名单"
    if len(candidate.body.strip()) < 20:
        return False, "正文过短"
    if require_research and not is_research_source(candidate.canonical_url or candidate.url):
        return False, "不是已登记的研究机构来源"
    if period_start and period_end and not date_in_period(
        candidate.publish_date, period_start, period_end
    ):
        return False, "发布日期不在统计期"
    return True, ""
