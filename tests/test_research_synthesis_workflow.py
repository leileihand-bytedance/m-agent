from pathlib import Path
import zipfile

import pytest
from docx import Document
from pydantic import ValidationError

from app.review.official_format_checker import review_official_format
from app.platform.tools import ToolGateway
from skills.research_synthesis.schema import ResearchEvidencePoint, ResearchSynthesisPlan, ResearchSynthesisResult
from skills.research_synthesis.workflow import _source_label, run


def _gateway(
    *,
    documents: dict[str, dict[str, object]],
    plan: dict[str, object] | None = None,
    draft: dict[str, object] | None = None,
):
    calls: list[tuple[str, object]] = []

    def document_reader(path, **kwargs):
        calls.append(("document_reader", path))
        return documents[Path(path).name]

    def llm_writer(payload):
        calls.append(("llm_writer", payload))
        if payload["output_type"] is ResearchSynthesisPlan:
            return plan or {
                "title": "关于数字化转型工作的综合调研材料",
                "outline_type": "report_skeleton",
                "coverage_mode": "exhaustive",
                "classification_reason": "提纲已经给出报告章节。",
                "sections": [
                    {
                        "heading": "总体情况",
                        "subsections": [
                            {
                                "heading": "主要做法",
                                "evidence_points": [
                                    {
                                        "content": "科技部和运营部共同推进有关工作。",
                                        "source_labels": ["科技部", "运营部"],
                                    }
                                ],
                            }
                        ],
                    }
                ],
                "unresolved_conflicts": [],
                "missing_items": [],
                "needs_clarification": False,
                "message": "",
            }
        return draft or {
            "title": "关于数字化转型工作的综合调研材料",
            "body": "一、总体情况\n整合后的正文。",
            "sources": [],
            "needs_clarification": False,
            "message": "",
        }

    return (
        ToolGateway(
            allowed_tools=("document_reader", "llm_writer"),
            tools={"document_reader": document_reader, "llm_writer": llm_writer},
        ),
        calls,
    )


def test_research_evidence_disables_unverified_kinds_and_incomplete_derivations():
    image = ResearchEvidencePoint(
        content="图片表格可能含数据",
        source_labels=["业务部"],
        evidence_kind="image_candidate",
        usable=True,
    )
    external = ResearchEvidencePoint(
        content="另有补充数据",
        source_labels=["业务部"],
        evidence_kind="external_missing",
        usable=True,
    )
    derived_without_formula = ResearchEvidencePoint(
        content="合计约2.2万户",
        source_labels=["业务部", "风险部"],
        evidence_kind="derived",
        derivation_note="根据经验估算。",
        usable=True,
    )

    assert image.usable is False
    assert external.usable is False
    assert derived_without_formula.usable is False


def test_research_plan_records_outline_classification_and_coverage_decision():
    plan = ResearchSynthesisPlan.model_validate(
        {
            "outline_type": "policy_catalog",
            "coverage_mode": "selective",
            "classification_reason": "提纲列出政策范围，素材只与其中两项直接相关。",
            "required_headings": [],
            "selected_headings": ["延期还本", "信用贷款"],
            "omitted_outline_items": ["无直接业务证据的政策事项"],
        }
    )

    assert plan.outline_type == "policy_catalog"
    assert plan.coverage_mode == "selective"
    assert plan.selected_headings == ["延期还本", "信用贷款"]
    assert plan.omitted_outline_items == ["无直接业务证据的政策事项"]


def test_research_plan_requires_explicit_outline_classification():
    with pytest.raises(ValidationError):
        ResearchSynthesisPlan.model_validate({"title": "综合调研材料"})


def test_research_synthesis_requires_outline_and_source_materials():
    tools, calls = _gateway(documents={})

    result = run(inputs={"text": "请做综合调研", "files": []}, tools=tools)

    assert result.needs_clarification is True
    assert "提纲" in result.message
    assert not any(name == "llm_writer" for name, _ in calls)


