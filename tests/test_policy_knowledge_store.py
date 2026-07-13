from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.policy_knowledge.store import PolicyKnowledgeStore


def test_policy_knowledge_store_upserts_and_searches_documents(tmp_path):
    store = PolicyKnowledgeStore(tmp_path / "policies.sqlite3")
    store.upsert_documents(
        [
            {
                "source": "nfra",
                "category": "policy_interpretation",
                "item_id": "917",
                "doc_id": "1001",
                "title": "关于做好小微企业金融服务工作的通知答记者问",
                "publish_date": "2026-05-19 18:35:07",
                "url": "https://www.nfra.gov.cn/cn/view/pages/ItemDetail.html?docId=1001&itemId=917&generaltype=0",
                "text": "金融机构要提升小微企业金融服务质效，强化首贷、续贷和信用贷款支持。",
                "original_links": [],
                "metadata": {"source_name": "政策解读"},
            },
            {
                "source": "nfra",
                "category": "regulatory_update",
                "item_id": "915",
                "doc_id": "2001",
                "title": "金融监管总局召开会议部署风险防控工作",
                "publish_date": "2026-06-08 17:10:41",
                "url": "https://www.nfra.gov.cn/cn/view/pages/ItemDetail.html?docId=2001&itemId=915&generaltype=0",
                "text": "会议研究部署金融风险防控、严监管强监管等近期重点工作。",
                "original_links": [],
                "metadata": {"source_name": "监管动态"},
            },
        ]
    )

    store.upsert_documents(
        [
            {
                "source": "nfra",
                "category": "policy_interpretation",
                "item_id": "917",
                "doc_id": "1001",
                "title": "关于做好小微企业金融服务工作的通知答记者问",
                "publish_date": "2026-05-19 18:35:07",
                "url": "https://www.nfra.gov.cn/cn/view/pages/ItemDetail.html?docId=1001&itemId=917&generaltype=0",
                "text": "更新后：提升小微企业金融服务质效。",
                "original_links": [],
                "metadata": {"source_name": "政策解读"},
            }
        ]
    )

    assert store.count_documents() == 2
    results = store.search("小微企业金融服务", limit=3)
    assert results[0]["doc_id"] == "1001"
    assert results[0]["source"] == "nfra"
    assert "小微企业" in results[0]["snippet"]


def test_policy_knowledge_store_returns_empty_results_for_blank_query(tmp_path):
    store = PolicyKnowledgeStore(tmp_path / "policies.sqlite3")

    assert store.search("   ") == []


def test_policy_knowledge_store_preserves_higher_value_category_on_duplicate_doc(tmp_path):
    store = PolicyKnowledgeStore(tmp_path / "policies.sqlite3")
    base_document = {
        "source": "nfra",
        "doc_id": "1001",
        "title": "人工智能指导意见答记者问",
        "publish_date": "2026-06-18 18:47:44",
        "text": "银行业保险业人工智能安全开发应用。",
        "original_links": [],
        "metadata": {},
    }

    store.upsert_documents(
        [
            {
                **base_document,
                "category": "policy_interpretation",
                "item_id": "917",
                "url": "https://www.nfra.gov.cn/policy",
            },
            {
                **base_document,
                "category": "policy_original",
                "item_id": "",
                "url": "https://www.nfra.gov.cn/original",
            },
            {
                **base_document,
                "category": "regulatory_update",
                "item_id": "915",
                "url": "https://www.nfra.gov.cn/update",
            },
        ]
    )

    result = store.search("人工智能 银行业保险业", limit=1)[0]
    assert result["category"] == "policy_original"
    assert result["item_id"] == ""
    assert result["url"] == "https://www.nfra.gov.cn/original"


def test_policy_knowledge_store_ranks_policy_original_before_interpretation(tmp_path):
    store = PolicyKnowledgeStore(tmp_path / "policies.sqlite3")
    store.upsert_documents(
        [
            {
                "source": "nfra",
                "category": "policy_interpretation",
                "item_id": "917",
                "doc_id": "1001",
                "title": "人工智能指导意见答记者问",
                "publish_date": "2026-06-18 18:47:44",
                "url": "https://www.nfra.gov.cn/interpretation",
                "text": "银行业保险业人工智能安全开发应用出台背景。",
                "original_links": [],
                "metadata": {},
            },
            {
                "source": "nfra",
                "category": "policy_original",
                "item_id": "",
                "doc_id": "3001",
                "title": "银行业保险业人工智能安全开发应用指导意见",
                "publish_date": "2026-06-18 18:48:00",
                "url": "https://www.nfra.gov.cn/original",
                "text": "银行业保险业人工智能安全开发应用正式监管要求。",
                "original_links": [],
                "metadata": {},
            },
        ]
    )

    results = store.search("人工智能 银行业保险业", limit=2)

    assert results[0]["category"] == "policy_original"
    assert results[0]["doc_id"] == "3001"


