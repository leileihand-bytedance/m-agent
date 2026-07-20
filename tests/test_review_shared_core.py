from __future__ import annotations

import asyncio

import pytest

from app.review.core.dedupe import dedupe_prefer_longer_description
from app.review.core.evidence import (
    build_paragraph_evidence,
    paragraph_finding_to_issue,
)
from app.review.core.metrics import ReviewRunMetrics
from app.review.core.model_output import (
    collect_message_text,
    looks_like_valid_issue_json,
    parse_paragraph_findings,
)
from app.review.core.model_runtime import create_model_message, run_with_retries
from app.review.core.models import Finding, ReviewResult
from app.review.rules.catalog import RULE_CATALOG, RuleScope
from app.review.rules.profiles import (
    GENERAL_DOCX_PROFILE,
    GENERAL_HTML_PROFILE,
    GENERAL_TEXT_PROFILE,
    HALFMONTHLY_PROFILE,
    NEICAN_PROFILE,
    REVIEW_PROFILES,
)
from app.platform.model_reliability import ModelCallError


class _TextBlock:
    def __init__(self, text: str):
        self.text = text


class _Message:
    def __init__(self, *parts: str):
        self.content = [_TextBlock(part) for part in parts]


class _Messages:
    def __init__(self, *, error: Exception | None = None):
        self.error = error
        self.calls = 0

    def create(self, **kwargs: object) -> _Message:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return _Message(str(kwargs["model"]))


class _Client:
    def __init__(self, *, error: Exception | None = None):
        self.messages = _Messages(error=error)


class _JsonMessages:
    def __init__(self):
        self.calls = 0

    def create(self, **kwargs: object) -> _Message:
        self.calls += 1
        return _Message('{"issues": []}')


class _JsonClient:
    def __init__(self):
        self.messages = _JsonMessages()


class _PayloadMessages:
    def __init__(self, counter: dict[str, int], payload: str):
        self._counter = counter
        self._payload = payload

    def create(self, **kwargs: object) -> _Message:
        self._counter["calls"] += 1
        return _Message(self._payload)


class _PayloadClient:
    def __init__(self, counter: dict[str, int], payload: str):
        self.messages = _PayloadMessages(counter, payload)


def test_shared_models_keep_legacy_paragraph_contract() -> None:
    finding = Finding(
        rule_id="general-typo",
        paragraph_index=2,
        line_number=3,
        original_text="本周布署工作。",
        description="“布署”应为“部署”",
        target_text="布署",
    )
    result = ReviewResult(
        findings=[finding],
        total_rules=1,
        passed_rules=0,
        filename="材料.docx",
    )

    from app.review.reviewer import Finding as LegacyFinding
    from app.review.reviewer import ReviewResult as LegacyReviewResult

    assert LegacyFinding is Finding
    assert LegacyReviewResult is ReviewResult
    assert result.findings == [finding]


def test_paragraph_evidence_requires_exact_source_and_uses_canonical_context() -> None:
    paragraphs = ["标题", "系统支持7×24小时服务。"]

    evidence = build_paragraph_evidence(
        paragraphs,
        paragraph_index=1,
        target_text="7×24小时",
        source_kind="docx",
    )

    assert evidence is not None
    assert evidence.location.unit_kind == "paragraph"
    assert evidence.location.unit_id == "1"
    assert evidence.exact_text == "7×24小时"
    assert evidence.context == paragraphs[1]
    punctuation_evidence = build_paragraph_evidence(
        ["事项， 后续安排。"],
        paragraph_index=0,
        target_text="， ",
        source_kind="docx",
    )
    assert punctuation_evidence is not None
    assert punctuation_evidence.exact_text == "， "
    assert build_paragraph_evidence(
        paragraphs,
        paragraph_index=1,
        target_text="7×7小时",
        source_kind="docx",
    ) is None


def test_paragraph_finding_converts_to_format_neutral_issue() -> None:
    finding = Finding(
        rule_id="general-grammar",
        paragraph_index=0,
        line_number=1,
        original_text="模型返回的非权威原文",
        description="句子缺少主语",
        target_text="通过培训，使大家掌握技能",
    )
    paragraphs = ["通过培训，使大家掌握技能。"]

    issue = paragraph_finding_to_issue(
        finding,
        paragraphs,
        source_kind="text",
    )

    assert issue is not None
    assert issue.rule_id == "general-grammar"
    assert issue.primary_evidence.context == paragraphs[0]
    assert issue.primary_evidence.exact_text == finding.target_text


