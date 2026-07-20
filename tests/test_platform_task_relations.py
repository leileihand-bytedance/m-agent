from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.platform.task_relations import (
    MaterialRole,
    RelationAction,
    TaskCardStatus,
    TaskRelation,
    PydanticTaskRelationClassifier,
    TaskRelationRepository,
    TaskRelationService,
)


def test_semantic_classifier_builds_bounded_prompt_and_structured_decision():
    seen = {}

    def runner(*, instructions, prompt, output_type):
        seen["instructions"] = instructions
        seen["prompt"] = prompt
        seen["output_type"] = output_type
        return {
            "relation": "continue",
            "target_task_id": "task-a",
            "material_role": "none",
            "suggested_skill_id": "writer1",
            "action": "execute",
            "confidence": 0.93,
            "reason": "用户提到该稿主题",
            "question": "",
            "parent_task_id": "",
        }

    classifier = PydanticTaskRelationClassifier(runner)
    decision = classifier(
        text="这一部分只保留一个案例",
        tasks=[
            {
                "task_id": "task-a",
                "title": "普惠金融简报",
                "skill_id": "writer1",
                "status": "completed",
                "content_summary": "一、政策背景；二、案例",
                "pending_question": "",
            }
        ],
        route_skill_id="",
        has_new_material=False,
        selected_task_id="task-a",
    )

    assert decision["target_task_id"] == "task-a"
    assert decision["confidence"] == 0.93
    assert "只能从候选任务中选择" in seen["instructions"]
    assert "task-a" in seen["prompt"]
    assert '"is_selected":true' in seen["prompt"]
    assert "这一部分只保留一个案例" in seen["prompt"]



def _create_completed_task(
    repository: TaskRelationRepository,
    *,
    task_id: str,
    user_id: str = "user-001",
    skill_id: str = "writer1",
    title: str,
    body: str,
    parent_task_id: str = "",
) -> None:
    repository.create_task(
        task_id=task_id,
        channel="wecom",
        user_id=user_id,
        skill_id=skill_id,
        title=title,
        status=TaskCardStatus.QUEUED,
        current_job_id=f"{task_id}-job-1",
        parent_task_id=parent_task_id,
        materials=[("url", f"https://example.com/{task_id}", MaterialRole.NEW_TASK)],
    )
    repository.record_result(
        task_id=task_id,
        job_id=f"{task_id}-job-1",
        title=title,
        body=body,
        status=TaskCardStatus.COMPLETED,
    )


@pytest.mark.parametrize(
    "case",
    json.loads(
        (Path(__file__).parent / "fixtures" / "task_relation_cases.json").read_text(
            encoding="utf-8"
        )
    ),
    ids=lambda case: case["name"],
)
def test_desensitized_task_relation_regression_cases(tmp_path: Path, case: dict[str, object]):
    repository = TaskRelationRepository(tmp_path / f"{case['name']}.sqlite3")
    _create_completed_task(
        repository,
        task_id="task-a",
        title="普惠金融服务简报",
        body="一、政策背景\n二、服务案例",
    )
    _create_completed_task(
        repository,
        task_id="task-b",
        title="数字金融政策直报",
        body="一、政策背景\n二、实践成效",
        skill_id="direct_report",
    )

    decision = TaskRelationService(repository).resolve_text(
        channel="wecom",
        user_id="user-001",
        text=str(case["text"]),
        route_skill_id=(
            str(case["route_skill_id"])
            if case.get("route_skill_id") is not None
            else None
        ),
        has_new_material=bool(case["has_new_material"]),
    )

    assert decision.relation.value == case["expected_relation"]
    assert decision.target_task_id == case["expected_target"]
    assert decision.material_role.value == case["expected_material_role"]


def test_repository_keeps_multiple_cards_and_versions_isolated_by_user(tmp_path: Path):
    repository = TaskRelationRepository(tmp_path / "relations.sqlite3")
    _create_completed_task(
        repository,
        task_id="task-a",
        title="微众银行小微金融服务简报",
        body="一、服务背景\n二、主要做法",
    )
    _create_completed_task(
        repository,
        task_id="task-b",
        title="微众银行数字金融直报",
        body="一、政策背景\n二、实践成效",
        skill_id="direct_report",
    )
    _create_completed_task(
        repository,
        task_id="task-other",
        user_id="user-002",
        title="其他用户简报",
        body="其他用户正文",
    )

    repository.bind_job(
        task_id="task-a",
        job_id="task-a-job-2",
        relation=TaskRelation.CONTINUE,
        status=TaskCardStatus.RUNNING,
    )
    repository.record_result(
        task_id="task-a",
        job_id="task-a-job-2",
        title="微众银行小微金融服务简报（修改稿）",
        body="一、服务背景\n二、主要做法\n三、工作成效",
        status=TaskCardStatus.COMPLETED,
    )

    cards = repository.list_tasks(channel="wecom", user_id="user-001")
    assert {card.task_id for card in cards} == {"task-a", "task-b"}
    card_a = repository.get_task("task-a", channel="wecom", user_id="user-001")
    assert card_a.current_version == 2
    assert card_a.current_job_id == "task-a-job-2"
    assert "工作成效" in card_a.content_summary
    assert repository.version_job_id("task-a", 1) == "task-a-job-1"
    assert repository.version_job_id("task-a", 2) == "task-a-job-2"