def test_policy_knowledge_store_searches_state_council_policy_topics(tmp_path):
    store = PolicyKnowledgeStore(tmp_path / "policies.sqlite3")
    store.upsert_documents(
        [
            {
                "source": "govcn",
                "category": "policy_original",
                "item_id": "",
                "doc_id": "6974607",
                "title": "国务院办公厅关于促进消费品以旧换新的通知",
                "publish_date": "2024-09-01",
                "url": "https://www.gov.cn/zhengce/zhengceku/202409/content_6974607.htm",
                "text": "加大消费品以旧换新政策支持力度，促进消费持续恢复。",
                "original_links": [],
                "metadata": {"source_name": "国务院政策文件库"},
            },
            {
                "source": "govcn",
                "category": "policy_original",
                "item_id": "",
                "doc_id": "6980001",
                "title": "国务院关于推动未来产业创新发展的意见",
                "publish_date": "2025-01-15",
                "url": "https://www.gov.cn/zhengce/zhengceku/202501/content_6980001.htm",
                "text": "培育发展人工智能、低空经济等未来产业。",
                "original_links": [],
                "metadata": {"source_name": "国务院政策文件库"},
            },
        ]
    )

    results = store.search("国务院 促进消费 实体经济", limit=2)

    assert results[0]["source"] == "govcn"
    assert results[0]["doc_id"] == "6974607"

    spaced_results = store.search("国务院 消费", limit=2)
    assert spaced_results[0]["doc_id"] == "6974607"


def test_policy_knowledge_store_skips_disabled_documents_in_search(tmp_path):
    store = PolicyKnowledgeStore(tmp_path / "policies.sqlite3")
    store.upsert_documents(
        [
            {
                "source": "nfra",
                "category": "policy_original",
                "item_id": "",
                "doc_id": "enabled-doc",
                "title": "关于提升小微企业金融服务质效的通知",
                "publish_date": "2026-07-02",
                "url": "https://www.nfra.gov.cn/enabled",
                "text": "提升小微企业金融服务质效。",
                "original_links": [],
                "metadata": {},
            },
            {
                "source": "nfra",
                "category": "policy_original",
                "item_id": "",
                "doc_id": "disabled-doc",
                "title": "关于进一步做好小微企业融资服务的通知",
                "publish_date": "2026-07-03",
                "url": "https://www.nfra.gov.cn/disabled",
                "text": "进一步做好小微企业融资服务。",
                "original_links": [],
                "metadata": {},
                "is_enabled": 0,
                "disabled_reason": "人工判定弱相关",
            },
        ]
    )

    results = store.search("小微企业融资服务", limit=5, category="policy_original")

    assert [item["doc_id"] for item in results] == ["enabled-doc"]


def test_policy_knowledge_store_preserves_governance_fields(tmp_path):
    store = PolicyKnowledgeStore(tmp_path / "policies.sqlite3")
    store.upsert_documents(
        [
            {
                "source": "govcn",
                "category": "policy_original",
                "item_id": "",
                "doc_id": "governed-doc",
                "title": "国务院办公厅关于促进服务消费高质量发展的意见",
                "publish_date": "2026-07-01",
                "url": "https://www.gov.cn/governed",
                "text": "促进服务消费高质量发展。",
                "original_links": [],
                "metadata": {},
                "theme_tags": ["consumption"],
                "region_tags": ["national"],
                "audience_tags": ["banks"],
                "source_weight": 7,
                "review_note": "优先用于简报政策背景。",
            }
        ]
    )

    document = store.list_documents(limit=1)[0]

    assert document["theme_tags"] == ["consumption"]
    assert document["region_tags"] == ["national"]
    assert document["audience_tags"] == ["banks"]
    assert document["source_weight"] == 7
    assert document["review_note"] == "优先用于简报政策背景。"
