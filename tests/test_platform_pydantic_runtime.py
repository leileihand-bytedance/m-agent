from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.platform.pydantic_runtime import PydanticAIWriter
from skills.direct_report.schema import DirectReportResult
from skills.rewrite.schema import RewriteResult


class _FakeRunResult:
    def __init__(self, output):
        self.output = output


class _FakeAgent:
    def __init__(self, output):
        self.output = output
        self.last_prompt = ""

    def run_sync(self, prompt):
        self.last_prompt = prompt
        return _FakeRunResult(self.output)


def test_pydantic_writer_uses_agent_and_structured_output():
    fake_agent = _FakeAgent(
        DirectReportResult(
            title="结构化标题",
            body="结构化正文",
            sources=["https://example.com/news"],
        )
    )
    calls = {}

    def agent_factory(model, output_type, instructions, model_settings=None):
        calls["model"] = model
        calls["output_type"] = output_type
        calls["instructions"] = instructions
        return fake_agent

    writer = PydanticAIWriter(
        api_key="test-key",
        base_url="https://example.com/anthropic",
        model_name="test-model",
        skill_dir=Path("skills/direct_report"),
        agent_factory=agent_factory,
    )

    result = writer.write(
        {
            "task": "direct_report",
            "instruction": "请写直报",
            "materials": [
                {
                    "title": "网页标题",
                    "text": "网页正文",
                    "url": "https://example.com/news",
                    "source": "policy_knowledge",
                    "category": "policy_original",
                    "publish_date": "2026-05-19",
                }
            ],
        }
    )

    assert result["title"] == "结构化标题"
    assert result["body"] == "结构化正文"
    assert result["sources"] == ["https://example.com/news"]
    assert calls["model"] == "test-model"
    assert calls["output_type"] is DirectReportResult
    assert "直报写作 Skill" in calls["instructions"]
    assert "700-800字" in calls["instructions"]
    assert "自称用\"微众银行\"" in calls["instructions"]
    assert "成功打造" in calls["instructions"]
    assert "网页正文" in fake_agent.last_prompt
    assert "政策分类：policy_original" in fake_agent.last_prompt
    assert "发布日期：2026-05-19" in fake_agent.last_prompt


def test_pydantic_writer_does_not_import_pydantic_ai_until_write():
    writer = PydanticAIWriter(
        api_key="",
        base_url="https://example.com/anthropic",
        model_name="test-model",
        skill_dir=Path("skills/direct_report"),
    )

    try:
        writer.write({"task": "direct_report", "instruction": "", "materials": []})
    except RuntimeError as exc:
        assert "ANTHROPIC_API_KEY" in str(exc) or "pydantic-ai" in str(exc)
    else:
        raise AssertionError("RuntimeError was not raised")


def test_pydantic_writer_loads_skill_rules_from_payload_skill_id():
    fake_agent = _FakeAgent(
        DirectReportResult(
            title="简报标题",
            body="简报正文",
            sources=["https://example.com/news"],
        )
    )
    calls = {}

    def agent_factory(model, output_type, instructions, model_settings=None):
        calls["instructions"] = instructions
        return fake_agent

    writer = PydanticAIWriter(
        api_key="test-key",
        base_url="https://example.com/anthropic",
        model_name="test-model",
        skill_dir=Path("skills"),
        agent_factory=agent_factory,
    )

    result = writer.write(
        {
            "skill_id": "writer1",
            "task": "writer1",
            "instruction": "请写简报",
            "materials": [{"title": "素材", "text": "素材正文", "url": "https://example.com/news"}],
        }
    )

    assert result["title"] == "简报标题"
    assert "简报写作助手 (Writer1)" in calls["instructions"]
    assert "直报写作 Skill" not in calls["instructions"]
    assert "素材正文" in fake_agent.last_prompt


