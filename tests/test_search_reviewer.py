"""搜索增强审核测试."""
import asyncio
from app.review.search_reviewer import review_with_search, _is_news_title, _初步判断_需要_llm_复核
from app.review.parser import parse_docx
from app.review.search_tools import SearchResult


def test_is_news_title():
    """新闻标题判断。"""
    assert _is_news_title("习近平在二十届中央纪委五次全会上发表重要讲话") == True
    assert _is_news_title("中国人民银行宣布降准") == True
    assert _is_news_title("1月16日，国务院总理李强主持召开国务院常务会议...") == False
    assert _is_news_title("") == False
    assert _is_news_title("党政要闻") == False


def test_preliminary_review_logic():
    """初步判断逻辑测试。"""
    search_result = SearchResult(
        url="http://example.com",
        title="国务院常务会议研究经济工作",
        snippet="...",
        source="official"
    )
    # 正文包含标题关键词
    para = "国务院常务会议研究了当前经济形势..."
    assert _初步判断_需要_llm_复核(para, search_result, "STATE_COUNCIL") == False

    # 正文不包含标题关键词
    para2 = "金融监管总局召开了工作会议..."
    assert _初步判断_需要_llm_复核(para2, search_result, "STATE_COUNCIL") == True