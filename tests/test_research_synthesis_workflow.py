from pathlib import Path
import zipfile

from docx import Document

from app.review.official_format_checker import review_official_format
from app.platform.tools import ToolGateway
from skills.research_synthesis.schema import ResearchSynthesisResult
from skills.research_synthesis.workflow import run


def _gateway(*, documents: dict[str, dict[str, object]], draft: dict[str, object] | None = None):
    calls: list[tuple[str, object]] = []

    def document_reader(path, **kwargs):
        calls.append(("document_reader", path))
        return documents[Path(path).name]

    def llm_writer(payload):
        calls.append(("llm_writer", payload))
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
    llm_payload = next(payload for name, payload in calls if name == "llm_writer")
    assert llm_payload["output_type"] is ResearchSynthesisResult
    assert llm_payload["materials"][0]["material_role"] == "outline"
    assert [item["material_role"] for item in llm_payload["materials"][1:]] == ["source", "source"]
    assert "严格保留提纲层级、顺序和章节名称" in llm_payload["planning_note"]
    assert llm_payload["materials"][1]["source_label"] == "科技部"
    assert "保留部门来源标签" in llm_payload["planning_note"]


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
    assert "科技部已完成系统建设。【来源：科技部素材】" in paragraphs
    assert reminder in paragraphs
    assert paragraphs[-1] == "【备注：请根据实际报送要求补充报告结尾、附件、联系人、落款和日期。】"
    with zipfile.ZipFile(output_path) as archive:
        assert not any(name.startswith("word/media/") for name in archive.namelist())
    assert review_official_format(output_path, output_path.name).findings == []
    llm_payload = next(payload for name, payload in calls if name == "llm_writer")
    assert reminder in llm_payload["planning_note"]
    assert "不要撰写报告开头和结尾" in llm_payload["planning_note"]


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
