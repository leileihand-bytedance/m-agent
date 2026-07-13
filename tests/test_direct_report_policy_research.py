from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.platform.tools import ToolGateway
from skills.direct_report.policy_research import research_direct_report_policy


def test_policy_research_selects_small_micro_policy_from_local_store():
    gateway = ToolGateway(
        allowed_tools=("policy_search",),
        tools={
            "policy_search": lambda query, limit=5, category=None: [
                {
                    "title": "关于提升小微企业金融服务质效的通知",
                    "snippet": "提升小微企业金融服务质效，优化融资供给，支持普惠金融发展。",
                    "url": "https://www.nfra.gov.cn/small-micro",
                    "source": "nfra",
                    "category": "policy_original",
                    "publish_date": "2026-05-18",
                }
            ]
        },
    )

    result = research_direct_report_policy(
        instruction="根据材料写直报",
        materials=[
            {
                "title": "微众银行优化小微企业融资服务",
                "text": "微众银行通过数字化方式提升小微企业融资服务效率，扩大普惠金融覆盖面。",
                "url": "https://example.com/news",
            }
        ],
        tools=gateway,
    )

    assert result.theme_id == "small_micro"
    assert result.use_policy is True
    assert result.selected_policy is not None
    assert result.selected_policy["title"] == "关于提升小微企业金融服务质效的通知"
    assert "在此背景下，微众银行" in result.bridge_guidance


def test_policy_research_selects_tech_innovation_policy_from_local_store():
    gateway = ToolGateway(
        allowed_tools=("policy_search",),
        tools={
            "policy_search": lambda query, limit=5, category=None: [
                {
                    "title": "关于做好科技金融大文章的通知",
                    "snippet": "引导金融资源更好服务科技创新和科技型企业发展。",
                    "url": "https://www.nfra.gov.cn/tech-finance",
                    "source": "nfra",
                    "category": "policy_original",
                    "publish_date": "2026-04-11",
                }
            ]
        },
    )

    result = research_direct_report_policy(
        instruction="根据材料写直报",
        materials=[
            {
                "title": "微众银行提升科创企业服务能力",
                "text": "微众银行围绕科技创新企业融资需求优化数字化服务机制，支持科创企业发展。",
                "url": "https://example.com/news",
            }
        ],
        tools=gateway,
    )

    assert result.theme_id == "tech_innovation"
    assert result.use_policy is True
    assert result.selected_policy is not None
    assert result.selected_policy["title"] == "关于做好科技金融大文章的通知"


def test_policy_research_rejects_weak_policy_and_returns_direct_topic():
    gateway = ToolGateway(
        allowed_tools=("policy_search",),
        tools={
            "policy_search": lambda query, limit=5, category=None: [
                {
                    "title": "国务院关于促进消费的若干意见",
                    "snippet": "着力扩大内需，促进消费。",
                    "url": "https://www.gov.cn/consumption",
                    "source": "govcn",
                    "category": "policy_original",
                    "publish_date": "2026-01-01",
                }
            ]
        },
    )

    result = research_direct_report_policy(
        instruction="根据材料写直报",
        materials=[
            {
                "title": "微众银行优化小微企业融资服务",
                "text": "微众银行通过数字化方式提升小微企业融资服务效率，扩大普惠金融覆盖面。",
                "url": "https://example.com/news",
            }
        ],
        tools=gateway,
    )

    assert result.theme_id == "small_micro"
    assert result.use_policy is False
    assert result.reason == "no_qualified_policy"
    assert result.selected_policy is None


def test_policy_research_skips_unsupported_theme():
    gateway = ToolGateway(
        allowed_tools=("policy_search",),
        tools={"policy_search": lambda query, limit=5, category=None: []},
    )

    result = research_direct_report_policy(
        instruction="根据材料写直报",
        materials=[
            {
                "title": "微众银行绿色金融服务取得进展",
                "text": "微众银行围绕绿色转型需求持续优化相关服务。",
                "url": "https://example.com/news",
            }
        ],
        tools=gateway,
    )

    assert result.theme_id is None
    assert result.use_policy is False
    assert result.reason == "unsupported_theme"
    assert result.selected_policy is None