def test_research_synthesis_does_not_guess_outline_when_filename_is_ambiguous(tmp_path):
    files = [tmp_path / "部门甲.docx", tmp_path / "部门乙.docx"]
    tools, calls = _gateway(
        documents={
            "部门甲.docx": {"title": "部门甲.docx", "text": "甲部门材料", "source": "uploaded_file"},
            "部门乙.docx": {"title": "部门乙.docx", "text": "乙部门材料", "source": "uploaded_file"},
        }
    )

    result = run(
        inputs={"text": "请按提纲整合综合调研材料", "files": [str(path) for path in files], "input_dir": str(tmp_path)},
        tools=tools,
    )

    assert result.needs_clarification is True
    assert "哪一份是调研提纲" in result.message
    assert "部门甲.docx" in result.message
    assert "部门乙.docx" in result.message
    assert not any(name == "llm_writer" for name, _ in calls)


def test_research_synthesis_uses_named_outline_and_preserves_material_roles(tmp_path):
    files = [tmp_path / "综合调研提纲.docx", tmp_path / "科技部素材.docx", tmp_path / "运营部素材.pdf"]
    tools, calls = _gateway(
        documents={
            "综合调研提纲.docx": {
                "title": "综合调研提纲.docx",
                "text": "一、总体情况\n二、主要做法\n三、问题与建议",
                "source": "uploaded_file",
            },
            "科技部素材.docx": {
                "title": "科技部素材.docx",
                "text": "科技部门提供的事实和数据。",
                "source": "uploaded_file",
            },
            "运营部素材.pdf": {
                "title": "运营部素材.pdf",
                "text": "运营部门提供的事实和数据。",
                "source": "uploaded_file",
            },
        }
    )

    result = run(
        inputs={"text": "请形成综合调研材料", "files": [str(path) for path in files], "input_dir": str(tmp_path)},
        tools=tools,
    )

    assert isinstance(result, ResearchSynthesisResult)
    assert result.needs_clarification is False
    assert result.sources == ["综合调研提纲.docx", "科技部素材.docx", "运营部素材.pdf"]
    llm_payloads = [payload for name, payload in calls if name == "llm_writer"]
    assert [payload["output_type"] for payload in llm_payloads] == [
        ResearchSynthesisPlan,
        ResearchSynthesisResult,
    ]
    plan_payload, draft_payload = llm_payloads
    assert plan_payload["prompt_path"] == "prompts/plan.md"
    assert plan_payload["materials"][0]["material_role"] == "outline"
    assert [item["material_role"] for item in plan_payload["materials"][1:]] == ["source", "source"]
    assert plan_payload["materials"][1]["source_label"] == "科技部"
    assert "先做材料台账" in plan_payload["planning_note"]
    assert "先判断提纲类型和覆盖方式" in plan_payload["planning_note"]
    assert "科技部和运营部共同推进有关工作" in draft_payload["planning_note"]
    assert "按提纲章节综合表达" in draft_payload["planning_note"]
    assert "不可使用 usable=false 的证据" in draft_payload["planning_note"]


