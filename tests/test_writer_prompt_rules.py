from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def test_writer1_prompt_uses_our_bank_naming_rule():
    prompt = (ROOT / "skills/writer1/prompts/draft.md").read_text(encoding="utf-8")
    skill = (ROOT / "skills/writer1/SKILL.md").read_text(encoding="utf-8")

    assert "深圳前海微众银行（以下简称“我行”）" in prompt
    assert "后文统一使用“我行”" in prompt
    assert "深圳前海微众银行（以下简称“我行”）" in skill
    assert '以下简称"我行"' not in prompt + skill


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
    critic = (ROOT / "skills/writer1/prompts/critic.md").read_text(encoding="utf-8")

    assert "写作规划" in prompt
    assert "revision_feedback" in prompt
    assert "新闻稿" in prompt + skill + critic
    assert "地方政府和监管部门" in prompt + skill + critic
    assert "1000字左右" in prompt + skill
    assert "机制成果型" in prompt + skill
    assert "产品工具型" in prompt + skill
    assert "活动亮相型" in prompt + skill
    assert "内部流转简报" not in prompt + skill + critic
    assert "适合内部流转和领导阅读" not in prompt + skill + critic


def test_writer2_prompt_mentions_unified_theme_weak_relation_and_revision_feedback():
    prompt = (ROOT / "skills/writer2/prompts/draft.md").read_text(encoding="utf-8")
    skill = (ROOT / "skills/writer2/SKILL.md").read_text(encoding="utf-8")
    critic = (ROOT / "skills/writer2/prompts/critic.md").read_text(encoding="utf-8")

    assert "写作规划" in prompt
    assert "revision_feedback" in prompt
    assert "统一主题" in prompt + skill
    assert "不要强行整合" in prompt + skill or "弱关联" in prompt + skill
    assert "地方政府和监管部门" in prompt + skill + critic
    assert "1000字左右" in prompt + skill
    assert "平台合作型" in prompt + skill
    assert "外部认可型" in prompt + skill
    assert "内部流转简报" not in prompt + skill + critic
    assert "适合内部流转和领导阅读" not in prompt + skill + critic


def test_brief_skills_do_not_keep_stale_or_exaggerated_guidance():
    writer1 = (ROOT / "skills/writer1/SKILL.md").read_text(encoding="utf-8")
    writer2 = (ROOT / "skills/writer2/SKILL.md").read_text(encoding="utf-8")
    combined = writer1 + writer2

    assert "topic-selector" not in combined
    assert "knowledge/写作素材库.json" not in combined
    assert "knowledge/领域术语库.json" not in combined
    assert "knowledge/政策背景库.json" not in combined
    assert "行业领先地位" not in combined
    assert '从0到1' not in combined
    assert "政治站位" not in combined
    assert "竞争对手" not in combined


def test_brief_todo_uses_external_reporting_positioning():
    todo = (ROOT / "docs/development/TODO.md").read_text(encoding="utf-8")
    section = todo.split("### TODO-002", 1)[1].split("### TODO-", 1)[0]

    assert "内部流转和领导阅读" not in section
    assert "地方政府和监管部门" in section
