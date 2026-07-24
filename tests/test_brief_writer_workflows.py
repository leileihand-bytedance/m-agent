from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.platform.tools import ToolGateway
from skills.writer1.workflow import run as run_writer1


def test_writer1_workflow_uses_policy_materials_and_skill_specific_writer():
    seen_payloads = []
    calls = []
    gateway = ToolGateway(
        allowed_tools=("web_reader", "policy_materials", "llm_writer"),
        tools={
            "web_reader": lambda url: {
                "title": "微众银行优化小微企业融资服务",
                "text": "微众银行通过数字化方式提升小微企业融资服务效率，扩大普惠金融覆盖面。",
                "url": url,
            },
            "policy_materials": lambda user_instruction, materials, limit=3: calls.append(
                ("policy_materials", user_instruction, materials, limit)
            )
            or [
                {
                    "title": "关于提升小微企业金融服务质效的通知",
                    "text": "相关性说明：命中政策主题：小微企业金融服务\n政策摘录：提升小微企业金融服务质效。",
                    "url": "https://www.nfra.gov.cn/policy",
                    "source": "policy_knowledge",
                    "category": "policy_original",
                    "publish_date": "2026-05-18",
                }
            ],
            "llm_writer": lambda payload: seen_payloads.append(payload)
            or {"title": "微众银行提升小微企业金融服务质效", "body": "简报正文"},
        },
    )

    result = run_writer1(
        inputs={"text": "请根据链接写简报：https://example.com/news", "urls": ["https://example.com/news"]},
        tools=gateway,
    )

    assert result.needs_clarification is False
    assert result.title == "微众银行提升小微企业金融服务质效"
    assert result.sources == ["https://example.com/news", "https://www.nfra.gov.cn/policy"]
    assert calls[0][0] == "policy_materials"
    assert seen_payloads[0]["skill_id"] == "writer1"
    assert "写作类型：单素材简报" in seen_payloads[0]["planning_note"]
    assert seen_payloads[0]["materials"][1]["source"] == "policy_knowledge"


def test_writer1_workflow_revises_previous_draft_without_refreshing_materials():
    seen_payloads = []
    gateway = ToolGateway(
        allowed_tools=("web_reader", "policy_materials", "llm_writer"),
        tools={
            "web_reader": lambda url: (_ for _ in ()).throw(AssertionError("revision should not refetch web pages")),
            "policy_materials": lambda user_instruction, materials, limit=3: (_ for _ in ()).throw(
                AssertionError("revision should not refresh policy materials")
            ),
            "llm_writer": lambda payload: seen_payloads.append(payload)
            or {"title": "修改后简报标题", "body": "修改后简报正文"},
        },
    )

    result = run_writer1(
        inputs={
            "revision": True,
            "revision_request": "只改标题，正文不要动",
            "previous_title": "原简报标题",
            "previous_body": "原简报正文，包含事实与政策背景。",
            "previous_sources": ["https://example.com/news"],
            "urls": ["https://example.com/news"],
        },
        tools=gateway,
    )

    assert result.needs_clarification is False
    assert result.title == "修改后简报标题"
    assert result.body == "原简报正文，包含事实与政策背景。"
    assert result.revision_plan["scope"] == "title"
    assert result.sources == ["https://example.com/news"]
    assert seen_payloads[0]["revision"] is True
    assert seen_payloads[0]["materials"][0]["source"] == "previous_draft"


def test_writer1_workflow_returns_clarification_when_url_read_fails():
    gateway = ToolGateway(
        allowed_tools=("web_reader", "llm_writer"),
        tools={
            "web_reader": lambda url: (_ for _ in ()).throw(TimeoutError("read timeout")),
            "llm_writer": lambda payload: (_ for _ in ()).throw(AssertionError("should not write without material")),
        },
    )

    result = run_writer1(
        inputs={"text": "请根据链接写简报：https://example.com/news", "urls": ["https://example.com/news"]},
        tools=gateway,
    )

    assert result.needs_clarification is True
    assert "链接读取失败" in result.message