def test_research_synthesis_creates_official_format_word_with_notes_and_no_images(tmp_path):
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    input_dir.mkdir()
    files = [input_dir / "调研提纲.docx", input_dir / "科技部素材.docx"]
    reminder = "【提醒：科技部素材含图片，请评估是否需要】"
    tools, calls = _gateway(
        documents={
            "调研提纲.docx": {
                "title": "调研提纲.docx",
                "text": "一、总体情况\n（一）主要做法\n1.具体情况",
                "source": "uploaded_file",
            },
            "科技部素材.docx": {
                "title": "科技部素材.docx",
                "text": f"已完成系统建设。\n{reminder}\n后续持续优化。",
                "source": "uploaded_file",
                "warning_codes": ["embedded_image_unread"],
            },
        },
        draft={
            "title": "关于数字化转型工作的综合调研材料",
            "body": (
                "一、总体情况\n"
                "（一）主要做法\n"
                "1.具体情况\n"
                f"科技部已完成系统建设。【来源：科技部素材】\n{reminder}"
            ),
            "sources": [],
            "needs_clarification": False,
            "message": "",
        },
    )

    result = run(
        inputs={
            "text": "请按提纲整合",
            "files": [str(path) for path in files],
            "input_dir": str(input_dir),
            "output_dir": str(output_dir),
        },
        tools=tools,
    )

    output_path = Path(result.output_file)
    assert output_path.exists()
    assert output_path.parent == output_dir
    assert output_path.suffix == ".docx"
    document = Document(output_path)
    paragraphs = [paragraph.text for paragraph in document.paragraphs]
    assert paragraphs[0] == "关于数字化转型工作的综合调研材料"
    assert paragraphs[1] == "【备注：请根据实际报送对象和通知要求补充报告开头。】"
    assert "1.具体情况" in paragraphs
    assert "一是具体情况" not in paragraphs
    assert "科技部已完成系统建设。【来源：科技部】" in paragraphs
    assert "【图片提醒：科技部本节素材包含1张图片，请评估是否需要】" in paragraphs
    assert paragraphs[-1] == "【备注：请根据实际报送要求补充报告结尾、附件、联系人、落款和日期。】"
    with zipfile.ZipFile(output_path) as archive:
        assert not any(name.startswith("word/media/") for name in archive.namelist())
    assert review_official_format(output_path, output_path.name).findings == []
    llm_payloads = [payload for name, payload in calls if name == "llm_writer"]
    assert reminder in llm_payloads[0]["planning_note"]
    assert "不要撰写报告开头和结尾" in llm_payloads[1]["planning_note"]


def test_research_synthesis_allows_user_to_name_outline_file(tmp_path):
    files = [tmp_path / "框架版本.docx", tmp_path / "业务部门材料.docx"]
    tools, calls = _gateway(
        documents={
            "框架版本.docx": {"title": "框架版本.docx", "text": "一、背景\n二、做法", "source": "uploaded_file"},
            "业务部门材料.docx": {"title": "业务部门材料.docx", "text": "业务事实。", "source": "uploaded_file"},
        }
    )

    result = run(
        inputs={
            "text": "框架版本.docx 是提纲，请按它整合综合调研材料",
            "files": [str(path) for path in files],
            "input_dir": str(tmp_path),
        },
        tools=tools,
    )

    assert result.needs_clarification is False
    llm_payload = next(payload for name, payload in calls if name == "llm_writer")
    assert llm_payload["materials"][0]["title"] == "框架版本.docx"


def test_research_synthesis_prefers_exact_outline_filename_over_department_replies(tmp_path):
    files = [
        tmp_path / "调研提纲.docx",
        tmp_path / "个体工商-调研提纲_个体工商金融项目组.docx",
        tmp_path / "企业风险-调研提纲及反馈内容汇总.docx",
    ]
    tools, calls = _gateway(
        documents={
            "调研提纲.docx": {
                "title": "调研提纲.docx",
                "text": "调研提纲\n一、政策落实情况\n二、存在问题",
                "source": "uploaded_file",
            },
            "个体工商-调研提纲_个体工商金融项目组.docx": {
                "title": "个体工商-调研提纲_个体工商金融项目组.docx",
                "text": "调研提纲\n一、政策落实情况\n回复：我行已完成相关工作。",
                "source": "uploaded_file",
            },
            "企业风险-调研提纲及反馈内容汇总.docx": {
                "title": "企业风险-调研提纲及反馈内容汇总.docx",
                "text": "调研提纲及反馈内容\n一、政策落实情况\n我行反馈如下。",
                "source": "uploaded_file",
            },
        }
    )

    result = run(
        inputs={
            "text": "请按提纲整合综合调研材料",
            "files": [str(path) for path in files],
            "input_dir": str(tmp_path),
        },
        tools=tools,
    )

    assert result.needs_clarification is False
    llm_payload = next(payload for name, payload in calls if name == "llm_writer")
    assert llm_payload["materials"][0]["title"] == "调研提纲.docx"


