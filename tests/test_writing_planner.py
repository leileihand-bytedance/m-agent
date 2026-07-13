from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from skills.writing_planner import build_direct_report_plan


def test_build_direct_report_plan_prefers_policy_background_and_key_data():
    plan = build_direct_report_plan(
        instruction="请根据材料写一篇直报，突出稳外贸。",
        materials=[
            {
                "title": "微众银行创新外贸贷助力稳外贸",
                "text": (
                    "2026年以来，我国外贸稳中有进。"
                    "微众银行联合合作伙伴推出“外贸贷”，"
                    "企业无需提供抵质押物，即可获得最高1000万元、期限最高3年的信用贷款。"
                    "上线以来，已为数百户外贸小微企业提供近5亿元信贷资金支持。"
                ),
                "url": "https://example.com/foreign-trade",
                "source": "user_url",
            },
            {
                "title": "关于做好稳外贸工作的通知",
                "text": "政策摘录：加大对外贸企业融资支持力度。",
                "url": "https://www.gov.cn/policy",
                "source": "policy_knowledge",
                "category": "policy_original",
            },
        ],
    )

    assert "文体：直报" in plan
    assert "开头策略：政策背景型" in plan
    assert "稳外贸" in plan
    assert "1000万元" in plan
    assert "近5亿元" in plan


def test_build_direct_report_plan_adds_case_style_guidance_for_typical_case():
    plan = build_direct_report_plan(
        instruction="请根据材料写一篇直报。",
        materials=[
            {
                "title": "严厉打击金融黑灰产，微众银行案件入选湖北高院典型案例",
                "text": (
                    "日前，微众银行案件入选湖北高院典型案例。"
                    "在公安部、国家金融监督管理总局联合部署开展新一轮金融领域黑灰产违法犯罪集群打击工作背景下，"
                    "该案例有望为同类案件审理提供参考范式。"
                    "长期以来，金融黑灰产恶意混淆金融消费者视听，严重侵害消费者合法权益。"
                ),
                "url": "https://example.com/case",
                "source": "user_url",
            }
        ],
    )

    assert "案例类型：典型案例/外部认可型" in plan
    assert "政策使用方式：" in plan
    assert "正文骨架：" in plan
    assert "有望为同类案件审理提供参考" in plan


def test_build_direct_report_plan_adds_activity_style_guidance_for_campaign_material():
    plan = build_direct_report_plan(
        instruction="请根据材料写一篇直报。",
        materials=[
            {
                "title": "微众银行深入开展2026年普及金融知识万里行活动",
                "text": (
                    "2026年普及金融知识万里行活动启动以来，微众银行策划开展一系列金融消费者教育宣传活动。"
                    "活动期间累计开展各类宣教超40次，覆盖超3000万人次。"
                    "针对两司两员等新业态群体，定制发布风险提示。"
                ),
                "url": "https://example.com/campaign",
                "source": "user_url",
            }
        ],
    )

    assert "案例类型：活动开展型" in plan
    assert "宣教超40次" in plan
    assert "覆盖超3000万人次" in plan


def test_build_direct_report_plan_locks_mainline_for_special_service_case():
    plan = build_direct_report_plan(
        instruction="请根据材料写一篇直报。",
        materials=[
            {
                "title": "微众银行持续完善无障碍金融服务",
                "text": (
                    "微众银行围绕听障客户、老年客户等特殊群体需求，持续完善无障碍金融服务机制。"
                    "微粒贷已建立覆盖申请、审核全流程的远程金融服务体系，"
                    "微众银行App爸妈版累计服务老年客户超129万人次。"
                ),
                "url": "https://example.com/accessibility",
                "source": "user_url",
            },
            {
                "title": "金融消费者权益保护相关政策",
                "text": "政策摘录：提升特殊群体金融服务可得性。",
                "url": "https://www.nfra.gov.cn/consumer",
                "source": "policy_knowledge",
            },
        ],
    )

    assert "案例类型：专项服务型" in plan
    assert "补充材料使用边界：" in plan
    assert "不得据此引入用户素材未出现的新业务线、产品、做法或数据" in plan
    assert "开头策略：政策背景型" in plan
    assert "正文骨架：政策号召或现实需求 -> 微众行动/具体举措 -> 成果成效 -> 下一步安排。" in plan


