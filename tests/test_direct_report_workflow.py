from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.platform.tools import ToolGateway
from skills.direct_report.workflow import _truncate_material_texts, run


def test_direct_report_keeps_head_and_tail_for_long_standard_document_material():
    materials = [
        {
            "title": "长材料.pdf",
            "text": "开头事实" + "甲" * 3000 + "结尾关键事实",
            "artifact_path": "/task/work/documents/example/document.json",
        }
    ]

    result = _truncate_material_texts(materials, max_length=500)

    assert "开头事实" in result[0]["text"]
    assert "结尾关键事实" in result[0]["text"]
    assert "完整解析结果" in result[0]["text"]


def test_direct_report_workflow_reads_url_and_returns_draft():
    gateway = ToolGateway(
        allowed_tools=("web_reader", "llm_writer"),
        tools={
            "web_reader": lambda url: {
                "title": "微众银行服务小微企业",
                "text": "微众银行通过数字化方式提升小微企业金融服务可得性。",
                "url": url,
            },
            "llm_writer": lambda payload: {
                "title": "微众银行提升小微企业金融服务可得性",
                "body": "微众银行围绕小微企业融资需求，持续完善数字化服务能力。",
            },
        },
    )

    result = run(
        inputs={
            "text": "根据这个链接写直报：https://example.com/news",
            "urls": ["https://example.com/news"],
        },
        tools=gateway,
    )

    assert result.title == "微众银行提升小微企业金融服务可得性"
    assert "小微企业" in result.body
    assert result.sources == ["https://example.com/news"]
    assert result.needs_clarification is False
    assert result.output_file == ""


def test_direct_report_workflow_generates_word_when_user_explicitly_requests_it(
    tmp_path: Path,
):
    gateway = ToolGateway(
        allowed_tools=("web_reader", "llm_writer"),
        tools={
            "web_reader": lambda url: {
                "title": "微众银行服务科技型企业",
                "text": "微众银行持续完善科技型企业金融服务机制。",
                "url": url,
            },
            "llm_writer": lambda payload: {
                "title": "微众银行完善科技金融服务机制",
                "body": "第一段。\n\n第二段。\n\n第三段。",
            },
        },
    )

    result = run(
        inputs={
            "text": (
                "根据这个链接写直报并输出Word，2026年第3期（总第28期），"
                "日期2026年7月21日：https://example.com/news"
            ),
            "urls": ["https://example.com/news"],
            "output_dir": str(tmp_path),
        },
        tools=gateway,
    )

    assert result.needs_clarification is False
    assert Path(result.output_file).is_file()
    assert Path(result.output_file).parent == tmp_path


def test_direct_report_export_only_revision_does_not_rewrite_previous_draft(
    tmp_path: Path,
):
    gateway = ToolGateway(
        allowed_tools=("llm_writer",),
        tools={
            "llm_writer": lambda payload: (_ for _ in ()).throw(
                AssertionError("export-only request must not call the model")
            )
        },
    )

    result = run(
        inputs={
            "revision": True,
            "revision_request": (
                "导出Word，2026年第3期（总第28期），日期2026年7月21日"
            ),
            "previous_title": "上一稿标题",
            "previous_body": "上一稿第一段。\n\n上一稿第二段。",
            "previous_sources": ["https://example.com/news"],
            "output_dir": str(tmp_path),
        },
        tools=gateway,
    )

    assert result.title == "上一稿标题"
    assert result.body == "上一稿第一段。\n\n上一稿第二段。"
    assert result.sources == ["https://example.com/news"]
    assert Path(result.output_file).is_file()
    assert "Word" in result.message


def test_direct_report_continues_with_readable_material_after_user_confirms():
    gateway = ToolGateway(
        allowed_tools=("web_reader", "llm_writer"),
        tools={
            "web_reader": lambda url: (
                (_ for _ in ()).throw(TimeoutError("read timeout"))
                if "timeout" in url
                else {
                    "title": "微众银行服务小微企业",
                    "text": "微众银行通过数字化方式提升小微企业金融服务可得性。",
                    "url": url,
                }
            ),
            "llm_writer": lambda payload: {
                "title": "微众银行提升小微企业金融服务可得性",
                "body": "微众银行围绕小微企业融资需求，持续完善数字化服务能力。",
            },
        },
    )

    result = run(
        inputs={
            "text": "继续使用已读取素材写",
            "urls": ["https://example.com/readable", "https://example.com/timeout"],
        },
        tools=gateway,
    )

    assert result.needs_clarification is False
    assert result.sources == ["https://example.com/readable"]