def test_background_completion_does_not_steal_the_users_selected_task(tmp_path: Path):
    repository = TaskRelationRepository(tmp_path / "relations.sqlite3")
    repository.create_task(
        task_id="task-a",
        channel="wecom",
        user_id="user-001",
        skill_id="writer1",
        title="任务 A",
        status=TaskCardStatus.QUEUED,
        current_job_id="task-a-job",
    )
    repository.create_task(
        task_id="task-b",
        channel="wecom",
        user_id="user-001",
        skill_id="writer1",
        title="任务 B",
        status=TaskCardStatus.QUEUED,
        current_job_id="task-b-job",
    )

    repository.record_result(
        task_id="task-a",
        job_id="task-a-job",
        title="任务 A 完成稿",
        body="任务 A 正文",
        status=TaskCardStatus.COMPLETED,
    )

    assert repository.selected_task(channel="wecom", user_id="user-001").task_id == "task-b"


def test_service_resolves_continue_supplement_derive_new_switch_and_cancel(tmp_path: Path):
    repository = TaskRelationRepository(tmp_path / "relations.sqlite3")
    _create_completed_task(
        repository,
        task_id="brief-task",
        title="小微金融服务简报",
        body="第一段介绍背景，第二段介绍信贷审批案例。",
    )
    _create_completed_task(
        repository,
        task_id="report-task",
        title="数字金融政策直报",
        body="第一段介绍政策，第二段介绍业务成效。",
        skill_id="direct_report",
    )
    service = TaskRelationService(repository)

    continued = service.resolve_text(
        channel="wecom",
        user_id="user-001",
        text="把小微金融服务简报的第二段压缩一点",
        route_skill_id=None,
    )
    assert continued.relation is TaskRelation.CONTINUE
    assert continued.target_task_id == "brief-task"
    assert continued.action is RelationAction.EXECUTE

    supplemented = service.resolve_text(
        channel="wecom",
        user_id="user-001",
        text="把这份新数据补到数字金融政策直报第二段",
        route_skill_id=None,
        has_new_material=True,
    )
    assert supplemented.relation is TaskRelation.ADD_MATERIAL
    assert supplemented.material_role is MaterialRole.SUPPLEMENT
    assert supplemented.target_task_id == "report-task"

    derived = service.resolve_text(
        channel="wecom",
        user_id="user-001",
        text="沿用小微金融服务简报的结构，根据下面的新材料另写一份",
        route_skill_id="writer1",
        has_new_material=True,
    )
    assert derived.relation is TaskRelation.DERIVE
    assert derived.parent_task_id == "brief-task"
    assert derived.target_task_id == "brief-task"

    new_task = service.resolve_text(
        channel="wecom",
        user_id="user-001",
        text="新任务：根据这个链接写一篇直报 https://example.com/new",
        route_skill_id="direct_report",
        has_new_material=True,
    )
    assert new_task.relation is TaskRelation.NEW_TASK
    assert new_task.target_task_id == ""

    switched = service.resolve_text(
        channel="wecom",
        user_id="user-001",
        text="切换到小微金融服务简报",
        route_skill_id=None,
    )
    assert switched.relation is TaskRelation.SWITCH
    assert switched.action is RelationAction.SELECT
    assert repository.selected_task(channel="wecom", user_id="user-001").task_id == "brief-task"

    cancelled = service.resolve_text(
        channel="wecom",
        user_id="user-001",
        text="取消数字金融政策直报这个任务",
        route_skill_id=None,
    )
    assert cancelled.relation is TaskRelation.CANCEL
    assert cancelled.action is RelationAction.CANCEL
    assert cancelled.target_task_id == "report-task"