def test_pydantic_writer_includes_planning_note_in_prompt():
    fake_agent = _FakeAgent(
        DirectReportResult(
            title="结构化标题",
            body="结构化正文",
            sources=[],
        )
    )

    writer = PydanticAIWriter(
        api_key="test-key",
        base_url="https://example.com/anthropic",
        model_name="test-model",
        skill_dir=Path("skills/direct_report"),
        agent_factory=lambda model, output_type, instructions, model_settings=None: fake_agent,
    )

    writer.write(
        {
            "task": "direct_report",
            "instruction": "请写直报",
            "planning_note": "文体：直报\n开头策略：政策背景型\n优先写入数据：近5亿元。",
            "materials": [{"title": "网页标题", "text": "网页正文", "url": "https://example.com/news"}],
        }
    )

    assert "## 写作规划" in fake_agent.last_prompt
    assert "开头策略：政策背景型" in fake_agent.last_prompt


def test_pydantic_writer_keeps_full_previous_draft_material_in_prompt():
    fake_agent = _FakeAgent(
        DirectReportResult(
            title="结构化标题",
            body="结构化正文",
            sources=[],
        )
    )

    writer = PydanticAIWriter(
        api_key="test-key",
        base_url="https://example.com/anthropic",
        model_name="test-model",
        skill_dir=Path("skills/direct_report"),
        agent_factory=lambda model, output_type, instructions, model_settings=None: fake_agent,
    )

    long_previous_draft = "前文" * 1200 + "尾段必须保留"
    writer.write(
        {
            "task": "direct_report",
            "instruction": "请基于上一稿修改",
            "revision": True,
            "materials": [
                {
                    "title": "上一稿",
                    "text": long_previous_draft,
                    "source": "previous_draft",
                }
            ],
        }
    )

    assert "尾段必须保留" in fake_agent.last_prompt
    assert "[后文已截断]" not in fake_agent.last_prompt


def test_pydantic_writer_balances_long_uploaded_document_material():
    fake_agent = _FakeAgent(
        DirectReportResult(
            title="结构化标题",
            body="结构化正文",
            sources=[],
        )
    )
    writer = PydanticAIWriter(
        api_key="test-key",
        base_url="https://example.com/anthropic",
        model_name="test-model",
        skill_dir=Path("skills/direct_report"),
        agent_factory=lambda model, output_type, instructions, model_settings=None: fake_agent,
    )
    long_material = "开头关键事实" + "甲" * 7000 + "中部关键事实" + "乙" * 7000 + "结尾关键事实"

    writer.write(
        {
            "task": "direct_report",
            "instruction": "请写直报",
            "materials": [
                {
                    "title": "长材料.pdf",
                    "text": long_material,
                    "source": "uploaded_file",
                    "content_complete": False,
                }
            ],
        }
    )

    assert "开头关键事实" in fake_agent.last_prompt
    assert "中部关键事实" in fake_agent.last_prompt
    assert "结尾关键事实" in fake_agent.last_prompt
    assert "长文档已均衡取样" in fake_agent.last_prompt


def test_pydantic_writer_keeps_outline_role_visible_and_uses_larger_outline_budget():
    fake_agent = _FakeAgent(
        DirectReportResult(title="结构化标题", body="结构化正文", sources=[])
    )
    writer = PydanticAIWriter(
        api_key="test-key",
        base_url="https://example.com/anthropic",
        model_name="test-model",
        skill_dir=Path("skills/direct_report"),
        agent_factory=lambda model, output_type, instructions, model_settings=None: fake_agent,
    )
    long_outline = "提纲开头" + "甲" * 7000 + "提纲结尾"

    writer.write(
        {
            "task": "direct_report",
            "instruction": "请按提纲整合",
            "materials": [
                {
                    "title": "综合调研提纲.docx",
                    "text": long_outline,
                    "source": "uploaded_file",
                    "material_role": "outline",
                }
            ],
        }
    )

    assert "材料角色：outline" in fake_agent.last_prompt
    assert "提纲开头" in fake_agent.last_prompt
    assert "提纲结尾" in fake_agent.last_prompt