def test_direct_report_workflow_revises_previous_draft_without_refetching_sources():
    seen_payloads = []
    gateway = ToolGateway(
        allowed_tools=("web_reader", "policy_materials", "llm_writer"),
        tools={
            "web_reader": lambda url: (_ for _ in ()).throw(AssertionError("revision should not refetch web pages")),
            "policy_materials": lambda user_instruction, materials, limit=3: (_ for _ in ()).throw(
                AssertionError("revision should not refresh policy materials")
            ),
            "llm_writer": lambda payload: seen_payloads.append(payload)
            or {"title": "修改后直报标题", "body": "修改后直报正文"},
        },
    )

    result = run(
        inputs={
            "revision": True,
            "revision_request": "再压缩一点，突出政策背景",
            "previous_title": "原直报标题",
            "previous_body": "原直报正文，包含较长的背景和事实。",
            "previous_sources": ["https://example.com/news"],
            "urls": ["https://example.com/news"],
        },
        tools=gateway,
    )

    assert result.needs_clarification is False
    assert result.title == "修改后直报标题"
    assert result.sources == ["https://example.com/news"]
    assert seen_payloads[0]["revision"] is True
    assert seen_payloads[0]["materials"][0]["source"] == "previous_draft"
    assert "原直报正文" in seen_payloads[0]["materials"][0]["text"]


def test_direct_report_title_only_revision_preserves_previous_body():
    gateway = ToolGateway(
        allowed_tools=("llm_writer",),
        tools={
            "llm_writer": lambda _payload: {
                "title": "修改后的直报标题",
                "body": "模型擅自重写的正文。",
            }
        },
    )

    result = run(
        inputs={
            "revision": True,
            "revision_request": "只改标题，正文不要动",
            "previous_title": "原直报标题",
            "previous_body": "原直报第一段。\n\n原直报第二段。",
        },
        tools=gateway,
    )

    assert result.title == "修改后的直报标题"
    assert result.body == "原直报第一段。\n\n原直报第二段。"
    assert result.revision_plan["scope"] == "title"


def test_direct_report_revision_can_replace_facts_with_new_material():
    seen_payloads = []
    gateway = ToolGateway(
        allowed_tools=("web_reader", "llm_writer"),
        tools={
            "web_reader": lambda url: {
                "title": "更新后的业务数据",
                "text": "新材料显示，业务覆盖范围已经更新。",
                "url": url,
            },
            "llm_writer": lambda payload: seen_payloads.append(payload)
            or {"title": "修改后直报标题", "body": "已按新材料更新正文。"},
        },
    )

    result = run(
        inputs={
            "revision": True,
            "supplement_materials": True,
            "material_role": "replace",
            "revision_request": "用新材料替换第二段数据",
            "text": "用新材料替换第二段数据",
            "previous_title": "原直报标题",
            "previous_body": "原直报正文。",
            "previous_sources": ["https://example.com/old"],
            "urls": ["https://example.com/new"],
        },
        tools=gateway,
    )

    assert result.needs_clarification is False
    assert result.sources == ["https://example.com/old", "https://example.com/new"]
    replacement = next(
        item
        for item in seen_payloads[0]["materials"]
        if item.get("url") == "https://example.com/new"
    )
    assert replacement["material_role"] == "replace"


def test_direct_report_workflow_asks_when_no_material_is_available():
    gateway = ToolGateway(allowed_tools=("web_reader", "llm_writer"), tools={})

    result = run(inputs={"text": "帮我写直报", "urls": []}, tools=gateway)

    assert result.needs_clarification is True
    assert "链接" in result.message


def test_direct_report_file_read_error_does_not_expose_internal_path_or_exception():
    gateway = ToolGateway(
        allowed_tools=("word_reader", "llm_writer"),
        tools={
            "word_reader": lambda file_path, input_dir: (_ for _ in ()).throw(
                RuntimeError("failed at /private/tmp/secret-job/input/material.docx")
            )
        },
    )

    result = run(
        inputs={
            "text": "请写直报",
            "files": ["/private/tmp/secret-job/input/material.docx"],
            "input_dir": "/private/tmp/secret-job/input",
        },
        tools=gateway,
    )

    assert result.needs_clarification is True
    assert "material.docx" in result.message
    assert "/private/tmp" not in result.message