def test_research_synthesis_uses_response_content_to_distinguish_outline(tmp_path):
    files = [tmp_path / "附件1-调研框架.docx", tmp_path / "部门甲-调研提纲.docx"]
    tools, calls = _gateway(
        documents={
            "附件1-调研框架.docx": {
                "title": "附件1-调研框架.docx",
                "text": "一、政策落实情况\n二、存在问题",
                "source": "uploaded_file",
            },
            "部门甲-调研提纲.docx": {
                "title": "部门甲-调研提纲.docx",
                "text": "一、政策落实情况\n回复：我行已完成相关工作。",
                "source": "uploaded_file",
            },
        }
    )

    result = run(
        inputs={
            "text": "请按提纲整合综合调研材料",
            "files": [str(path) for path in files],
            "input_dir": str(tmp_path),
        },
        tools=tools,
    )

    assert result.needs_clarification is False
    llm_payload = next(payload for name, payload in calls if name == "llm_writer")
    assert llm_payload["materials"][0]["title"] == "附件1-调研框架.docx"


def test_research_synthesis_requires_at_least_one_material_beyond_outline(tmp_path):
    outline = tmp_path / "调研提纲.docx"
    tools, calls = _gateway(
        documents={
            "调研提纲.docx": {"title": "调研提纲.docx", "text": "一、总体情况", "source": "uploaded_file"},
        }
    )

    result = run(
        inputs={"text": "请做综合调研", "files": [str(outline)], "input_dir": str(tmp_path)},
        tools=tools,
    )

    assert result.needs_clarification is True
    assert "部门素材" in result.message
    assert not any(name == "llm_writer" for name, _ in calls)


def test_research_synthesis_stops_when_outline_mode_remains_unknown(tmp_path):
    files = [tmp_path / "调研提纲.docx", tmp_path / "业务部素材.docx"]
    tools, calls = _gateway(
        documents={
            "调研提纲.docx": {
                "title": "调研提纲.docx",
                "text": "有关事项和问题，请结合情况说明。",
                "source": "uploaded_file",
            },
            "业务部素材.docx": {
                "title": "业务部素材.docx",
                "text": "已开展相关工作。",
                "source": "uploaded_file",
            },
        },
        plan={
            "title": "综合调研材料",
            "outline_type": "unknown",
            "coverage_mode": "exhaustive",
            "classification_reason": "无法判断是逐项答复还是按相关性选取。",
            "sections": [
                {
                    "heading": "有关事项",
                    "subsections": [
                        {
                            "heading": "相关工作",
                            "evidence_points": [
                                {
                                    "content": "已开展相关工作。",
                                    "source_labels": ["业务部"],
                                }
                            ],
                        }
                    ],
                }
            ],
            "needs_clarification": False,
            "message": "",
        },
    )

    result = run(
        inputs={"text": "请整合", "files": [str(path) for path in files], "input_dir": str(tmp_path)},
        tools=tools,
    )

    assert result.needs_clarification is True
    assert "逐项覆盖" in result.message
    assert len([payload for name, payload in calls if name == "llm_writer"]) == 1


def test_research_synthesis_normalizes_real_world_department_filenames():
    assert _source_label({"title": "个体工商-调研提纲_个体工商金融项目组.docx"}) == "个体工商金融项目组"
    assert _source_label({"title": "零售信贷-附件1-20220722零售信贷部.docx"}) == "零售信贷部"
    assert _source_label({"title": "生活金融-调研提纲（1）.docx"}) == "生活金融部"
    assert _source_label({"title": "汽车金融部-调研反馈.docx"}) == "汽车金融部"
    assert _source_label({"title": "企业风险-调研提纲及反馈内容汇总.docx"}) == "企业风险部"