def test_followup_revision_prefers_the_current_draft_before_semantic_matching(
    tmp_path: Path,
) -> None:
    repository = TaskRelationRepository(tmp_path / "relations.sqlite3")
    _create_completed_task(
        repository,
        task_id="older-brief",
        title="某银行获评行业奖项简报",
        body="简报正文",
    )
    _create_completed_task(
        repository,
        task_id="current-report",
        title="某银行推进智能运营直报",
        body="直报正文",
        skill_id="direct_report",
    )
    semantic_calls: list[str] = []

    def misleading_semantic_classifier(**kwargs: object) -> dict[str, object]:
        semantic_calls.append(str(kwargs.get("text", "")))
        return {
            "relation": "continue",
            "target_task_id": "older-brief",
            "material_role": "none",
            "suggested_skill_id": "writer1",
            "action": "execute",
            "confidence": 0.95,
            "reason": "错误地按奖项关键词选择旧稿",
        }

    decision = TaskRelationService(
        repository,
        semantic_classifier=misleading_semantic_classifier,
    ).resolve_text(
        channel="wecom",
        user_id="user-001",
        text="这是典型的动态类稿件，应该直入主题，从获奖引入，再改一下",
        route_skill_id=None,
    )

    assert decision.relation is TaskRelation.CONTINUE
    assert decision.target_task_id == "current-report"
    assert semantic_calls == []


def test_explicit_document_type_overrides_a_different_current_draft(tmp_path: Path) -> None:
    repository = TaskRelationRepository(tmp_path / "relations.sqlite3")
    _create_completed_task(
        repository,
        task_id="report-task",
        title="某银行推进智能运营直报",
        body="直报正文",
        skill_id="direct_report",
    )
    _create_completed_task(
        repository,
        task_id="selected-brief",
        title="某银行普惠金融简报",
        body="简报正文",
    )

    decision = TaskRelationService(repository).resolve_text(
        channel="wecom",
        user_id="user-001",
        text="继续改这个直报，开头直接引入获奖",
        route_skill_id=None,
    )

    assert decision.relation is TaskRelation.CONTINUE
    assert decision.target_task_id == "report-task"


def test_ambiguous_revision_asks_once_and_recovers_after_restart(tmp_path: Path):
    db_path = tmp_path / "relations.sqlite3"
    repository = TaskRelationRepository(db_path)
    _create_completed_task(
        repository,
        task_id="task-a",
        title="普惠金融简报",
        body="普惠金融正文",
    )
    _create_completed_task(
        repository,
        task_id="task-b",
        title="数字金融简报",
        body="数字金融正文",
    )
    service = TaskRelationService(repository)

    ambiguous = service.resolve_text(
        channel="wecom",
        user_id="user-001",
        text="第二段再短一点",
        route_skill_id=None,
    )

    assert ambiguous.relation is TaskRelation.NEEDS_CLARIFICATION
    assert ambiguous.action is RelationAction.ASK
    assert "普惠金融简报" in ambiguous.question
    assert "数字金融简报" in ambiguous.question

    restarted = TaskRelationService(TaskRelationRepository(db_path))
    resumed = restarted.resolve_text(
        channel="wecom",
        user_id="user-001",
        text="数字金融那篇",
        route_skill_id=None,
    )

    assert resumed.relation is TaskRelation.ANSWER_CLARIFICATION
    assert resumed.target_task_id == "task-b"
    assert resumed.effective_text == "第二段再短一点"
    assert restarted.repository.pending_decision(channel="wecom", user_id="user-001") is None


def test_pending_clarification_can_be_corrected_to_new_independent_task(tmp_path: Path):
    repository = TaskRelationRepository(tmp_path / "relations.sqlite3")
    _create_completed_task(
        repository,
        task_id="task-a",
        title="普惠金融简报",
        body="普惠金融正文",
    )
    _create_completed_task(
        repository,
        task_id="task-b",
        title="数字金融简报",
        body="数字金融正文",
    )
    service = TaskRelationService(repository)
    service.resolve_text(
        channel="wecom",
        user_id="user-001",
        text="把这一段改得更正式",
        route_skill_id=None,
    )

    corrected = service.resolve_text(
        channel="wecom",
        user_id="user-001",
        text="不是改旧稿，是另写一份",
        route_skill_id="rewrite",
    )

    assert corrected.relation is TaskRelation.NEW_TASK
    assert corrected.target_task_id == ""
    assert corrected.effective_text == "把这一段改得更正式"


def test_pending_skill_question_is_treated_as_answer_for_the_original_task(tmp_path: Path):
    repository = TaskRelationRepository(tmp_path / "relations.sqlite3")
    _create_completed_task(
        repository,
        task_id="task-a",
        title="普惠金融简报",
        body="普惠金融正文",
    )
    repository.set_pending_question(
        task_id="task-a",
        question="是否继续使用已经读取的素材？",
        resume_context={"skill_id": "writer1", "urls": ["https://example.com/a"]},
    )
    service = TaskRelationService(repository)

    decision = service.resolve_text(
        channel="wecom",
        user_id="user-001",
        text="继续使用已读取素材写",
        route_skill_id=None,
    )

    assert decision.relation is TaskRelation.ANSWER_CLARIFICATION
    assert decision.target_task_id == "task-a"
    assert decision.action is RelationAction.EXECUTE