def test_direct_report_asks_for_readable_copy_when_scanned_pdf_has_no_text():
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

    result = run(
        inputs={
            "text": "请写直报",
            "files": ["/task/input/扫描材料.pdf"],
            "input_dir": "/task/input",
        },
        tools=gateway,
    )

    assert result.needs_clarification is True
    assert "扫描材料.pdf" in result.message
    assert "未读到有效正文" in result.message
    assert "failed at" not in result.message


def test_direct_report_workflow_asks_when_web_page_cannot_be_read():
    gateway = ToolGateway(
        allowed_tools=("web_reader", "llm_writer"),
        tools={
            "web_reader": lambda url: (_ for _ in ()).throw(RuntimeError("网页未能提取到正文")),
            "llm_writer": lambda payload: (_ for _ in ()).throw(AssertionError("should not draft when source is unreadable")),
        },
    )

    result = run(
        inputs={
            "text": "根据这个链接写直报：https://example.com/news",
            "urls": ["https://example.com/news"],
        },
        tools=gateway,
    )

    assert result.needs_clarification is True
    assert "无法读取" in result.message
    assert "https://example.com/news" in result.message


def test_direct_report_workflow_asks_when_web_page_has_no_meaningful_body():
    gateway = ToolGateway(
        allowed_tools=("web_reader", "llm_writer"),
        tools={
            "web_reader": lambda url: {
                "title": "助力“无障碍”，普惠“零距离”微众银行持续推动无障碍金融服务",
                "text": "新京报",
                "url": url,
            },
            "llm_writer": lambda payload: (_ for _ in ()).throw(AssertionError("should not draft when page has no body")),
        },
    )

    result = run(
        inputs={
            "text": "根据这个链接写直报：https://example.com/news",
            "urls": ["https://example.com/news"],
        },
        tools=gateway,
    )

    assert result.needs_clarification is True
    assert "未读到有效正文" in result.message


def test_direct_report_workflow_uses_search_when_no_url_is_available():
    calls = []
    gateway = ToolGateway(
        allowed_tools=("search", "llm_writer"),
        tools={
            "search": lambda query, max_results=5: calls.append(("search", query, max_results))
            or [
                {
                    "title": "微众银行服务小微企业",
                    "snippet": "微众银行持续提升小微企业融资服务能力。",
                    "url": "https://example.com/search-result",
                    "source": "media",
                }
            ],
            "llm_writer": lambda payload: calls.append(("llm_writer", payload))
            or {"title": "搜索生成标题", "body": "搜索生成正文"},
        },
    )

    result = run(inputs={"text": "帮我搜索资料写一篇直报", "urls": []}, tools=gateway)

    assert result.needs_clarification is False
    assert result.title == "搜索生成标题"
    assert result.sources == ["https://example.com/search-result"]
    assert calls[0] == ("search", "帮我搜索资料写一篇直报", 5)
    assert calls[1][0] == "llm_writer"
    assert calls[1][1]["materials"][0]["text"] == "微众银行持续提升小微企业融资服务能力。"


def test_direct_report_workflow_adds_policy_background_before_writing():
    seen_payloads = []
    policy_calls = []

    def policy_search(query, limit=3, category=None):
        policy_calls.append((query, limit, category))
        if category == "policy_original":
            return [
                {
                    "title": "关于提升小微企业金融服务质效的通知",
                    "snippet": "监管部门要求提升小微企业金融服务质效。",
                    "url": "https://www.nfra.gov.cn/original",
                    "source": "nfra",
                    "category": "policy_original",
                }
            ]
        return []

    gateway = ToolGateway(
        allowed_tools=("web_reader", "policy_search", "llm_writer"),
        tools={
            "web_reader": lambda url: {
                "title": "用户素材",
                "text": "微众银行计划优化小微企业金融服务。",
                "url": url,
            },
            "policy_search": policy_search,
            "llm_writer": lambda payload: seen_payloads.append(payload)
            or {"title": "补充政策背景标题", "body": "补充政策背景正文"},
        },
    )

    result = run(
        inputs={
            "text": "根据这个链接写直报：https://example.com/news",
            "urls": ["https://example.com/news"],
        },
        tools=gateway,
    )

    assert result.needs_clarification is False
    assert result.sources == ["https://example.com/news", "https://www.nfra.gov.cn/original"]
    assert policy_calls[0][1:] == (10, "policy_original")
    assert "小微企业" in policy_calls[0][0]
    assert seen_payloads[0]["materials"][0]["title"] == "用户素材"
    assert seen_payloads[0]["materials"][1]["source"] == "policy_knowledge"
    assert seen_payloads[0]["materials"][1]["category"] == "policy_original"