def test_build_direct_report_plan_recenters_multi_party_material_on_webank():
    plan = build_direct_report_plan(
        instruction="请根据材料写一篇直报。",
        materials=[
            {
                "title": "名单制识别 批量化担保，微众银行联合兴业担保为深圳“外贸贷”添上数字之翼",
                "text": (
                    "近日，深圳市融担基金面向具备真实进出口业务的中小微企业推出政策性专项产品“外贸贷”。"
                    "微众银行携手兴业担保，以名单制识别、批量化担保方式，为符合条件的外贸小微企业提供融资支持。"
                    "企业无需提供抵质押物，即可申请最高1000万元、期限最高3年的信用贷款。"
                    "上线以来，已支持一批外贸企业获得融资。"
                ),
                "url": "https://example.com/foreign-trade",
                "source": "user_url",
            }
        ],
    )

    assert "主体要求：全文以微众银行为组织中心" in plan
    assert "合作方、政府基金、平台、活动和媒体表述只作背景或协同支撑，不喧宾夺主。" in plan
    assert "建议标题方向：标题以“微众银行+核心举措/机制+结果/作用”组织" in plan
    assert "避免沿用外部媒体标题或他方口径" in plan
    assert "核心事件：微众银行" in plan
    assert "正文按“政策/场景背景 -> 微众银行做了什么 -> 取得什么成效 -> 下一步怎么做”展开" in plan


def test_build_direct_report_plan_does_not_prioritize_single_enterprise_case():
    plan = build_direct_report_plan(
        instruction="请根据材料写一篇直报。",
        materials=[
            {
                "title": "微众银行联合兴业担保推出“外贸贷” 支持深圳外贸小微企业稳订单拓市场",
                "text": (
                    "在稳外贸政策持续推进背景下，微众银行携手兴业担保，为深圳外贸中小微企业提供融资支持。"
                    "业务率先在深圳龙岗区实现规模化落地，上线一个多月以来，已服务区内20家小微外贸企业，累计放款近1000万元。"
                    "龙岗区一家电子消费品出口企业在微众银行全线上流程支持下快速获批165万元授信，及时补足备货流动资金。"
                ),
                "url": "https://example.com/foreign-trade",
                "source": "user_url",
            }
        ],
    )

    assert "个案使用方式：单个企业受益案例如需使用，只能作为一句辅助例证，不单独成段，不作为标题或主线。" in plan
    assert "20家小微外贸企业" in plan
    assert "近1000万元" in plan
    assert "165万元授信" not in plan


def test_build_direct_report_plan_uses_comprehensive_progress_archetype_for_roundup_material():
    plan = build_direct_report_plan(
        instruction="请根据材料写一篇直报。",
        materials=[
            {
                "title": "深耕产业金融、践行社会公益，微众银行微业贷与小微企业共成长",
                "text": (
                    "2021年，微众银行围绕普惠金融、产业金融和社会公益持续发力。"
                    "截至2021年末，微业贷已累计申请企业超240万家，累计授信超1万亿元。"
                    "围绕制造业、绿色、农业等重点领域，微众银行不断丰富数字产业金融服务。"
                    "其中，近期推出企业活期+，为企业客户提供流动资金管理服务。"
                    "此外，微众银行还持续开展社会公益项目，支持乡村振兴和教育帮扶。"
                ),
                "url": "https://example.com/roundup",
                "source": "user_url",
            }
        ],
    )

    core_event_line = next(line for line in plan.splitlines() if line.startswith("核心事件："))

    assert "案例类型：综合进展型" in plan
    assert "主线限定：这类材料允许围绕一个总主题展开，主体分 2-3 个并列板块承接" in plan
    assert "标题以“微众银行+总主题+阶段性成效/综合进展”组织" in plan
    assert "企业活期+" not in core_event_line


def test_build_direct_report_plan_adds_transition_and_elevated_ending_guidance():
    plan = build_direct_report_plan(
        instruction="请根据材料写一篇直报，突出普惠金融。",
        materials=[
            {
                "title": "微众银行持续提升小微企业金融服务可得性",
                "text": (
                    "党中央、国务院高度重视普惠金融工作，多次作出部署。"
                    "微众银行持续优化面向小微企业的线上金融服务机制。"
                    "截至2026年6月，已为一批小微企业提供融资支持。"
                ),
                "url": "https://example.com/inclusive-finance",
                "source": "user_url",
            }
        ],
    )

    assert "衔接要求：" in plan
    assert "在此背景下" in plan
    assert "微众银行积极响应" in plan
    assert "段落推进：各段之间按“背景/部署 -> 微众响应/动作 -> 成效 -> 下一步”递进" in plan
    assert "结尾抬升：先写微众银行下一步安排" in plan
    assert "提升小微企业融资可得性" in plan
