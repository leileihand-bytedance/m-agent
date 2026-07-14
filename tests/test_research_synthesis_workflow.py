from pathlib import Path

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