def test_direct_report_workflow_skips_policy_materials_for_lawsuit_event():
    seen_payloads = []
    gateway = ToolGateway(
        allowed_tools=("web_reader", "policy_materials", "llm_writer"),
        tools={
            "web_reader": lambda url: {
                "title": "微众银行商标侵权案获法院判赔",
                "text": (
                    "近日，湖北省武汉市中级人民法院对微众银行起诉的四家关联企业商标侵权"
                    "及不正当竞争案件作出判决，判赔金额合计280万元。微众银行通过除尘行动"
                    "持续打击金融黑灰产。"
                ),
                "url": url,
            },
            "policy_materials": lambda user_instruction, materials, limit=3: (_ for _ in ()).throw(
                AssertionError("lawsuit event should not fetch policy materials")
            ),
            "llm_writer": lambda payload: seen_payloads.append(payload)
            or {"title": "微众银行打击金融黑灰产取得阶段性成果", "body": "微众银行" + "持续完善金融黑灰产治理机制。" * 40},
        },
    )

    result = run(
        inputs={
            "text": "根据这个链接写直报：https://example.com/lawsuit",
            "urls": ["https://example.com/lawsuit"],
        },
        tools=gateway,
    )

    assert result.needs_clarification is False
    assert result.sources == ["https://example.com/lawsuit"]
    assert len(seen_payloads[0]["materials"]) == 1
    assert "开头策略：直入主题型" in seen_payloads[0]["planning_note"]


def test_direct_report_workflow_directs_to_topic_when_only_interpretation_is_available():
    policy_calls = []
    gateway = ToolGateway(
        allowed_tools=("web_reader", "policy_search", "llm_writer"),
        tools={
            "web_reader": lambda url: {
                "title": "用户素材",
                "text": "微众银行计划优化小微企业金融服务。",
                "url": url,
            },
            "policy_search": lambda query, limit=3, category=None: policy_calls.append(category)
            or [],
            "llm_writer": lambda payload: {"title": "降级政策背景标题", "body": "降级政策背景正文"},
        },
    )

    result = run(
        inputs={
            "text": "根据这个链接写直报：https://example.com/news",
            "urls": ["https://example.com/news"],
        },
        tools=gateway,
    )

    assert result.needs_clarification is False
    assert policy_calls
    assert all(category == "policy_original" for category in policy_calls)
    assert result.sources == ["https://example.com/news"]


def test_direct_report_workflow_does_not_add_bank_materials():
    seen_payloads = []
    calls = []
    gateway = ToolGateway(
        allowed_tools=("web_reader", "bank_materials", "policy_search", "llm_writer"),
        tools={
            "web_reader": lambda url: {
                "title": "用户素材",
                "text": "微众银行通过数字化方式提升小微企业融资服务效率。",
                "url": url,
            },
            "bank_materials": lambda user_instruction, materials, limit=3: calls.append(
                ("bank_materials", user_instruction, materials, limit)
            )
            or [
                {
                    "title": "微业贷服务小微企业",
                    "text": "微众银行素材摘录：微业贷累计申请企业法人客户超过760万。",
                    "url": "bank://e1",
                    "source": "bank_knowledge",
                }
            ],
            "policy_search": lambda query, limit=3, category=None: calls.append(
                ("policy_search", query, category)
            )
            or [
                {
                    "title": "关于提升小微企业金融服务质效的通知",
                    "snippet": "提升小微企业金融服务质效。",
                    "url": "https://www.nfra.gov.cn/policy",
                    "source": "nfra",
                    "category": "policy_original",
                }
            ],
            "llm_writer": lambda payload: seen_payloads.append(payload)
            or {"title": "标题", "body": "正文"},
        },
    )

    result = run(
        inputs={
            "text": "根据这个链接写直报：https://example.com/news",
            "urls": ["https://example.com/news"],
        },
        tools=gateway,
    )

    assert result.needs_clarification is False
    assert [item.get("source", "") for item in seen_payloads[0]["materials"]] == [
        "",
        "policy_knowledge",
    ]
    assert all(call[0] == "policy_search" for call in calls)