def test_writer1_workflow_asks_for_readable_copy_when_document_has_no_text():
    gateway = ToolGateway(
        allowed_tools=("document_reader", "llm_writer"),
        tools={
            "document_reader": lambda path, *, allowed_root, work_dir: {
                "title": "扫描材料.pdf",
                "text": "",
                "source": "uploaded_file",
                "warning_codes": ["ocr_required"],
            },
            "llm_writer": lambda payload: (_ for _ in ()).throw(
                AssertionError("should not write without extracted document text")
            ),
        },
    )

    result = run_writer1(
        inputs={
            "text": "请写简报",
            "files": ["/task/input/扫描材料.pdf"],
            "input_dir": "/task/input",
        },
        tools=gateway,
    )

    assert result.needs_clarification is True
    assert "扫描材料.pdf" in result.message
    assert "未读到有效正文" in result.message


def test_multi_source_workflow_asks_user_when_one_url_read_fails():
    gateway = ToolGateway(
        allowed_tools=("web_reader", "llm_writer"),
        tools={
            "web_reader": lambda url: (
                (_ for _ in ()).throw(TimeoutError("read timeout"))
                if "timeout" in url
                else {
                    "title": "微众银行服务小微企业",
                    "text": "微众银行通过微业贷提升小微企业融资服务效率。",
                    "url": url,
                    "source": "web",
                }
            ),
            "llm_writer": lambda payload: (_ for _ in ()).throw(
                AssertionError("should ask user before writing with partial links")
            ),
        },
    )

    result = run_writer1(
        inputs={
            "text": "基于两个素材写简报",
            "urls": ["https://example.com/readable", "https://example.com/timeout"],
        },
        tools=gateway,
    )

    assert result.needs_clarification is True
    assert "有链接读取失败" in result.message
    assert "继续使用已读取素材写" in result.message
    assert "粘贴读取失败链接的正文" in result.message


def test_multi_source_workflow_continues_with_readable_material_after_user_confirms():
    gateway = ToolGateway(
        allowed_tools=("web_reader", "llm_writer"),
        tools={
            "web_reader": lambda url: (
                (_ for _ in ()).throw(TimeoutError("read timeout"))
                if "timeout" in url
                else {
                    "title": "微众银行服务小微企业",
                    "text": "微众银行通过微业贷提升小微企业融资服务效率。",
                    "url": url,
                    "source": "web",
                }
            ),
            "llm_writer": lambda payload: {
                "title": "微众银行提升小微企业融资服务效率",
                "body": "深圳前海微众银行（以下简称“我行”）持续提升小微企业融资服务效率。",
            },
        },
    )

    result = run_writer1(
        inputs={
            "text": "继续使用已读取素材写",
            "urls": ["https://example.com/readable", "https://example.com/timeout"],
        },
        tools=gateway,
    )

    assert result.needs_clarification is False
    assert result.title == "微众银行提升小微企业融资服务效率"


def test_multi_source_workflow_revises_previous_draft_without_requiring_multiple_sources():
    seen_payloads = []
    gateway = ToolGateway(
        allowed_tools=("llm_writer",),
        tools={
            "llm_writer": lambda payload: seen_payloads.append(payload)
            or {"title": "修改后多素材简报标题", "body": "修改后多素材简报正文"},
        },
    )

    result = run_writer1(
        inputs={
            "revision": True,
            "revision_request": "补一段面向监管的意义",
            "previous_title": "原多素材简报标题",
            "previous_body": "原多素材简报正文。",
            "previous_sources": ["https://example.com/a", "https://example.com/b"],
        },
        tools=gateway,
    )

    assert result.needs_clarification is False
    assert result.title == "修改后多素材简报标题"
    assert result.sources == ["https://example.com/a", "https://example.com/b"]
    assert seen_payloads[0]["skill_id"] == "writer1"
    assert seen_payloads[0]["revision_request"] == "补一段面向监管的意义"