def test_research_synthesis_repairs_heading_source_and_duplicate_image_labels(tmp_path):
    files = [
        tmp_path / "调研提纲.docx",
        tmp_path / "企业风险-调研提纲及反馈内容汇总.docx",
        tmp_path / "汽车金融部-调研反馈.docx",
    ]
    enterprise_reminder = "【提醒：企业风险部素材含图片，请评估是否需要】"
    auto_reminder = "【提醒：汽车金融部素材含图片，请评估是否需要】"
    tools, calls = _gateway(
        documents={
            "调研提纲.docx": {
                "title": "调研提纲.docx",
                "text": "1.总体情况——牵头部门：办公室\n2.主要做法。——牵头部门：办公室",
                "source": "uploaded_file",
            },
            "企业风险-调研提纲及反馈内容汇总.docx": {
                "title": "企业风险-调研提纲及反馈内容汇总.docx",
                "text": f"已服务100户。\n{enterprise_reminder}",
                "source": "uploaded_file",
            },
            "汽车金融部-调研反馈.docx": {
                "title": "汽车金融部-调研反馈.docx",
                "text": f"已服务200户。\n{auto_reminder}\n{auto_reminder}",
                "source": "uploaded_file",
            },
        },
        draft={
            "title": "综合调研材料",
            "body": (
                "1.总体情况——牵头部门：办公室\n"
                "（1）服务进展\n"
                "【来源：企业风险-调研提纲及反馈内容汇总】已服务100户，汽车金融部已服务200户。"
                "【来源：汽车金融部-调研反馈】\n"
                f"{auto_reminder}\n{auto_reminder}\n"
                "2.主要做法。——牵头部门：办公室\n"
                "【材料待补充】"
            ),
            "sources": [],
            "needs_clarification": False,
            "message": "模型原始消息",
        },
    )

    result = run(
        inputs={"text": "请按提纲整合", "files": [str(path) for path in files], "input_dir": str(tmp_path)},
        tools=tools,
    )

    assert result.body.startswith("一、总体情况\n（一）服务进展")
    assert "二、主要做法" in result.body
    assert "牵头部门" not in result.body
    assert "企业风险-调研提纲及反馈内容汇总" not in result.body
    assert "汽车金融部-调研反馈" not in result.body
    assert "已服务100户，汽车金融部已服务200户。【来源：企业风险部、汽车金融部】" in result.body
    assert result.body.count("【图片提醒：汽车金融部本节素材包含2张图片，请评估是否需要】") == 1
    assert result.message == "已按1份提纲和2份部门素材生成综合调研 Word 初稿。"
    assert len([payload for name, payload in calls if name == "llm_writer"]) == 2


def test_research_synthesis_keeps_missing_outline_topic_visible(tmp_path):
    files = [tmp_path / "调研提纲.docx", tmp_path / "运营部素材.docx"]
    tools, _ = _gateway(
        documents={
            "调研提纲.docx": {
                "title": "调研提纲.docx",
                "text": "1.总体情况\n2.存在问题\n3.工作建议",
                "source": "uploaded_file",
            },
            "运营部素材.docx": {
                "title": "运营部素材.docx",
                "text": "已完成有关工作。",
                "source": "uploaded_file",
            },
        },
        plan={
            "title": "综合调研材料",
            "outline_type": "questionnaire",
            "coverage_mode": "exhaustive",
            "classification_reason": "提纲按连续问题要求逐项答复。",
            "required_headings": ["总体情况", "存在问题", "工作建议"],
            "selected_headings": ["总体情况", "存在问题", "工作建议"],
            "sections": [],
            "missing_items": ["存在问题"],
            "needs_clarification": False,
        },
        draft={
            "title": "综合调研材料",
            "body": "1.总体情况\n已完成有关工作。【来源：运营部】\n3.工作建议\n【材料待补充】",
            "sources": [],
            "needs_clarification": False,
            "message": "",
        },
    )

    result = run(
        inputs={"text": "请按提纲整合", "files": [str(path) for path in files], "input_dir": str(tmp_path)},
        tools=tools,
    )

    assert result.body.index("一、总体情况") < result.body.index("二、存在问题") < result.body.index("三、工作建议")
    assert "二、存在问题\n【材料待补充：该提纲主题未在模型初稿中形成内容，请人工核对。】" in result.body