def test_shared_model_output_parser_keeps_only_allowed_and_bounded_issues() -> None:
    output = """```json
    {"reasoning": "检查完成", "issues": [
      {"paragraph_index": 0, "rule_id": "general-typo", "target_text": "布署", "original_text": "模型原文", "description": "应为部署"},
      {"paragraph_index": 8, "rule_id": "general-typo", "target_text": "越界", "description": "越界"},
      {"paragraph_index": 0, "rule_id": "other-rule", "target_text": "标题", "description": "越权规则"}
    ]}
    ```"""

    findings, reasoning = parse_paragraph_findings(
        output,
        ["本周布署工作。"],
        allowed_rules=("general-typo",),
    )

    assert reasoning == "检查完成"
    assert len(findings) == 1
    assert findings[0].rule_id == "general-typo"
    assert findings[0].original_text == "模型原文"
    assert looks_like_valid_issue_json(output)
    assert collect_message_text(_Message("第一段", "第二段")) == "第一段\n第二段"


def test_shared_dedupe_preserves_first_position_and_prefers_longer_description() -> None:
    short = Finding("general-typo", 0, 1, "布署", "错字", "布署")
    long = Finding("general-typo", 0, 1, "布署", "“布署”应为“部署”", "布署")
    other = Finding("general-grammar", 1, 2, "通过培训，使", "缺主语", "通过培训，使")

    findings = dedupe_prefer_longer_description(
        [short, other, long],
        key=lambda item: (item.rule_id, item.paragraph_index, item.target_text),
    )

    assert findings == [long, other]


def test_shared_model_runtime_records_stage_calls_and_failures() -> None:
    metrics = ReviewRunMetrics()
    client = _Client()

    message = create_model_message(
        client,
        metrics=metrics,
        stage="local_scan",
        model="fake-model",
    )

    assert collect_message_text(message) == "fake-model"
    assert metrics.model_calls == 1
    assert metrics.model_calls_by_stage == {"local_scan": 1}
    assert metrics.model_failures == 0

    failed_client = _Client(error=RuntimeError("network"))
    with pytest.raises(ModelCallError) as captured:
        create_model_message(
            failed_client,
            metrics=metrics,
            stage="whole_document_logic",
            model="fake-model",
        )

    assert captured.value.safe_error_code == "model_call_failed"
    assert "network" not in str(captured.value)

    assert metrics.model_calls == 2
    assert metrics.model_failures == 1
    assert metrics.model_failures_by_stage == {"whole_document_logic": 1}


def test_shared_retry_stops_after_first_success_without_extra_call() -> None:
    attempts: list[int] = []

    async def operation(attempt: int) -> tuple[list[str], str | None]:
        attempts.append(attempt)
        if attempt == 0:
            return [], "invalid JSON"
        return ["ok"], None

    outcome = asyncio.run(run_with_retries(operation, max_attempts=3))

    assert outcome.succeeded
    assert outcome.value == ["ok"]
    assert outcome.errors == ("invalid JSON",)
    assert outcome.attempts == 2
    assert attempts == [0, 1]


def test_shared_retry_stops_on_unknown_exception() -> None:
    attempts: list[int] = []

    async def operation(attempt: int) -> tuple[list[str], str | None]:
        attempts.append(attempt)
        raise RuntimeError("unexpected implementation detail")

    outcome = asyncio.run(run_with_retries(operation, max_attempts=3))

    assert not outcome.succeeded
    assert outcome.errors == ("model_call_failed",)
    assert outcome.attempts == 1
    assert attempts == [0]


def test_general_text_profile_uses_shared_runtime_without_extra_model_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.review.general_reviewer import review_general

    client = _JsonClient()
    metrics = ReviewRunMetrics()
    monkeypatch.setattr(
        "app.review.general_reviewer.build_anthropic_client",
        lambda: (client, "fake-model"),
    )

    result = asyncio.run(
        review_general(
            ["本周工作正常推进。"],
            "",
            "文字消息",
            metrics=metrics,
            profile=GENERAL_TEXT_PROFILE,
        )
    )

    assert result.total_rules == len(GENERAL_TEXT_PROFILE.rule_ids)
    assert client.messages.calls == 1
    assert metrics.model_calls == 1
    assert metrics.model_calls_by_stage == {"local_scan": 1}