def test_policy_research_prefers_finance_policy_over_regional_tech_approval():
    gateway = ToolGateway(
        allowed_tools=("policy_search",),
        tools={
            "policy_search": lambda query, limit=5, category=None: [
                {
                    "title": "国务院关于同意河北雄安高新技术产业开发区升级为国家高新技术产业开发区的批复",
                    "snippet": "因地制宜发展新质生产力，推动科技创新和产业创新深度融合。",
                    "url": "https://www.gov.cn/gaoxinqu",
                    "source": "govcn",
                    "category": "policy_original",
                    "publish_date": "2026-02-13",
                },
                {
                    "title": "国家金融监督管理总局关于做好科技金融大文章的通知",
                    "snippet": "引导金融资源更好服务科技创新和科技型企业发展，提升科技金融服务能力。",
                    "url": "https://www.nfra.gov.cn/tech-finance",
                    "source": "nfra",
                    "category": "policy_original",
                    "publish_date": "2026-04-11",
                },
            ]
        },
    )

    result = research_direct_report_policy(
        instruction="根据材料写直报",
        materials=[
            {
                "title": "微众银行提升科技创新企业服务能力",
                "text": "微众银行围绕科技型企业和专精特新企业融资需求优化服务机制，提升科技金融服务能力。",
                "url": "https://example.com/news",
            }
        ],
        tools=gateway,
    )

    assert result.use_policy is True
    assert result.selected_policy is not None
    assert result.selected_policy["title"] == "国家金融监督管理总局关于做好科技金融大文章的通知"


def test_policy_research_prefers_overarching_small_micro_finance_policy():
    gateway = ToolGateway(
        allowed_tools=("policy_search",),
        tools={
            "policy_search": lambda query, limit=5, category=None: [
                {
                    "title": "国家税务总局  国家金融监督管理总局关于进一步深化和规范“银税互动”工作的通知",
                    "snippet": "发挥纳税缴费信用在普惠金融体系建设中的重要作用，支持民营和小微企业融资发展。",
                    "url": "https://www.nfra.gov.cn/yinshuihudong",
                    "source": "nfra",
                    "category": "policy_original",
                    "publish_date": "2026-04-02",
                },
                {
                    "title": "国家金融监督管理总局关于做好普惠金融大文章的指导意见",
                    "snippet": "提升小微企业金融服务质效，增强融资可得性，推动普惠金融高质量发展。",
                    "url": "https://www.nfra.gov.cn/inclusive-finance",
                    "source": "nfra",
                    "category": "policy_original",
                    "publish_date": "2026-05-18",
                },
            ]
        },
    )

    result = research_direct_report_policy(
        instruction="根据材料写直报",
        materials=[
            {
                "title": "微众银行优化小微企业融资服务",
                "text": "微众银行通过数字化方式提升小微企业融资服务效率，扩大普惠金融覆盖面。",
                "url": "https://example.com/news",
            }
        ],
        tools=gateway,
    )

    assert result.use_policy is True
    assert result.selected_policy is not None
    assert result.selected_policy["title"] == "国家金融监督管理总局关于做好普惠金融大文章的指导意见"


def test_policy_research_returns_no_policy_when_only_weak_tech_candidates_exist():
    gateway = ToolGateway(
        allowed_tools=("policy_search",),
        tools={
            "policy_search": lambda query, limit=5, category=None: [
                {
                    "title": "国务院关于同意河北雄安高新技术产业开发区升级为国家高新技术产业开发区的批复",
                    "snippet": "因地制宜发展新质生产力，推动科技创新和产业创新深度融合，积极开展重大科技项目。",
                    "url": "https://www.gov.cn/gaoxinqu",
                    "source": "govcn",
                    "category": "policy_original",
                    "publish_date": "2026-02-13",
                },
                {
                    "title": "国家金融监督管理总局关于银行业保险业人工智能安全开发应用的指导意见",
                    "snippet": "落实人工智能行动意见，推动人工智能科技创新与金融业务深度融合，引导金融领域人工智能应用健康发展。",
                    "url": "https://www.nfra.gov.cn/ai-safety",
                    "source": "nfra",
                    "category": "policy_original",
                    "publish_date": "2026-06-18",
                },
            ]
        },
    )

    result = research_direct_report_policy(
        instruction="根据材料写直报",
        materials=[
            {
                "title": "微众银行提升科技创新企业服务能力",
                "text": "微众银行围绕科技型企业和专精特新企业融资需求优化服务机制，提升科技金融服务能力。",
                "url": "https://example.com/news",
            }
        ],
        tools=gateway,
    )

    assert result.theme_id == "tech_innovation"
    assert result.use_policy is False
    assert result.reason == "no_qualified_policy"
    assert result.selected_policy is None