def test_revision_waits_for_running_target_and_resumes_without_losing_request(tmp_path: Path):
    repository = TaskRelationRepository(tmp_path / "relations.sqlite3")
    _create_completed_task(
        repository,
        task_id="task-a",
        title="普惠金融简报",
        body="普惠金融正文",
    )
    repository.set_status("task-a", TaskCardStatus.RUNNING)
    service = TaskRelationService(repository)

    waiting = service.resolve_text(
        channel="wecom",
        user_id="user-001",
        text="把第二段再压缩一点",
        route_skill_id=None,
    )

    assert waiting.relation is TaskRelation.NEEDS_CLARIFICATION
    assert waiting.target_task_id == "task-a"
    assert "还在生成中" in waiting.question

    repository.set_status("task-a", TaskCardStatus.COMPLETED)
    resumed = service.resolve_text(
        channel="wecom",
        user_id="user-001",
        text="继续",
        route_skill_id=None,
    )
    assert resumed.relation is TaskRelation.ANSWER_CLARIFICATION
    assert resumed.target_task_id == "task-a"
    assert resumed.effective_text == "把第二段再压缩一点"


def test_low_confidence_semantic_result_still_requires_user_confirmation(tmp_path: Path):
    repository = TaskRelationRepository(tmp_path / "relations.sqlite3")
    _create_completed_task(
        repository,
        task_id="task-a",
        title="普惠金融简报",
        body="普惠金融正文",
    )
    _create_completed_task(
        repository,
        task_id="task-b",
        title="数字金融简报",
        body="数字金融正文",
    )

    def uncertain_classifier(**_kwargs):
        return {
            "relation": "continue",
            "target_task_id": "task-a",
            "material_role": "none",
            "suggested_skill_id": "writer1",
            "action": "execute",
            "confidence": 0.55,
            "reason": "两个任务都可能匹配",
        }

    service = TaskRelationService(repository, semantic_classifier=uncertain_classifier)
    decision = service.resolve_text(
        channel="wecom",
        user_id="user-001",
        text="把案例再收一收",
        route_skill_id=None,
    )

    assert decision.relation is TaskRelation.NEEDS_CLARIFICATION
    assert decision.action is RelationAction.ASK


def test_high_confidence_semantic_result_can_select_existing_task(tmp_path: Path):
    repository = TaskRelationRepository(tmp_path / "relations.sqlite3")
    _create_completed_task(
        repository,
        task_id="task-a",
        title="普惠金融简报",
        body="普惠金融正文",
    )
    _create_completed_task(
        repository,
        task_id="task-b",
        title="数字金融简报",
        body="数字金融正文",
    )

    service = TaskRelationService(
        repository,
        semantic_classifier=lambda **_kwargs: {
            "relation": "continue",
            "target_task_id": "task-b",
            "material_role": "none",
            "suggested_skill_id": "writer1",
            "action": "execute",
            "confidence": 0.91,
            "reason": "内容摘要与数字金融任务一致",
        },
    )
    decision = service.resolve_text(
        channel="wecom",
        user_id="user-001",
        text="这一部分只保留一个案例",
        route_skill_id=None,
    )

    assert decision.relation is TaskRelation.CONTINUE
    assert decision.target_task_id == "task-b"
    assert repository.relation_metrics(channel="wecom", user_id="user-001")[
        "semantic_decisions"
    ] == 1


def test_repository_reports_relation_decision_metrics_without_storing_user_text(tmp_path: Path):
    repository = TaskRelationRepository(tmp_path / "relations.sqlite3")
    _create_completed_task(
        repository,
        task_id="task-a",
        title="普惠金融简报",
        body="普惠金融正文",
    )
    _create_completed_task(
        repository,
        task_id="task-b",
        title="数字金融简报",
        body="数字金融正文",
    )
    service = TaskRelationService(repository)

    service.resolve_text(
        channel="wecom",
        user_id="user-001",
        text="第二段再短一点",
        route_skill_id=None,
    )
    service.resolve_text(
        channel="wecom",
        user_id="user-001",
        text="不是修改旧稿，是另写一份",
        route_skill_id="writer1",
    )

    metrics = repository.relation_metrics(channel="wecom", user_id="user-001")
    assert metrics == {
        "total": 2,
        "clarifications": 1,
        "pending_recoveries": 1,
        "user_corrections": 1,
        "semantic_decisions": 0,
    }
    with repository._connect() as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(relation_decisions)")}
    assert "user_text" not in columns
    assert "original_text" not in columns