def test_pydantic_writer_instructions_limit_supplementary_material_scope():
    fake_agent = _FakeAgent(
        DirectReportResult(
            title="结构化标题",
            body="结构化正文",
            sources=[],
        )
    )
    calls = {}

    def agent_factory(model, output_type, instructions, model_settings=None):
        calls["instructions"] = instructions
        return fake_agent

    writer = PydanticAIWriter(
        api_key="test-key",
        base_url="https://example.com/anthropic",
        model_name="test-model",
        skill_dir=Path("skills/direct_report"),
        agent_factory=agent_factory,
    )

    writer.write(
        {
            "task": "direct_report",
            "instruction": "请写直报",
            "materials": [
                {
                    "title": "无障碍金融服务",
                    "text": "微众银行持续完善无障碍金融服务。",
                    "url": "https://example.com/news",
                }
            ],
        }
    )

    assert "不得据此引入用户素材未出现的新业务线" in calls["instructions"]


def test_pydantic_writer_instructions_require_webank_as_main_subject():
    fake_agent = _FakeAgent(
        DirectReportResult(
            title="结构化标题",
            body="结构化正文",
            sources=[],
        )
    )
    calls = {}

    def agent_factory(model, output_type, instructions, model_settings=None):
        calls["instructions"] = instructions
        return fake_agent

    writer = PydanticAIWriter(
        api_key="test-key",
        base_url="https://example.com/anthropic",
        model_name="test-model",
        skill_dir=Path("skills/direct_report"),
        agent_factory=agent_factory,
    )

    writer.write(
        {
            "task": "direct_report",
            "instruction": "请写直报",
            "materials": [
                {
                    "title": "外贸贷相关素材",
                    "text": "微众银行联合合作方为外贸企业提供融资支持。",
                    "url": "https://example.com/news",
                }
            ],
        }
    )

    assert "全文必须以微众银行为组织中心" in calls["instructions"]
    assert "不能沿用外部素材把其他主体写成全文主角" in calls["instructions"]


def test_pydantic_writer_instructions_limit_single_enterprise_case_usage():
    fake_agent = _FakeAgent(
        DirectReportResult(
            title="结构化标题",
            body="结构化正文",
            sources=[],
        )
    )
    calls = {}

    def agent_factory(model, output_type, instructions, model_settings=None):
        calls["instructions"] = instructions
        return fake_agent

    writer = PydanticAIWriter(
        api_key="test-key",
        base_url="https://example.com/anthropic",
        model_name="test-model",
        skill_dir=Path("skills/direct_report"),
        agent_factory=agent_factory,
    )

    writer.write(
        {
            "task": "direct_report",
            "instruction": "请写直报",
            "materials": [
                {
                    "title": "外贸贷相关素材",
                    "text": "龙岗区一家企业通过微众银行获批授信。",
                    "url": "https://example.com/news",
                }
            ],
        }
    )

    assert "单个企业受益个案不能单独撑起一篇直报" in calls["instructions"]
    assert "如需使用，只能压缩为主体段中的一句辅助例证" in calls["instructions"]


def test_pydantic_writer_supports_rewrite_custom_output_type():
    fake_agent = _FakeAgent(
        RewriteResult(
            body="润色后的正文",
            revision_note="调整了语气和句式。",
        )
    )
    calls = {}

    def agent_factory(model, output_type, instructions, model_settings=None):
        calls["output_type"] = output_type
        calls["instructions"] = instructions
        return fake_agent

    writer = PydanticAIWriter(
        api_key="test-key",
        base_url="https://example.com/anthropic",
        model_name="test-model",
        skill_dir=Path("skills"),
        agent_factory=agent_factory,
    )

    result = writer.write(
        {
            "skill_id": "rewrite",
            "task": "rewrite",
            "instruction": "请把原文改得更正式。",
            "materials": [{"title": "用户原文", "text": "原始正文", "url": "", "source": "user_text"}],
            "output_type": RewriteResult,
        }
    )

    assert result["body"] == "润色后的正文"
    assert result["revision_note"] == "调整了语气和句式。"
    assert calls["output_type"] is RewriteResult
    assert "材料润色 Skill" in calls["instructions"]
    assert "原始正文" in fake_agent.last_prompt