def test_writer1_workflow_adds_bank_materials_before_policy_materials():
    seen_payloads = []
    calls = []
    gateway = ToolGateway(
        allowed_tools=("web_reader", "bank_materials", "policy_materials", "llm_writer"),
        tools={
            "web_reader": lambda url: {
                "title": "微众银行优化小微企业融资服务",
                "text": "微众银行通过数字化方式提升小微企业融资服务效率。",
                "url": url,
            },
            "bank_materials": lambda user_instruction, materials, limit=3: calls.append(
                ("bank_materials", materials)
            )
            or [
                {
                    "title": "微业贷服务小微企业",
                    "text": "微众银行素材摘录：微业贷累计申请企业法人客户超过760万。",
                    "url": "bank://e1",
                    "source": "bank_knowledge",
                }
            ],
            "policy_materials": lambda user_instruction, materials, limit=3: calls.append(
                ("policy_materials", materials)
            )
            or [
                {
                    "title": "关于提升小微企业金融服务质效的通知",
                    "text": "政策摘录：提升小微企业金融服务质效。",
                    "url": "https://www.nfra.gov.cn/policy",
                    "source": "policy_knowledge",
                }
            ],
            "llm_writer": lambda payload: seen_payloads.append(payload)
            or {"title": "微众银行提升小微企业金融服务质效", "body": "简报正文"},
        },
    )

    result = run_writer1(
        inputs={"text": "请根据链接写简报：https://example.com/news", "urls": ["https://example.com/news"]},
        tools=gateway,
    )

    assert result.needs_clarification is False
    assert [item.get("source", "") for item in seen_payloads[0]["materials"]] == [
        "",
        "bank_knowledge",
        "policy_knowledge",
    ]
    assert len(calls[0][1]) == 1
    assert len(calls[1][1]) == 1
    assert calls[1][1][0]["title"] == "微众银行优化小微企业融资服务"


def test_writer1_workflow_skips_bank_materials_when_user_material_already_has_data():
    seen_payloads = []
    calls = []
    gateway = ToolGateway(
        allowed_tools=("web_reader", "bank_materials", "policy_materials", "llm_writer"),
        tools={
            "web_reader": lambda url: {
                "title": "微众银行优化小微企业融资服务",
                "text": "截至2026年6月，微众银行已服务小微企业超180万户，累计贷款超过3万亿元。",
                "url": url,
            },
            "bank_materials": lambda user_instruction, materials, limit=3: calls.append(("bank_materials", materials))
            or [
                {
                    "title": "微业贷服务小微企业",
                    "text": "微众银行素材摘录：微业贷累计申请企业法人客户超过760万。",
                    "url": "bank://e1",
                    "source": "bank_knowledge",
                }
            ],
            "policy_materials": lambda user_instruction, materials, limit=3: calls.append(("policy_materials", materials))
            or [],
            "llm_writer": lambda payload: seen_payloads.append(payload)
            or {"title": "微众银行简报标题", "body": "简报正文"},
        },
    )

    result = run_writer1(
        inputs={"text": "请根据链接写简报：https://example.com/news", "urls": ["https://example.com/news"]},
        tools=gateway,
    )

    assert result.needs_clarification is False
    assert [call[0] for call in calls] == ["policy_materials"]
    assert [item.get("source", "") for item in seen_payloads[0]["materials"]] == [""]


def test_multi_source_workflow_skips_bank_materials_when_user_materials_already_have_data():
    seen_payloads = []
    calls = []
    gateway = ToolGateway(
        allowed_tools=("web_reader", "bank_materials", "policy_materials", "llm_writer"),
        tools={
            "web_reader": lambda url: {
                "title": f"素材-{url[-1]}",
                "text": (
                    "截至2026年6月，微众银行已服务小微企业超180万户，累计贷款超过3万亿元。"
                    if url.endswith("/a")
                    else "其中，外贸企业授信客户超1200家，累计授信金额超35亿元。"
                ),
                "url": url,
            },
            "bank_materials": lambda user_instruction, materials, limit=3: calls.append(("bank_materials", materials))
            or [
                {
                    "title": "微业贷服务小微企业",
                    "text": "微众银行素材摘录：微业贷累计申请企业法人客户超过760万。",
                    "url": "bank://e1",
                    "source": "bank_knowledge",
                }
            ],
            "policy_materials": lambda user_instruction, materials, limit=3: calls.append(("policy_materials", materials))
            or [],
            "llm_writer": lambda payload: seen_payloads.append(payload)
            or {"title": "微众银行多素材简报标题", "body": "简报正文"},
        },
    )

    result = run_writer1(
        inputs={
            "text": "请根据两个链接整合写简报",
            "urls": ["https://example.com/a", "https://example.com/b"],
        },
        tools=gateway,
    )

    assert result.needs_clarification is False
    assert [call[0] for call in calls] == ["policy_materials"]
    assert [item.get("source", "") for item in seen_payloads[0]["materials"]] == ["", ""]