def test_direct_report_workflow_reads_uploaded_files_and_inline_material():
    seen_payloads = []
    read_calls = []
    gateway = ToolGateway(
        allowed_tools=("word_reader", "pdf_reader", "llm_writer"),
        tools={
            "word_reader": lambda path, *, allowed_root: read_calls.append(("word", Path(path).name, Path(allowed_root).name))
            or {
                "title": Path(path).name,
                "text": "Word 文件正文",
                "path": path,
            },
            "pdf_reader": lambda path, *, allowed_root: read_calls.append(("pdf", Path(path).name, Path(allowed_root).name))
            or {
                "title": Path(path).name,
                "text": "PDF 文件正文",
                "path": path,
            },
            "llm_writer": lambda payload: seen_payloads.append(payload) or {"title": "文件直报标题", "body": "文件直报正文"},
        },
    )

    result = run(
        inputs={
            "text": "请写直报，突出稳外贸。",
            "material_text": "这是补充的文字素材，说明该产品上线后已服务一批外贸企业。",
            "files": ["/tmp/job/input/材料A.docx", "/tmp/job/input/材料B.pdf"],
            "input_dir": "/tmp/job/input",
            "urls": [],
        },
        tools=gateway,
    )

    assert result.needs_clarification is False
    assert result.title == "文件直报标题"
    assert read_calls == [("word", "材料A.docx", "input"), ("pdf", "材料B.pdf", "input")]
    assert [item["title"] for item in seen_payloads[0]["materials"]] == [
        "材料A.docx",
        "材料B.pdf",
        "用户补充文字素材",
    ]


def test_direct_report_workflow_builds_planning_note_for_writer():
    seen_payloads = []
    gateway = ToolGateway(
        allowed_tools=("web_reader", "policy_search", "llm_writer"),
        tools={
            "web_reader": lambda url: {
                "title": "微众银行创新外贸贷助力稳外贸",
                "text": (
                    "2026年以来，我国外贸稳中有进。"
                    "微众银行联合合作伙伴推出“外贸贷”。"
                    "企业无需提供抵质押物，即可获得最高1000万元、期限最高3年的信用贷款。"
                    "上线以来，已为数百户外贸小微企业提供近5亿元信贷资金支持。"
                ),
                "url": url,
            },
            "policy_search": lambda query, limit=3, category=None: [
                {
                    "title": "关于做好稳外贸工作的通知",
                    "snippet": "加大对外贸企业融资支持力度。",
                    "url": "https://www.gov.cn/policy",
                    "source": "govcn",
                    "category": "policy_original",
                }
            ],
            "llm_writer": lambda payload: seen_payloads.append(payload) or {"title": "标题", "body": "正文"},
        },
    )

    run(
        inputs={
            "text": "根据这个链接写直报：https://example.com/news",
            "urls": ["https://example.com/news"],
        },
        tools=gateway,
    )

    assert "planning_note" in seen_payloads[0]
    assert "开头策略" in seen_payloads[0]["planning_note"]
    assert "稳外贸" in seen_payloads[0]["planning_note"]
    assert "1000万元" in seen_payloads[0]["planning_note"]
    assert "政策研究结论" in seen_payloads[0]["planning_note"]


def test_direct_report_workflow_only_keeps_one_policy_material():
    seen_payloads = []
    gateway = ToolGateway(
        allowed_tools=("web_reader", "policy_search", "llm_writer"),
        tools={
            "web_reader": lambda url: {
                "title": "微众银行优化小微企业融资服务",
                "text": "微众银行通过数字化方式提升小微企业融资服务效率，扩大普惠金融覆盖面。",
                "url": url,
            },
            "policy_search": lambda query, limit=3, category=None: [
                {
                    "title": "关于提升小微企业金融服务质效的通知",
                    "snippet": "提升小微企业金融服务质效，优化融资供给。",
                    "url": "https://www.nfra.gov.cn/policy-1",
                    "source": "nfra",
                    "category": "policy_original",
                },
                {
                    "title": "关于促进消费的若干意见",
                    "snippet": "着力扩大内需，促进消费。",
                    "url": "https://www.gov.cn/policy-2",
                    "source": "govcn",
                    "category": "policy_original",
                },
            ],
            "llm_writer": lambda payload: seen_payloads.append(payload) or {"title": "标题", "body": "正文"},
        },
    )

    result = run(
        inputs={
            "text": "根据这个链接写直报：https://example.com/news",
            "urls": ["https://example.com/news"],
        },
        tools=gateway,
    )

    assert result.needs_clarification is False
    assert [item.get("source", "") for item in seen_payloads[0]["materials"]] == ["", "policy_knowledge"]
    assert seen_payloads[0]["materials"][1]["title"] == "关于提升小微企业金融服务质效的通知"


