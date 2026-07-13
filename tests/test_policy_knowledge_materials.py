from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.policy_knowledge.materials import infer_policy_intent


def test_infer_policy_intent_recognizes_foreign_trade_case_theme():
    result = infer_policy_intent(
        "微众银行联合合作伙伴推出外贸贷，基于真实出口订单、报关流水、外汇营收等数据，为外贸企业提供信用融资服务。"
    )

    assert "稳外贸和外贸金融支持" in result["theme_labels"]
    assert "外贸" in result["query"]


def test_infer_policy_intent_recognizes_cross_border_data_case_theme():
    result = infer_policy_intent(
        "微众银行探索跨境数据验证机制，推动数据要素高效便利安全跨境流动，助力释放数据要素价值。"
    )

    assert "数据要素与跨境数据流动" in result["theme_labels"]
    assert "跨境数据" in result["query"]


def test_infer_policy_intent_recognizes_green_finance_case_theme():
    result = infer_policy_intent(
        "微众银行探索普惠金融与绿色金融融合发展，围绕双碳目标构建小微企业绿色信贷识别体系。"
    )

    assert "绿色金融与绿色转型" in result["theme_labels"]
    assert "绿色金融" in result["query"]


def test_infer_policy_intent_recognizes_tech_innovation_case_theme():
    result = infer_policy_intent(
        "微众银行推出科技初创通，依托征信平台与数据模型支持科创企业稳健成长，提升科技金融服务质效。"
    )

    assert "科技创新和科技金融" in result["theme_labels"]
    assert "科技金融" in result["query"]