def test_writer1_workflow_accepts_inline_material_without_url():
    seen_payloads = []
    gateway = ToolGateway(
        allowed_tools=("policy_materials", "llm_writer"),
        tools={
            "policy_materials": lambda user_instruction, materials, limit=3: [],
            "llm_writer": lambda payload: seen_payloads.append(payload)
            or {"title": "微众银行简报标题", "body": "简报正文"},
        },
    )

    result = run_writer1(
        inputs={"text": "请写简报：微众银行通过数字化方式提升小微企业金融服务效率，扩大普惠金融覆盖面。", "urls": []},
        tools=gateway,
    )

    assert result.needs_clarification is False
    assert seen_payloads[0]["materials"][0]["source"] == "user_text"


def test_writer1_workflow_treats_generated_body_as_completed_even_if_model_sets_clarification():
    gateway = ToolGateway(
        allowed_tools=("policy_materials", "llm_writer"),
        tools={
            "policy_materials": lambda user_instruction, materials, limit=3: [],
            "llm_writer": lambda payload: {
                "title": "微众银行简报标题",
                "body": "简报正文",
                "needs_clarification": True,
                "message": "如需补充数据可继续提供。",
            },
        },
    )

    result = run_writer1(
        inputs={"text": "请写简报：微众银行通过数字化方式提升小微企业金融服务效率，扩大普惠金融覆盖面。", "urls": []},
        tools=gateway,
    )

    assert result.needs_clarification is False
    assert result.title == "微众银行简报标题"


def test_writer1_workflow_asks_for_material_when_input_is_too_short():
    gateway = ToolGateway(allowed_tools=("llm_writer",), tools={})

    result = run_writer1(inputs={"text": "写简报", "urls": []}, tools=gateway)

    assert result.needs_clarification is True
    assert "素材" in result.message


def test_multi_source_workflow_uses_canonical_brief_rules():
    seen_payloads = []
    gateway = ToolGateway(
        allowed_tools=("web_reader", "policy_materials", "llm_writer"),
        tools={
            "web_reader": lambda url: {"title": f"素材 {url}", "text": "素材正文", "url": url},
            "policy_materials": lambda user_instruction, materials, limit=3: [],
            "llm_writer": lambda payload: seen_payloads.append(payload)
            or {"title": "微众银行多素材简报标题", "body": "多素材简报正文"},
        },
    )

    result = run_writer1(
        inputs={
            "text": "请写多素材简报：https://example.com/a https://example.com/b",
            "urls": ["https://example.com/a", "https://example.com/b"],
        },
        tools=gateway,
    )

    assert result.needs_clarification is False
    assert result.sources == ["https://example.com/a", "https://example.com/b"]
    assert seen_payloads[0]["skill_id"] == "writer1"
    assert "写作类型：多素材简报" in seen_payloads[0]["planning_note"]
    assert len(seen_payloads[0]["materials"]) == 2


def test_writer1_unified_workflow_automatically_uses_multi_source_mode():
    seen_payloads = []
    gateway = ToolGateway(
        allowed_tools=("web_reader", "policy_materials", "llm_writer"),
        tools={
            "web_reader": lambda url: {
                "title": f"小微服务素材 {url}",
                "text": "微众银行持续完善小微企业数字化融资服务机制，提升普惠金融服务效率。",
                "url": url,
            },
            "policy_materials": lambda user_instruction, materials, limit=3: [],
            "llm_writer": lambda payload: seen_payloads.append(payload)
            or {"title": "微众银行持续提升小微企业服务质效", "body": "简报正文"},
        },
    )

    result = run_writer1(
        inputs={
            "text": "请整合两个链接写简报",
            "urls": ["https://example.com/a", "https://example.com/b"],
        },
        tools=gateway,
    )

    assert result.needs_clarification is False
    assert seen_payloads[0]["skill_id"] == "writer1"
    assert "写作类型：多素材简报" in seen_payloads[0]["planning_note"]
    assert len(seen_payloads[0]["materials"]) == 2