def test_direct_report_workflow_directs_to_topic_when_local_policy_is_not_qualified():
    seen_payloads = []
    gateway = ToolGateway(
        allowed_tools=("web_reader", "policy_search", "llm_writer"),
        tools={
            "web_reader": lambda url: {
                "title": "微众银行优化小微企业融资服务",
                "text": "微众银行通过数字化方式提升小微企业融资服务效率，扩大普惠金融覆盖面。",
                "url": url,
            },
            "policy_search": lambda query, limit=3, category=None: [
                {
                    "title": "国务院关于促进消费的若干意见",
                    "snippet": "着力扩大内需，促进消费。",
                    "url": "https://www.gov.cn/consumption",
                    "source": "govcn",
                    "category": "policy_original",
                }
            ],
            "llm_writer": lambda payload: seen_payloads.append(payload)
            or {"title": "直入主题标题", "body": "直入主题正文"},
        },
    )

    result = run(
        inputs={
            "text": "根据这个链接写直报：https://example.com/news",
            "urls": ["https://example.com/news"],
        },
        tools=gateway,
    )

    assert result.needs_clarification is False
    assert result.sources == ["https://example.com/news"]
    assert len(seen_payloads[0]["materials"]) == 1
    assert "本稿直入主题" in seen_payloads[0]["planning_note"]


def test_direct_report_workflow_rewrites_when_hard_violations_found():
    calls = []

    def llm_writer(payload):
        calls.append(payload)
        task = payload.get("task")
        if task == "direct_report_critic":
            return {
                "violations": [
                    {
                        "rule": "single-main-line",
                        "severity": "hard",
                        "message": "正文引入与主线无关的第二业务线。",
                        "suggestion": "删除无关业务线，只围绕外贸贷机制一条主线展开。",
                    }
                ],
                "needs_clarification": False,
                "message": "",
            }

        direct_calls = [p for p in calls if p.get("task") == "direct_report"]
        if len(direct_calls) == 1:
            # 初稿：故意违反标题格式和个案单独成段
            return {
                "title": "名单制识别批量化担保助力外贸企业",
                "body": (
                    "名单制识别和批量化担保机制有效缓解了外贸企业融资难题。"
                    + "\n\n龙岗区一家电子消费品出口企业通过微众银行获批授信，及时补足备货资金。"
                    + "该企业负责人表示，这一机制大幅提升了融资效率。"
                    + "微众银行持续优化服务流程，扩大覆盖范围。" * 35
                ),
            }

        # 重写稿
        return {
            "title": "稳外贸：微众银行名单制识别批量化担保服务小微企业",
            "body": "微众银行" + "通过名单制识别和批量化担保机制服务外贸企业。" * 40,
        }

    gateway = ToolGateway(
        allowed_tools=("web_reader", "policy_materials", "llm_writer"),
        tools={
            "web_reader": lambda url: {
                "title": "微众银行创新外贸贷助力稳外贸",
                "text": "微众银行联合合作伙伴推出外贸贷，已为数百户企业提供近5亿元信贷资金支持。",
                "url": url,
            },
            "policy_materials": lambda user_instruction, materials, limit=3: [],
            "llm_writer": llm_writer,
        },
    )

    result = run(
        inputs={
            "text": "根据这个链接写直报：https://example.com/news",
            "urls": ["https://example.com/news"],
        },
        tools=gateway,
    )

    assert result.needs_clarification is False
    assert "微众银行" in result.title
    assert "龙岗区一家" not in result.body
    assert any(p.get("task") == "direct_report_critic" for p in calls)
    assert any(
        p.get("task") == "direct_report" and "revision_feedback" in p for p in calls
    )