def test_research_synthesis_selective_policy_catalog_only_keeps_supported_topics(tmp_path):
    files = [tmp_path / "政策调研提纲.docx", tmp_path / "普惠业务部素材.docx"]
    tools, _ = _gateway(
        documents={
            "政策调研提纲.docx": {
                "title": "政策调研提纲.docx",
                "text": "一、财政支持政策\n二、金融支持政策\n三、就业保障政策",
                "source": "uploaded_file",
            },
            "普惠业务部素材.docx": {
                "title": "普惠业务部素材.docx",
                "text": "已落实延期还本，并持续增加信用贷款支持。",
                "source": "uploaded_file",
            },
        },
        plan={
            "title": "政策落实情况综合调研材料",
            "outline_type": "policy_catalog",
            "coverage_mode": "selective",
            "classification_reason": "提纲是政策目录，素材只支撑两项金融政策。",
            "required_headings": [],
            "selected_headings": ["延期还本", "信用贷款"],
            "omitted_outline_items": ["财政支持政策：无直接证据", "就业保障政策：无直接证据"],
            "sections": [
                {
                    "heading": "延期还本",
                    "subsections": [
                        {
                            "heading": "落实情况",
                            "evidence_points": [
                                {
                                    "content": "已落实延期还本。",
                                    "source_labels": ["普惠业务部"],
                                    "evidence_kind": "source_text",
                                }
                            ],
                        }
                    ],
                },
                {
                    "heading": "信用贷款",
                    "subsections": [
                        {
                            "heading": "落实情况",
                            "evidence_points": [
                                {
                                    "content": "持续增加信用贷款支持。",
                                    "source_labels": ["普惠业务部"],
                                    "evidence_kind": "source_text",
                                }
                            ],
                        }
                    ],
                },
            ],
            "needs_clarification": False,
        },
        draft={
            "title": "政策落实情况综合调研材料",
            "body": (
                "1.延期还本\n已落实延期还本。【来源：普惠业务部】\n"
                "2.信用贷款\n持续增加信用贷款支持。【来源：普惠业务部】\n"
                "3.就业保障政策\n没有直接材料也写入正文。"
            ),
            "sources": [],
            "needs_clarification": False,
        },
    )

    result = run(
        inputs={"text": "请按提纲范围选择相关事项整合", "files": [str(path) for path in files], "input_dir": str(tmp_path)},
        tools=tools,
    )

    assert result.body.startswith("一、延期还本")
    assert "二、信用贷款" in result.body
    assert "财政支持政策" not in result.body
    assert "就业保障政策" not in result.body
    assert "没有直接材料也写入正文" not in result.body


def test_research_synthesis_stops_when_selective_outline_has_no_usable_topic(tmp_path):
    files = [tmp_path / "政策调研提纲.docx", tmp_path / "业务部素材.docx"]
    tools, calls = _gateway(
        documents={
            "政策调研提纲.docx": {
                "title": "政策调研提纲.docx",
                "text": "一、融资支持政策\n二、就业保障政策",
                "source": "uploaded_file",
            },
            "业务部素材.docx": {
                "title": "业务部素材.docx",
                "text": "【提醒：业务部素材含图片，请评估是否需要】",
                "source": "uploaded_file",
            },
        },
        plan={
            "title": "政策落实情况综合调研材料",
            "outline_type": "policy_catalog",
            "coverage_mode": "selective",
            "classification_reason": "当前只有图片提醒，没有可读取文字证据。",
            "selected_headings": ["融资支持政策"],
            "sections": [
                {
                    "heading": "融资支持政策",
                    "subsections": [
                        {
                            "heading": "落实情况",
                            "evidence_points": [
                                {
                                    "content": "图片可能包含融资数据。",
                                    "source_labels": ["业务部"],
                                    "evidence_kind": "image_candidate",
                                    "usable": True,
                                }
                            ],
                        }
                    ],
                }
            ],
            "needs_clarification": False,
        },
    )

    result = run(
        inputs={"text": "请按相关性选取", "files": [str(path) for path in files], "input_dir": str(tmp_path)},
        tools=tools,
    )

    assert result.needs_clarification is True
    assert "没有可直接入稿的文字证据" in result.message
    assert len([payload for name, payload in calls if name == "llm_writer"]) == 1


