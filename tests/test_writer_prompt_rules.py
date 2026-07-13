from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def test_writer1_prompt_uses_our_bank_naming_rule():
    prompt = (ROOT / "skills/writer1/prompts/draft.md").read_text(encoding="utf-8")
    skill = (ROOT / "skills/writer1/SKILL.md").read_text(encoding="utf-8")
    writing_materials = (ROOT / "skills/writer1/knowledge/writing-materials.json").read_text(encoding="utf-8")

    assert "深圳前海微众银行（以下简称“我行”）" in prompt
    assert "后文统一使用“我行”" in prompt
    assert "深圳前海微众银行（以下简称“我行”）" in skill
    assert "深圳前海微众银行（以下简称“我行”）" in writing_materials
    assert '以下简称"我行"' not in prompt + skill + writing_materials


def test_writer2_prompt_uses_our_bank_naming_rule():
    prompt = (ROOT / "skills/writer2/prompts/draft.md").read_text(encoding="utf-8")
    skill = (ROOT / "skills/writer2/SKILL.md").read_text(encoding="utf-8")

    assert "深圳前海微众银行（以下简称“我行”）" in prompt
    assert "后文统一使用“我行”" in prompt
    assert "深圳前海微众银行（以下简称“我行”）" in skill
    assert '以下简称"我行"' not in prompt + skill


def test_direct_report_prompt_allows_comprehensive_progress_exception():
    prompt = (ROOT / "skills/direct_report/prompts/draft.md").read_text(encoding="utf-8")
    skill = (ROOT / "skills/direct_report/SKILL.md").read_text(encoding="utf-8")

    assert "仅当写作规划明确为“综合进展型”时" in prompt
    assert "围绕一个总主题组织 2-3 个并列板块" in prompt
    assert "综合进展型" in skill
    assert "2-3 个并列板块" in skill


def test_direct_report_prompt_requires_transition_and_elevated_ending():
    prompt = (ROOT / "skills/direct_report/prompts/draft.md").read_text(encoding="utf-8")
    skill = (ROOT / "skills/direct_report/SKILL.md").read_text(encoding="utf-8")

    assert "在此背景下" in prompt
    assert "微众银行积极响应" in prompt
    assert "句与句之间要有自然过渡" in prompt
    assert "结尾不只停留在就事论事" in prompt
    assert "做好金融“五篇大文章”" in skill


def test_direct_report_and_brief_title_connector_rules_are_explicit():
    direct_prompt = (ROOT / "skills/direct_report/prompts/draft.md").read_text(encoding="utf-8")
    direct_skill = (ROOT / "skills/direct_report/SKILL.md").read_text(encoding="utf-8")
    writer1_prompt = (ROOT / "skills/writer1/prompts/draft.md").read_text(encoding="utf-8")
    writer1_skill = (ROOT / "skills/writer1/SKILL.md").read_text(encoding="utf-8")
    writer2_prompt = (ROOT / "skills/writer2/prompts/draft.md").read_text(encoding="utf-8")
    writer2_skill = (ROOT / "skills/writer2/SKILL.md").read_text(encoding="utf-8")

    assert "逗号或冒号" in direct_prompt
    assert "禁止使用空格" in direct_skill
    assert "逗号或冒号" in writer1_prompt
    assert "禁止使用空格" in writer1_skill
    assert "逗号或冒号" in writer2_prompt
    assert "禁止使用空格" in writer2_skill


def test_writer1_prompt_mentions_planning_revision_feedback_and_anti_news_style():
    prompt = (ROOT / "skills/writer1/prompts/draft.md").read_text(encoding="utf-8")
    skill = (ROOT / "skills/writer1/SKILL.md").read_text(encoding="utf-8")

    assert "写作规划" in prompt
    assert "revision_feedback" in prompt
    assert "新闻稿" in prompt + skill


def test_writer2_prompt_mentions_unified_theme_weak_relation_and_revision_feedback():
    prompt = (ROOT / "skills/writer2/prompts/draft.md").read_text(encoding="utf-8")
    skill = (ROOT / "skills/writer2/SKILL.md").read_text(encoding="utf-8")

    assert "写作规划" in prompt
    assert "revision_feedback" in prompt
    assert "统一主题" in prompt + skill
    assert "不要强行整合" in prompt + skill or "弱关联" in prompt + skill
