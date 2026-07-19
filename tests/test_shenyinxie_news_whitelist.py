from pathlib import Path

import pytest

from skills.shenyinxie_news.selection import MediaSource, MediaWhitelist


@pytest.fixture
def whitelist() -> MediaWhitelist:
    return MediaWhitelist(
        [
            MediaSource(
                name="人民网",
                category="中央级媒体",
                domains=["people.com.cn"],
                tier=1,
            ),
            MediaSource(
                name="中国人民银行",
                category="政府/监管",
                domains=["pbc.gov.cn"],
                tier=1,
            ),
            MediaSource(
                name="深圳特区报",
                category="深圳主流党媒",
                domains=["sztqb.sznews.com"],
                tier=2,
            ),
        ]
    )


def test_whitelist_allows_exact_domain(whitelist):
    assert whitelist.is_allowed("https://people.com.cn/article/1")
    info = whitelist.media_info("https://people.com.cn/article/1")
    assert info["name"] == "人民网"
    assert info["tier"] == 1


def test_whitelist_allows_subdomain(whitelist):
    assert whitelist.is_allowed("https://finance.people.com.cn/article/1")
    info = whitelist.media_info("https://finance.people.com.cn/article/1")
    assert info["name"] == "人民网"


def test_whitelist_allows_www_prefix(whitelist):
    assert whitelist.is_allowed("https://www.people.com.cn/article/1")


def test_whitelist_rejects_fake_domain_suffix(whitelist):
    assert not whitelist.is_allowed("https://fakepeople.com.cn/article/1")


def test_whitelist_rejects_own_channel(whitelist):
    assert not whitelist.is_allowed("https://www.webank.com/news/1")
    assert not whitelist.is_allowed("https://mp.weixin.qq.com/s/xxx")


def test_whitelist_loads_from_yaml():
    path = Path(__file__).resolve().parents[1] / "skills" / "shenyinxie_news" / "media_sources.yaml"
    whitelist = MediaWhitelist.from_yaml(path)
    assert whitelist.is_allowed("https://www.people.com.cn/article/1")
    assert whitelist.is_allowed("https://finance.qq.com/a/1")
    assert whitelist.is_allowed("https://www.cnr.cn/jrpd/1")
    assert whitelist.is_allowed("https://www.nfnews.com/content/1.html")
    assert whitelist.is_allowed("https://app.financialnews.com.cn/detailArticle/1.html")
    assert whitelist.is_allowed("https://www.cebnet.com.cn/20260701/1.html")
    assert whitelist.is_allowed("https://finance.ce.cn/bank12/scroll/1.html")
    assert whitelist.is_allowed("https://xxsb.gz-cmc.com/pages/1.html")
    assert whitelist.is_allowed("https://finance.ynet.com/2026/06/16/1.html")
    assert whitelist.is_allowed("https://m.hexun.com/tech/1.html")
    assert whitelist.is_allowed("https://www.hkcd.com.hk/hkcdweb/content/1.html")
    assert whitelist.is_allowed("https://m.pedaily.cn/99discoveries/1")
    assert whitelist.is_allowed("https://www.dotdotnews.com/a/202606/24/1.html")
    assert whitelist.is_allowed("https://www.dutenews.com/n/article/1")
    assert not whitelist.is_allowed("https://www.webank.com/news/1")
    assert not whitelist.is_allowed("https://baijiahao.baidu.com/s?id=1")


def test_whitelist_prefers_lower_tier_on_duplicate_domain():
    sources = [
        MediaSource(name="低权威", category="测试", domains=["example.com"], tier=2),
        MediaSource(name="高权威", category="测试", domains=["example.com"], tier=1),
    ]
    whitelist = MediaWhitelist(sources)
    info = whitelist.media_info("https://example.com/a")
    assert info["name"] == "高权威"
    assert info["tier"] == 1