def test_research_synthesis_marks_untraceable_numbers_and_keeps_verified_derivation(tmp_path):
    files = [tmp_path / "问卷调研提纲.docx", tmp_path / "业务部素材.docx", tmp_path / "风险部素材.docx"]
    image_reminder = "【提醒：业务部素材含图片，请评估是否需要】"
    tools, _ = _gateway(
        documents={
            "问卷调研提纲.docx": {
                "title": "问卷调研提纲.docx",
                "text": "一、服务情况",
                "source": "uploaded_file",
            },
            "业务部素材.docx": {
                "title": "业务部素材.docx",
                "text": f"已服务16884户。\n{image_reminder}",
                "source": "uploaded_file",
            },
            "风险部素材.docx": {
                "title": "风险部素材.docx",
                "text": "已服务5092户。",
                "source": "uploaded_file",
            },
        },
        plan={
            "title": "服务情况综合调研材料",
            "outline_type": "questionnaire",
            "coverage_mode": "exhaustive",
            "classification_reason": "提纲要求回答服务情况。",
            "required_headings": ["服务情况"],
            "selected_headings": ["服务情况"],
            "sections": [
                {
                    "heading": "服务情况",
                    "subsections": [
                        {
                            "heading": "服务成效",
                            "evidence_points": [
                                {
                                    "content": "业务部已服务16884户。",
                                    "source_labels": ["业务部"],
                                    "evidence_kind": "source_text",
                                },
                                {
                                    "content": "风险部已服务5092户。",
                                    "source_labels": ["风险部"],
                                    "evidence_kind": "source_text",
                                },
                                {
                                    "content": "合计21976户，约2.2万户。",
                                    "source_labels": ["业务部", "风险部"],
                                    "evidence_kind": "derived",
                                    "derivation_note": "16884+5092=21976，按万户保留1位小数约为2.2万户。",
                                },
                                {
                                    "content": "图片表格可能显示59.14%。",
                                    "source_labels": ["业务部"],
                                    "evidence_kind": "image_candidate",
                                    "usable": True,
                                },
                                {
                                    "content": "另有8800户获得支持。",
                                    "source_labels": ["业务部"],
                                    "evidence_kind": "external_missing",
                                    "usable": True,
                                },
                                {
                                    "content": "不存在部门声称另有9999户。",
                                    "source_labels": ["不存在部门"],
                                    "evidence_kind": "source_text",
                                    "usable": True,
                                },
                            ],
                            "image_reminders": [image_reminder],
                        }
                    ],
                }
            ],
            "needs_clarification": False,
        },
        draft={
            "title": "服务情况综合调研材料",
            "body": (
                "一、服务情况\n"
                "共服务约2.2万户。【来源：业务部、风险部】\n"
                "图片表格显示占比59.14%。【来源：业务部】\n"
                "另有8800户获得支持。\n"
                "另有9999户获得支持。【来源：业务部】\n"
                f"{image_reminder}"
            ),
            "sources": [],
            "needs_clarification": False,
        },
    )

    result = run(
        inputs={"text": "请按提纲逐项整合", "files": [str(path) for path in files], "input_dir": str(tmp_path)},
        tools=tools,
    )

    verified_line = next(line for line in result.body.splitlines() if "约2.2万户" in line)
    image_number_line = next(line for line in result.body.splitlines() if "59.14%" in line)
    external_number_line = next(line for line in result.body.splitlines() if "8800户" in line)
    invalid_source_line = next(line for line in result.body.splitlines() if "9999户" in line)
    assert "来源待核对" not in verified_line
    assert "【来源待核对：该段包含材料台账未登记的数据，请人工核对。】" in image_number_line
    assert "【来源待核对：该段包含材料台账未登记的数据，请人工核对。】" in external_number_line
    assert "【来源待核对：该段包含材料台账未登记的数据，请人工核对。】" in invalid_source_line
    assert "【来源：待核对】" in external_number_line
    assert "【图片提醒：业务部本节素材包含1张图片，请评估是否需要】" in result.body