def test_writer1_unified_workflow_asks_to_split_weakly_related_sources():
    gateway = ToolGateway(
        allowed_tools=("web_reader", "policy_materials", "llm_writer"),
        tools={
            "web_reader": lambda url: (
                {
                    "title": "微众银行服务外贸企业",
                    "text": "微众银行通过跨境金融服务支持外贸企业稳订单拓市场。",
                    "url": url,
                }
                if url.endswith("/a")
                else {
                    "title": "微众银行举办员工羽毛球比赛",
                    "text": "微众银行组织员工开展羽毛球比赛，丰富员工业余生活。",
                    "url": url,
                }
            ),
            "policy_materials": lambda user_instruction, materials, limit=3: [],
            "llm_writer": lambda payload: (_ for _ in ()).throw(
                AssertionError("weakly related materials should not draft")
            ),
        },
    )

    result = run_writer1(
        inputs={
            "text": "请整合两个链接写简报",
            "urls": ["https://example.com/a", "https://example.com/b"],
        },
        tools=gateway,
    )

    assert result.needs_clarification is True
    assert "不适合整合成一篇简报" in result.message


def test_multi_source_workflow_splits_inline_materials():
    seen_payloads = []
    gateway = ToolGateway(
        allowed_tools=("policy_materials", "llm_writer"),
        tools={
            "policy_materials": lambda user_instruction, materials, limit=3: [],
            "llm_writer": lambda payload: seen_payloads.append(payload)
            or {"title": "微众银行多素材简报标题", "body": "多素材简报正文"},
        },
    )

    result = run_writer1(
        inputs={
            "text": (
                "请写多素材简报：素材一，微众银行通过数字化方式提升小微企业融资服务效率。"
                "素材二，微众银行探索人工智能在金融服务中的应用。"
            ),
            "urls": [],
        },
        tools=gateway,
    )

    assert result.needs_clarification is False
    assert len(seen_payloads[0]["materials"]) == 2
    assert seen_payloads[0]["materials"][0]["title"] == "用户直接提供素材1"
    assert "小微企业融资" in seen_payloads[0]["materials"][0]["text"]
    assert "人工智能" in seen_payloads[0]["materials"][1]["text"]


def test_writer1_workflow_reads_uploaded_files_and_material_text():
    seen_payloads = []
    read_calls = []
    gateway = ToolGateway(
        allowed_tools=("word_reader", "pdf_reader", "bank_materials", "policy_materials", "llm_writer"),
        tools={
            "word_reader": lambda path, *, allowed_root: read_calls.append(("word", Path(path).name))
            or {
                "title": Path(path).name,
                "text": "Word 文件正文",
                "path": path,
            },
            "pdf_reader": lambda path, *, allowed_root: read_calls.append(("pdf", Path(path).name))
            or {
                "title": Path(path).name,
                "text": "PDF 文件正文",
                "path": path,
            },
            "bank_materials": lambda user_instruction, materials, limit=3: [],
            "policy_materials": lambda user_instruction, materials, limit=3: [],
            "llm_writer": lambda payload: seen_payloads.append(payload)
            or {"title": "微众银行简报标题", "body": "简报正文"},
        },
    )

    result = run_writer1(
        inputs={
            "text": "请写简报，突出稳外贸。",
            "material_text": "这是补充的文字素材，说明产品上线后覆盖了多个场景。",
            "files": ["/tmp/job/input/材料A.docx", "/tmp/job/input/材料B.pdf"],
            "input_dir": "/tmp/job/input",
            "urls": [],
        },
        tools=gateway,
    )

    assert result.needs_clarification is False
    assert read_calls == [("word", "材料A.docx"), ("pdf", "材料B.pdf")]
    assert [item["title"] for item in seen_payloads[0]["materials"]] == [
        "材料A.docx",
        "材料B.pdf",
        "用户补充文字素材",
    ]