def test_rule_catalog_and_profiles_are_static_and_isolate_specialized_rules() -> None:
    for profile in REVIEW_PROFILES.values():
        assert profile.rule_ids
        assert set(profile.rule_ids) <= set(RULE_CATALOG)

    assert GENERAL_TEXT_PROFILE.rule_ids == GENERAL_DOCX_PROFILE.rule_ids
    assert GENERAL_HTML_PROFILE.rule_ids == GENERAL_DOCX_PROFILE.rule_ids
    assert "halfmonthly-leader-title" not in GENERAL_DOCX_PROFILE.rule_ids
    assert "toc-mismatch" not in GENERAL_DOCX_PROFILE.rule_ids
    assert RULE_CATALOG["quote-pair"].scope == RuleScope.COMMON
    assert RULE_CATALOG["general-incomplete"].scope == RuleScope.CONDITIONAL
    assert RULE_CATALOG["halfmonthly-leader-title"].scope == RuleScope.SPECIALIZED


def test_neican_phases_use_shared_runtime_metrics_without_extra_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.review.reviewer import review_phase1, review_phase2

    output = """{
      "issues": [
        {
          "paragraph_index": 0,
          "rule_id": "title-truncated",
          "target_text": "模型虚构片段",
          "original_text": "模型虚构原文",
          "description": "“测试标题”语义不完整"
        }
      ]
    }"""
    counter = {"calls": 0}
    client = _PayloadClient(counter, output)
    metrics = ReviewRunMetrics()
    monkeypatch.setattr(
        "app.review.reviewer._get_anthropic_client",
        lambda: (client, "fake-model"),
    )

    async def run_phases() -> tuple[ReviewResult, ReviewResult]:
        phase1 = await review_phase1(
            ["测试标题", "测试正文。"],
            "",
            "内参.docx",
            metrics=metrics,
            profile=NEICAN_PROFILE,
        )
        phase2 = await review_phase2(
            ["测试标题", "测试正文。"],
            "",
            "内参.docx",
            metrics=metrics,
            profile=NEICAN_PROFILE,
        )
        return phase1, phase2

    phase1, phase2 = asyncio.run(run_phases())

    phase1_finding = next(
        finding
        for finding in phase1.findings
        if finding.rule_id == "title-truncated"
    )
    assert counter["calls"] == 2
    assert metrics.model_calls == 2
    assert metrics.model_calls_by_stage == {
        "neican_phase1": 1,
        "neican_phase2": 1,
    }
    assert phase1.total_rules + phase2.total_rules == len(NEICAN_PROFILE.rule_ids)
    assert phase1_finding.original_text == "测试标题"
    assert phase1_finding.target_text == "测试标题"


def test_halfmonthly_uses_shared_profile_metrics_and_exact_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.review.halfmonthly_reviewer import review_halfmonthly

    output = """{
      "issues": [
        {
          "paragraph_index": 4,
          "rule_id": "halfmonthly-section-mismatch",
          "target_text": "模型虚构片段",
          "original_text": "模型虚构原文",
          "description": "“项目”内容应归入工作动态及成果"
        }
      ]
    }"""
    counter = {"calls": 0}
    metrics = ReviewRunMetrics()
    monkeypatch.setattr(
        "app.review.halfmonthly_reviewer.build_anthropic_client",
        lambda: (_PayloadClient(counter, output), "fake-model"),
    )
    paragraphs = [
        "内部资料",
        "微众银行信息动态半月报",
        "（2026年4月1日-4月15日）",
        "业务动态及成果",
        "4月3日，我行完成项目。",
    ]

    result = asyncio.run(
        review_halfmonthly(
            paragraphs,
            "",
            "半月报.docx",
            metrics=metrics,
            profile=HALFMONTHLY_PROFILE,
        )
    )

    model_finding = next(
        finding
        for finding in result.findings
        if finding.rule_id == "halfmonthly-section-mismatch"
    )
    assert counter["calls"] == 1
    assert metrics.model_calls == 1
    assert metrics.model_calls_by_stage == {"halfmonthly_semantic": 1}
    assert result.total_rules == len(HALFMONTHLY_PROFILE.rule_ids)
    assert model_finding.original_text == paragraphs[4]
    assert model_finding.target_text == "项目"
    assert model_finding.target_text in model_finding.original_text
