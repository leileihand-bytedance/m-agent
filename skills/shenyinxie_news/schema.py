from typing import Literal

from pydantic import BaseModel, Field


class NewsCandidate(BaseModel):
    """搜索/网页读取后得到的一篇候选报道。"""

    url: str = Field(description="搜索返回的原始 URL")
    canonical_url: str = Field(description="网页标注的规范 URL 或最终 URL")
    title: str = Field(description="报道标题")
    site: str = Field(description="来源站点 hostname")
    media_name: str = Field(default="", description="白名单中的媒体名称")
    media_tier: int = Field(default=0, description="媒体权威性等级，数字越小越权威")
    publish_date: str = Field(default="", description="发布日期 ISO 字符串，提取失败为空")
    date_extracted_from: str = Field(default="", description="日期提取依据")
    body: str = Field(description="正文文本")
    is_core_subject: bool | None = Field(default=None, description="微众银行是否为核心主体")
    is_repost: bool | None = Field(default=None, description="是否为简单转载")
    relevance_score: float = Field(default=0.0)
    authority_score: float = Field(default=0.0)
    news_value_score: float = Field(default=0.0)
    completeness_score: float = Field(default=0.0)
    originality_score: float = Field(default=0.0)
    total_score: float = Field(default=0.0)
    select_reason: str = Field(default="", description="入选或淘汰理由")
    content_mode: Literal["", "full_text", "extract"] = Field(default="")
    source_title: str = Field(default="", description="原报道标题")
    editor_note: str = Field(default="", description="摘编说明，全文稿为空")
    achievement_types: list[str] = Field(default_factory=list)


class ArticleAssessment(BaseModel):
    """模型对单篇候选的报送价值和可用方式判断。"""

    decision: Literal["full_text", "extract", "reject"]
    is_positive_achievement: bool
    subject_strength: Literal["primary", "substantial", "mention"]
    reason: str
    suggested_title: str = ""
    excerpt_paragraphs: list[str] = Field(default_factory=list)
    achievement_types: list[str] = Field(default_factory=list)


class SelectedArticle(BaseModel):
    """最终入选并写入 Word 的报道。"""

    title: str
    media_name: str
    publish_date: str
    body: str
    original_url: str
    content_mode: Literal["full_text", "extract"] = "full_text"
    source_title: str = ""
    editor_note: str = ""


class ShenyinxieNewsResult(BaseModel):
    """深银协动态 Skill 的完整输出。"""

    title: str = Field(default="", description="生成文档的标题")
    body: str = Field(default="", description="面向用户的正文内容")
    period_start: str = Field(description="本期开始日期，ISO 字符串")
    period_end: str = Field(description="本期结束日期，ISO 字符串")
    issue_number: str = Field(description="期次，如 2026-14")
    articles: list[SelectedArticle] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list, description="原文链接列表")
    output_file: str = Field(default="", description="生成 Word 的路径，v0.5 可能为空")
    needs_clarification: bool = Field(default=False)
    message: str = Field(default="", description="面向用户的说明")


class RelevanceCheck(BaseModel):
    """模型判断单篇报道是否以微众银行为核心主体。"""

    is_core_subject: bool
    is_repost: bool
    reason: str


class ArticleScore(BaseModel):
    """模型对单篇合格报道的评分。"""

    url: str
    relevance_score: float = Field(ge=0.0, le=10.0)
    authority_score: float = Field(ge=0.0, le=10.0)
    news_value_score: float = Field(ge=0.0, le=10.0)
    completeness_score: float = Field(ge=0.0, le=10.0)
    originality_score: float = Field(ge=0.0, le=10.0)
    reason: str


class ScoreResult(BaseModel):
    """模型对多篇候选报道的评分结果。"""

    scores: list[ArticleScore]
