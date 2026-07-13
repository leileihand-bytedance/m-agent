"""原文核对测试."""
from app.review.citation_verifier import extract_original_text, VerificationResult


def test_extract_original_text_with_citation():
    """有原文引用时，提取原文部分。"""
    para = "中国人民银行宣布降准0.25个百分点。原文:为支持实体经济发展，中国人民银行决定于2026年6月15日起，下调金融机构存款准备金率0.25个百分点。"
    result = extract_original_text(para)
    assert result is not None
    assert "为支持实体经济发展" in result
    assert "原文:" not in result


def test_extract_original_text_without_citation():
    """无原文引用时，返回 None。"""
    para = "中国人民银行宣布降准0.25个百分点。"
    result = extract_original_text(para)
    assert result is None


def test_extract_original_text_multiple_colon():
    """正文中有冒号但不是原文引用。"""
    para = "国务院新闻办公室主任王晓明表示：原文:国务院新闻办公室今日发布..."
    result = extract_original_text(para)
    assert result is not None
    assert result.startswith("国务院新闻办公室今日发布")