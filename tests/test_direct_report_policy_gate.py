from skills.direct_report.policy_research import DirectReportPolicyResearch
from skills.writing_planner import build_direct_report_plan, should_add_direct_report_policy_materials


def test_case_or_lawsuit_material_uses_direct_lead_and_skips_policy_materials():
    materials = [
        {
            "title": "微众银行商标侵权案获法院判赔",
            "text": (
                "近日，湖北省武汉市中级人民法院对微众银行起诉的四家关联企业商标侵权"
                "及不正当竞争案件作出判决，判赔金额合计280万元。微众银行通过除尘行动"
                "持续打击金融黑灰产。"
            ),
        }
    ]

    plan = build_direct_report_plan("请写直报", materials)

    assert "案例类型：典型案例/外部认可型" in plan
    assert "开头策略：直入主题型" in plan
    assert "默认不写具体政策名称" in plan
    assert should_add_direct_report_policy_materials("请写直报", materials) is False


def test_activity_material_uses_direct_lead_and_skips_policy_materials():
    materials = [
        {
            "title": "微众银行开展金融知识直播活动",
            "text": "6月23日，微众银行承办金融知识直播活动，围绕非法集资、网络诈骗等风险开展提示。",
        }
    ]

    plan = build_direct_report_plan("请写直报", materials)

    assert "案例类型：活动开展型" in plan
    assert "开头策略：直入主题型" in plan
    assert "默认不写具体政策名称" in plan
    assert should_add_direct_report_policy_materials("请写直报", materials) is False


def test_product_or_mechanism_material_can_add_policy_materials():
    materials = [
        {
            "title": "微众银行推出微贸贷",
            "text": (
                "微众银行联合多方推出专属于外贸小微企业的纯线上无抵押信贷产品微贸贷，"
                "支持深圳外贸小微企业稳订单拓市场。"
            ),
        }
    ]

    plan = build_direct_report_plan("请写直报", materials)

    assert "案例类型：产品支持型" in plan
    assert "开头策略：政策背景型" in plan
    assert should_add_direct_report_policy_materials("请写直报", materials) is True


def test_shared_policy_theme_label_is_rendered_instead_of_none():
    materials = [
        {
            "title": "微众银行服务外贸小微企业",
            "text": "微众银行通过微贸贷支持外贸小微企业稳订单、拓市场。",
        }
    ]
    policy_research = DirectReportPolicyResearch(
        theme_id="foreign_trade",
        theme_label="稳外贸金融服务",
        use_policy=True,
        reason="matched",
        selected_policy={"title": "支持外贸稳定发展的政策", "text": "政策摘录"},
        lead_guidance="开头建议",
        bridge_guidance="衔接建议",
        closing_guidance="结尾建议",
    )

    plan = build_direct_report_plan("请写直报", materials, policy_research=policy_research)

    assert "本稿可挂稳外贸金融服务相关政策" in plan
    assert "本稿可挂None" not in plan