def test_direct_report_workflow_runs_critic_even_when_deterministic_rules_pass():
    calls = []

    def llm_writer(payload):
        calls.append(payload)
        task = payload.get("task")
        if task == "direct_report_critic":
            return {
                "violations": [
                    {
                        "rule": "news-style",
                        "severity": "hard",
                        "message": "初稿虽然格式合规，但表达仍像新闻通稿。",
                        "suggestion": "改为直报口吻，压缩宣传性表达，突出政策背景、微众银行做法和成效。",
                    }
                ],
                "needs_clarification": False,
                "message": "",
            }

        direct_calls = [p for p in calls if p.get("task") == "direct_report"]
        if len(direct_calls) == 1:
            return {
                "title": "微众银行数字化服务提升小微企业融资可得性",
                "body": "微众银行" + "围绕小微企业融资需求持续完善数字化服务能力。" * 32,
            }

        return {
            "title": "微众银行优化数字化服务提升小微企业融资可得性",
            "body": "微众银行" + "围绕小微企业融资需求优化线上服务机制。" * 35,
        }

    gateway = ToolGateway(
        allowed_tools=("web_reader", "policy_materials", "llm_writer"),
        tools={
            "web_reader": lambda url: {
                "title": "微众银行服务小微企业",
                "text": "微众银行通过数字化方式提升小微企业金融服务可得性。",
                "url": url,
            },
            "policy_materials": lambda user_instruction, materials, limit=3: [],
            "llm_writer": llm_writer,
        },
    )

    result = run(
        inputs={
            "text": "根据这个链接写直报：https://example.com/news",
            "urls": ["https://example.com/news"],
            "direct_report_critic_mode": "rewrite",
        },
        tools=gateway,
    )

    assert result.needs_clarification is False
    assert result.title == "微众银行优化数字化服务提升小微企业融资可得性"
    assert any(p.get("task") == "direct_report_critic" for p in calls)
    assert any(
        p.get("task") == "direct_report" and "revision_feedback" in p for p in calls
    )


def test_direct_report_workflow_advisory_critic_does_not_rewrite():
    calls = []

    def llm_writer(payload):
        calls.append(payload)
        if payload.get("task") == "direct_report_critic":
            return {
                "violations": [
                    {
                        "rule": "news-style",
                        "severity": "hard",
                        "message": "初稿仍像新闻通稿。",
                        "suggestion": "改为直报口吻。",
                    }
                ],
                "needs_clarification": False,
                "message": "",
            }
        return {
            "title": "微众银行数字化服务提升小微企业融资可得性",
            "body": "微众银行" + "围绕小微企业融资需求持续完善数字化服务能力。" * 32,
        }

    gateway = ToolGateway(
        allowed_tools=("web_reader", "policy_materials", "llm_writer"),
        tools={
            "web_reader": lambda url: {
                "title": "微众银行服务小微企业",
                "text": "微众银行通过数字化方式提升小微企业金融服务可得性。",
                "url": url,
            },
            "policy_materials": lambda user_instruction, materials, limit=3: [],
            "llm_writer": llm_writer,
        },
    )

    result = run(
        inputs={
            "text": "根据这个链接写直报：https://example.com/news",
            "urls": ["https://example.com/news"],
            "direct_report_critic_mode": "advisory",
        },
        tools=gateway,
    )

    direct_calls = [p for p in calls if p.get("task") == "direct_report"]
    critic_calls = [p for p in calls if p.get("task") == "direct_report_critic"]
    assert result.needs_clarification is False
    assert result.title == "微众银行数字化服务提升小微企业融资可得性"
    assert len(direct_calls) == 1
    assert len(critic_calls) == 1
    assert "news-style" in result.message


def test_direct_report_workflow_off_critic_skips_critic_call():
    calls = []

    def llm_writer(payload):
        calls.append(payload)
        if payload.get("task") == "direct_report_critic":
            raise AssertionError("critic should not run in off mode")
        return {
            "title": "微众银行数字化服务提升小微企业融资可得性",
            "body": "微众银行" + "围绕小微企业融资需求持续完善数字化服务能力。" * 32,
        }

    gateway = ToolGateway(
        allowed_tools=("web_reader", "policy_materials", "llm_writer"),
        tools={
            "web_reader": lambda url: {
                "title": "微众银行服务小微企业",
                "text": "微众银行通过数字化方式提升小微企业金融服务可得性。",
                "url": url,
            },
            "policy_materials": lambda user_instruction, materials, limit=3: [],
            "llm_writer": llm_writer,
        },
    )

    result = run(
        inputs={
            "text": "根据这个链接写直报：https://example.com/news",
            "urls": ["https://example.com/news"],
            "direct_report_critic_mode": "off",
        },
        tools=gateway,
    )

    assert result.needs_clarification is False
    assert all(p.get("task") != "direct_report_critic" for p in calls)