def test_writer1_workflow_passes_planning_note_and_rewrites_after_news_style_critic():
    seen_payloads = []

    def llm_writer(payload):
        seen_payloads.append(payload)
        task = payload.get("task")
        if task == "writer1_critic":
            return {
                "violations": [
                    {
                        "rule": "brief-style",
                        "severity": "hard",
                        "message": "正文仍像新闻稿，主要在复述发布动作，没有转成简报体。",
                        "suggestion": "改为内部简报体，围绕背景、做法、成效组织正文，去掉发布会式表达。",
                    }
                ]
            }
        if payload.get("revision_feedback"):
            return {
                "title": "微众银行提升小微企业金融服务质效",
                "body": "深圳前海微众银行（以下简称“我行”）围绕小微企业融资需求持续完善数字化服务机制，并取得阶段性进展。后文我行将继续提升服务质效。",
            }
        return {
            "title": "微众银行提升小微企业金融服务质效",
            "body": "近日，微众银行举办发布会并介绍相关情况，表示将持续加大服务力度。",
        }

    gateway = ToolGateway(
        allowed_tools=("policy_materials", "llm_writer"),
        tools={
            "policy_materials": lambda user_instruction, materials, limit=3: [],
            "llm_writer": llm_writer,
        },
    )

    result = run_writer1(
        inputs={
            "text": "请写简报：微众银行围绕小微企业融资需求持续完善数字化服务机制，截至2026年6月相关服务已覆盖超180万户。",
            "urls": [],
        },
        tools=gateway,
    )

    writer_calls = [payload for payload in seen_payloads if payload.get("task") == "writer1"]
    critic_calls = [payload for payload in seen_payloads if payload.get("task") == "writer1_critic"]

    assert result.needs_clarification is False
    assert result.title == "微众银行提升小微企业金融服务质效"
    assert len(writer_calls) == 2
    assert "planning_note" in writer_calls[0]
    assert "写作类型：单素材简报" in writer_calls[0]["planning_note"]
    assert len(critic_calls) >= 1
    assert "revision_feedback" in writer_calls[1]
    assert "新闻稿" in writer_calls[1]["revision_feedback"]


def test_multi_source_workflow_asks_to_split_when_sources_are_weakly_related():
    gateway = ToolGateway(
        allowed_tools=("web_reader", "policy_materials", "llm_writer"),
        tools={
            "web_reader": lambda url: (
                {
                    "title": "微众银行优化小微企业融资服务",
                    "text": "微众银行通过数字化方式提升小微企业融资服务效率。",
                    "url": url,
                }
                if url.endswith("/a")
                else {
                    "title": "微众银行举办员工羽毛球比赛",
                    "text": "微众银行组织员工开展羽毛球比赛，丰富员工业余生活。",
                    "url": url,
                }
            ),
            "policy_materials": lambda user_instruction, materials, limit=3: [],
            "llm_writer": lambda payload: (_ for _ in ()).throw(AssertionError("weakly related materials should not draft")),
        },
    )

    result = run_writer1(
        inputs={
            "text": "请整合两个链接写简报",
            "urls": ["https://example.com/a", "https://example.com/b"],
        },
        tools=gateway,
    )

    assert result.needs_clarification is True
    assert "不适合整合成一篇简报" in result.message


def test_writer1_workflow_prefers_shared_policy_research_tool():
    seen_payloads = []
    calls = []
    gateway = ToolGateway(
        allowed_tools=("web_reader", "bank_materials", "policy_research", "llm_writer"),
        tools={
            "web_reader": lambda url: {
                "title": "微众银行优化小微企业融资服务",
                "text": "微众银行通过数字化方式提升小微企业融资服务效率，扩大普惠金融覆盖面。",
                "url": url,
            },
            "bank_materials": lambda user_instruction, materials, limit=3: [],
            "policy_research": lambda **kwargs: calls.append(kwargs)
            or {
                "should_attach_policy": True,
                "decision_reason": "qualified_local_policy",
                "matched_themes": ["small_micro"],
                "retrieval_query": "小微企业 普惠金融 融资",
                "confidence": 0.86,
                "primary_policy": {
                    "title": "关于提升小微企业金融服务质效的通知",
                    "source": "nfra",
                    "category": "policy_original",
                    "publish_date": "2026-07-01",
                    "url": "https://www.nfra.gov.cn/policy",
                    "snippet": "提升小微企业金融服务质效，优化融资供给。",
                    "matched_terms": ["小微企业", "普惠金融"],
                    "relevance_score": 36,
                    "selection_reason": "命中政策主题：小微企业金融服务；匹配关键词：小微企业、普惠金融",
                },
                "alternative_policies": [],
            },
            "llm_writer": lambda payload: seen_payloads.append(payload)
            or {"title": "微众银行提升小微企业金融服务质效", "body": "简报正文"},
        },
    )

    result = run_writer1(
        inputs={"text": "请根据链接写简报：https://example.com/news", "urls": ["https://example.com/news"]},
        tools=gateway,
    )

    assert result.needs_clarification is False
    assert result.sources == ["https://example.com/news", "https://www.nfra.gov.cn/policy"]
    assert calls[0]["usage_profile"] == "brief"
    assert seen_payloads[0]["materials"][1]["source"] == "policy_knowledge"
