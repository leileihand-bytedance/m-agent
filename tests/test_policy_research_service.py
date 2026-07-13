from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.policy_knowledge.store import PolicyKnowledgeStore
from app.policy_research.service import research_policy_attachment


def test_research_policy_attachment_returns_primary_and_alternatives(tmp_path):
    db_path = tmp_path / "policies.sqlite3"
    store = PolicyKnowledgeStore(db_path)
    store.upsert_documents(
        [
            {
                "source": "nfra",
                "category": "policy_original",
                "item_id": "",
                "doc_id": "small-micro-1",
                "title": "关于提升小微企业金融服务质效的通知",
                "publish_date": "2026-07-01",
                "url": "https://www.nfra.gov.cn/p1",
                "text": "提升小微企业金融服务质效，优化融资供给，支持普惠金融发展。",
                "original_links": [],
                "metadata": {},
            },
            {
                "source": "govcn",
                "category": "policy_original",
                "item_id": "",
                "doc_id": "small-micro-2",
                "title": "国务院办公厅关于做好金融服务实体经济有关工作的通知",
                "publish_date": "2026-06-20",
                "url": "https://www.gov.cn/p2",
                "text": "支持小微企业发展，提升融资可得性，更好服务实体经济。",
                "original_links": [],
                "metadata": {},
            },
        ]
    )

    result = research_policy_attachment(
        user_instruction="请根据这条素材写简报",
        materials=[
            {
                "title": "微众银行推出微贸贷",
                "text": "微众银行围绕外贸小微企业推出微贸贷，支持稳订单拓市场。",
                "source": "user_text",
            }
        ],
        db_path=db_path,
        usage_profile="brief",
        limit=3,
    )

    assert result.should_attach_policy is True
    assert result.primary_policy is not None
    assert result.primary_policy.title == "关于提升小微企业金融服务质效的通知"
    assert len(result.alternative_policies) >= 1


def test_direct_report_profile_rejects_activity_material(tmp_path):
    result = research_policy_attachment(
        user_instruction="请写直报",
        materials=[
            {
                "title": "微众银行开展金融知识直播活动",
                "text": "围绕反诈和消保开展直播宣教活动。",
                "source": "user_text",
            }
        ],
        db_path=tmp_path / "policies.sqlite3",
        usage_profile="direct_report",
        limit=3,
    )

    assert result.should_attach_policy is False
    assert result.decision_reason == "unsupported_material_type"


def test_research_policy_attachment_skips_disabled_policy(tmp_path):
    db_path = tmp_path / "policies.sqlite3"
    store = PolicyKnowledgeStore(db_path)
    store.upsert_documents(
        [
            {
                "source": "nfra",
                "category": "policy_original",
                "item_id": "",
                "doc_id": "disabled-1",
                "title": "关于提升小微企业金融服务质效的通知",
                "publish_date": "2026-07-01",
                "url": "https://www.nfra.gov.cn/p1",
                "text": "提升小微企业金融服务质效。",
                "original_links": [],
                "metadata": {},
                "is_enabled": 0,
            }
        ]
    )

    result = research_policy_attachment(
        user_instruction="请写直报",
        materials=[
            {
                "title": "微众银行推出微业贷",
                "text": "支持小微企业融资。",
                "source": "user_text",
            }
        ],
        db_path=db_path,
        usage_profile="direct_report",
    )

    assert result.should_attach_policy is False
    assert result.decision_reason == "no_qualified_policy"