def test_direct_report_workflow_returns_draft_when_critic_call_fails():
    calls = []

    def llm_writer(payload):
        calls.append(payload)
        if payload.get("task") == "direct_report_critic":
            raise RuntimeError("critic structured output failed")
        return {
            "title": "微众银行数字化服务提升小微企业融资可得性",
            "body": "微众银行" + "围绕小微企业融资需求持续完善数字化服务能力。" * 32,
        }

    gateway = ToolGateway(
        allowed_tools=("web_reader", "policy_materials", "llm_writer"),
        tools={
            "web_reader": lambda url: {
                "title": "微众银行服务小微企业",
                "text": "微众银行通过数字化方式提升小微企业金融服务可得性。",
                "url": url,
            },
            "policy_materials": lambda user_instruction, materials, limit=3: [],
            "llm_writer": llm_writer,
        },
    )

    result = run(
        inputs={
            "text": "根据这个链接写直报：https://example.com/news",
            "urls": ["https://example.com/news"],
        },
        tools=gateway,
    )

    assert result.needs_clarification is False
    assert result.title == "微众银行数字化服务提升小微企业融资可得性"
    assert any(p.get("task") == "direct_report_critic" for p in calls)


def test_direct_report_workflow_asks_when_material_is_only_single_enterprise_case():
    gateway = ToolGateway(
        allowed_tools=("web_reader", "llm_writer"),
        tools={
            "web_reader": lambda url: {
                "title": "龙岗区一家企业通过微众银行获批授信",
                "text": (
                    "龙岗区一家电子消费品出口企业通过微众银行全线上流程快速获批165万元授信，"
                    "及时补足备货流动资金，有效缓解了经营周转压力。"
                ),
                "url": url,
            },
            "llm_writer": lambda payload: (_ for _ in ()).throw(
                AssertionError("should not draft when material is only a single enterprise case")
            ),
        },
    )

    result = run(
        inputs={
            "text": "根据这个链接写直报：https://example.com/news",
            "urls": ["https://example.com/news"],
        },
        tools=gateway,
    )

    assert result.needs_clarification is True
    assert "单个企业个案" in result.message
    assert "机制" in result.message


def test_direct_report_workflow_prefers_shared_policy_research_tool():
    seen_payloads = []
    gateway = ToolGateway(
        allowed_tools=("web_reader", "policy_research", "llm_writer"),
        tools={
            "web_reader": lambda url: {
                "title": "微众银行优化小微企业融资服务",
                "text": "微众银行通过数字化方式提升小微企业融资服务效率，扩大普惠金融覆盖面。",
                "url": url,
            },
            "policy_research": lambda **kwargs: {
                "should_attach_policy": True,
                "decision_reason": "qualified_local_policy",
                "matched_themes": ["small_micro"],
                "retrieval_query": "小微企业 普惠金融 融资",
                "confidence": 0.88,
                "primary_policy": {
                    "title": "关于提升小微企业金融服务质效的通知",
                    "source": "nfra",
                    "category": "policy_original",
                    "publish_date": "2026-07-01",
                    "url": "https://www.nfra.gov.cn/policy",
                    "snippet": "提升小微企业金融服务质效，优化融资供给。",
                    "matched_terms": ["小微企业", "普惠金融"],
                    "relevance_score": 38,
                    "selection_reason": "命中政策主题：小微企业金融服务；匹配关键词：小微企业、普惠金融",
                },
                "alternative_policies": [],
            },
            "llm_writer": lambda payload: seen_payloads.append(payload) or {"title": "标题", "body": "正文"},
        },
    )

    result = run(
        inputs={
            "text": "根据这个链接写直报：https://example.com/news",
            "urls": ["https://example.com/news"],
        },
        tools=gateway,
    )

    assert result.needs_clarification is False
    assert result.sources == ["https://example.com/news", "https://www.nfra.gov.cn/policy"]
    assert seen_payloads[0]["materials"][1]["source"] == "policy_knowledge"
    assert seen_payloads[0]["materials"][1]["title"] == "关于提升小微企业金融服务质效的通知"
