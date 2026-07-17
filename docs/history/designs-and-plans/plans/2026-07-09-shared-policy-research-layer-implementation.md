# Shared Policy Research Layer Implementation Plan

> 状态：已实施。当前行为以 `app/policy_research/`、相关 skill 和自动化测试为准。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 `direct_report`、`writer1`、`writer2` 建立共享政策研究层，并对本地政策库做最小结构化升级，使政策挂靠判断从“分散在 skill 中”变成“统一可复用能力”。

**Architecture:** 保留 `app/policy_knowledge/` 作为抓取和检索底座；新增 `app/policy_research/` 负责“能不能挂、挂哪条、为什么、摘哪一句、备选”；平台通过 `policy_research` 工具暴露该能力，skills 按各自 profile 调用。政策库只做小幅结构升级，增加标签和禁用状态，不在本阶段引入新检索基础设施。

**Tech Stack:** Python, pytest, SQLite, Pydantic, ToolGateway, existing platform runtime

---

### Task 1: 为共享政策研究层写失败测试

**Files:**
- Create: `tests/test_policy_research_service.py`
- Create: `tests/test_policy_research_profiles.py`
- Modify: `tests/test_platform_builtin_tools.py`

- [ ] **Step 1: Write the failing test**

在 `tests/test_policy_research_service.py` 中增加基础行为测试：

```python
from app.policy_research.service import research_policy_attachment


def test_research_policy_attachment_returns_primary_and_alternatives(tmp_path):
    result = research_policy_attachment(
        user_instruction="请根据这条素材写简报",
        materials=[
            {
                "title": "微众银行推出微贸贷",
                "text": "微众银行围绕外贸小微企业推出微贸贷，支持稳订单拓市场。",
                "source": "user_text",
            }
        ],
        db_path=tmp_path / "policies.sqlite3",
        usage_profile="brief",
        limit=3,
    )

    assert result.should_attach_policy is True
    assert result.primary_policy is not None
    assert result.primary_policy.title
```

在 `tests/test_policy_research_profiles.py` 中增加差异化行为测试：

```python
from app.policy_research.service import research_policy_attachment


def test_direct_report_profile_rejects_activity_material(tmp_path):
    result = research_policy_attachment(
        user_instruction="请写直报",
        materials=[
            {
                "title": "微众银行开展金融知识直播活动",
                "text": "围绕反诈和消保开展直播宣教活动。",
                "source": "user_text",
            }
        ],
        db_path=tmp_path / "policies.sqlite3",
        usage_profile="direct_report",
        limit=3,
    )

    assert result.should_attach_policy is False
    assert result.decision_reason == "unsupported_material_type"
```

在 `tests/test_platform_builtin_tools.py` 中增加工具注册测试：

```python
assert "policy_research" in tools
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_policy_research_service.py tests/test_policy_research_profiles.py tests/test_platform_builtin_tools.py -v`

Expected: FAIL，报错集中在 `app.policy_research` 模块不存在、`policy_research` 工具未注册。

- [ ] **Step 3: Commit**

```bash
git add tests/test_policy_research_service.py tests/test_policy_research_profiles.py tests/test_platform_builtin_tools.py
git commit -m "test: define shared policy research layer behavior"
```

### Task 2: 建立共享层输入输出模型和 profile 规则

**Files:**
- Create: `app/policy_research/__init__.py`
- Create: `app/policy_research/models.py`
- Create: `app/policy_research/profiles.py`
- Test: `tests/test_policy_research_service.py`
- Test: `tests/test_policy_research_profiles.py`

- [ ] **Step 1: Write the failing test**

补充模型和 profile 行为测试：

```python
from app.policy_research.models import PolicyResearchResult, PolicyCandidate
from app.policy_research.profiles import get_policy_research_profile


def test_get_policy_research_profile_returns_known_profile():
    profile = get_policy_research_profile("brief")
    assert profile.id == "brief"
    assert profile.max_alternatives == 2


def test_policy_research_result_defaults_are_stable():
    result = PolicyResearchResult(
        should_attach_policy=False,
        decision_reason="unsupported_theme",
        matched_themes=[],
        retrieval_query="",
        confidence=0.0,
    )
    assert result.primary_policy is None
    assert result.alternative_policies == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_policy_research_service.py tests/test_policy_research_profiles.py -v`

Expected: FAIL，提示缺少 `models.py`、`profiles.py` 或字段定义不完整。

- [ ] **Step 3: Write minimal implementation**

在 `app/policy_research/models.py` 中定义：

```python
from pydantic import BaseModel, Field


class PolicyCandidate(BaseModel):
    title: str
    source: str
    category: str
    publish_date: str
    url: str
    snippet: str
    matched_terms: list[str] = Field(default_factory=list)
    relevance_score: int = 0
    selection_reason: str = ""


class PolicyResearchResult(BaseModel):
    should_attach_policy: bool
    decision_reason: str
    matched_themes: list[str] = Field(default_factory=list)
    retrieval_query: str = ""
    confidence: float = 0.0
    primary_policy: PolicyCandidate | None = None
    alternative_policies: list[PolicyCandidate] = Field(default_factory=list)
```

在 `app/policy_research/profiles.py` 中定义：

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class PolicyResearchProfile:
    id: str
    max_primary: int
    max_alternatives: int
    reject_material_types: tuple[str, ...]
    prefer_policy_original: bool = True


PROFILES = {
    "direct_report": PolicyResearchProfile(
        id="direct_report",
        max_primary=1,
        max_alternatives=1,
        reject_material_types=("event_activity", "award_or_recognition", "lawsuit_or_case"),
    ),
    "brief": PolicyResearchProfile(
        id="brief",
        max_primary=1,
        max_alternatives=2,
        reject_material_types=(),
    ),
}


def get_policy_research_profile(profile_id: str) -> PolicyResearchProfile:
    return PROFILES.get(profile_id, PROFILES["brief"])
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_policy_research_service.py tests/test_policy_research_profiles.py -v`

Expected: 部分测试仍失败，但模型和 profile 相关断言转为 PASS。

- [ ] **Step 5: Commit**

```bash
git add app/policy_research/__init__.py app/policy_research/models.py app/policy_research/profiles.py tests/test_policy_research_service.py tests/test_policy_research_profiles.py
git commit -m "feat: add shared policy research models and profiles"
```

### Task 3: 实现共享政策研究服务

**Files:**
- Create: `app/policy_research/service.py`
- Modify: `app/policy_knowledge/materials.py`
- Test: `tests/test_policy_research_service.py`
- Test: `tests/test_policy_research_profiles.py`

- [ ] **Step 1: Write the failing test**

补充服务级测试：

```python
from app.policy_knowledge.store import PolicyKnowledgeStore
from app.policy_research.service import research_policy_attachment


def test_research_policy_attachment_skips_disabled_policy(tmp_path):
    store = PolicyKnowledgeStore(tmp_path / "policies.sqlite3")
    store.upsert_documents(
        [
            {
                "source": "nfra",
                "category": "policy_original",
                "item_id": "",
                "doc_id": "1",
                "title": "关于提升小微企业金融服务质效的通知",
                "publish_date": "2026-07-01",
                "url": "https://www.nfra.gov.cn/p1",
                "text": "提升小微企业金融服务质效。",
                "original_links": [],
                "metadata": {},
                "is_enabled": 0,
            }
        ]
    )

    result = research_policy_attachment(
        user_instruction="请写直报",
        materials=[{"title": "微众银行推出微业贷", "text": "支持小微企业融资。", "source": "user_text"}],
        db_path=tmp_path / "policies.sqlite3",
        usage_profile="direct_report",
    )

    assert result.should_attach_policy is False
    assert result.decision_reason in {"no_qualified_policy", "policy_db_unavailable"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_policy_research_service.py tests/test_policy_research_profiles.py -v`

Expected: FAIL，失败点集中在 `research_policy_attachment` 未实现或未过滤禁用记录。

- [ ] **Step 3: Write minimal implementation**

在 `app/policy_research/service.py` 中实现主流程：

```python
from pathlib import Path

from app.policy_knowledge.materials import infer_policy_intent, rank_policy_candidates
from app.policy_knowledge.store import PolicyKnowledgeStore
from app.policy_research.models import PolicyCandidate, PolicyResearchResult
from app.policy_research.profiles import get_policy_research_profile


def research_policy_attachment(
    *,
    user_instruction: str,
    materials: list[dict[str, object]],
    db_path: str | Path,
    usage_profile: str,
    limit: int = 3,
) -> PolicyResearchResult:
    profile = get_policy_research_profile(usage_profile)
    material_type = detect_material_type(materials)
    if material_type in profile.reject_material_types:
        return PolicyResearchResult(
            should_attach_policy=False,
            decision_reason="unsupported_material_type",
            matched_themes=[],
            retrieval_query="",
            confidence=0.9,
        )

    source_text = "\n".join(
        [user_instruction] + [str(item.get("title", "")) + "\n" + str(item.get("text", "")) for item in materials]
    )
    intent = infer_policy_intent(source_text)
    if not intent["needs_policy"]:
        return PolicyResearchResult(
            should_attach_policy=False,
            decision_reason="unsupported_theme",
            matched_themes=[],
            retrieval_query="",
            confidence=0.2,
        )

    store = PolicyKnowledgeStore(db_path)
    candidates = store.search(str(intent["query"]), limit=max(limit * 4, 8), category="policy_original")
    ranked = rank_policy_candidates(
        candidates=candidates,
        themes=list(intent["themes"]),
        keywords=list(intent["keywords"]),
        min_relevance=25,
    )
    if not ranked:
        return PolicyResearchResult(
            should_attach_policy=False,
            decision_reason="no_qualified_policy",
            matched_themes=list(intent["theme_labels"]),
            retrieval_query=str(intent["query"]),
            confidence=0.4,
        )

    primary = _to_candidate(ranked[0])
    alternatives = [_to_candidate(item) for item in ranked[1 : 1 + profile.max_alternatives]]
    return PolicyResearchResult(
        should_attach_policy=True,
        decision_reason="qualified_local_policy",
        matched_themes=list(intent["theme_labels"]),
        retrieval_query=str(intent["query"]),
        confidence=0.8,
        primary_policy=primary,
        alternative_policies=alternatives,
    )
```

并在同文件中添加最小的素材类型识别：

```python
def detect_material_type(materials: list[dict[str, object]]) -> str:
    text = "\n".join(f"{item.get('title', '')}\n{item.get('text', '')}" for item in materials)
    if any(term in text for term in ("直播", "宣传", "活动")):
        return "event_activity"
    if any(term in text for term in ("获奖", "荣获", "入选", "评选")):
        return "award_or_recognition"
    if any(term in text for term in ("判决", "法院", "侵权", "案件")):
        return "lawsuit_or_case"
    if any(term in text for term in ("推出", "上线", "产品", "授信", "融资服务")):
        return "product_or_service"
    if any(term in text for term in ("模式", "机制", "平台", "体系", "探索")):
        return "mechanism_or_platform"
    return "comprehensive_progress"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_policy_research_service.py tests/test_policy_research_profiles.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/policy_research/service.py app/policy_knowledge/materials.py tests/test_policy_research_service.py tests/test_policy_research_profiles.py
git commit -m "feat: implement shared policy research service"
```

### Task 4: 升级政策知识库 schema 和检索过滤

**Files:**
- Modify: `app/policy_knowledge/store.py`
- Modify: `app/policy_knowledge/cli.py`
- Test: `tests/test_policy_knowledge_store.py`
- Test: `tests/test_policy_knowledge_materials.py`

- [ ] **Step 1: Write the failing test**

在 `tests/test_policy_knowledge_store.py` 中新增字段和禁用过滤测试：

```python
def test_policy_knowledge_store_ignores_disabled_documents(tmp_path):
    store = PolicyKnowledgeStore(tmp_path / "policies.sqlite3")
    store.upsert_documents(
        [
            {
                "source": "govcn",
                "category": "policy_original",
                "item_id": "",
                "doc_id": "6974607",
                "title": "国务院办公厅关于促进消费品以旧换新的通知",
                "publish_date": "2024-09-01",
                "url": "https://www.gov.cn/policy",
                "text": "促进消费持续恢复。",
                "original_links": [],
                "metadata": {},
                "is_enabled": 0,
            }
        ]
    )

    assert store.search("促进消费", limit=3) == []
```

在 `tests/test_policy_knowledge_materials.py` 中补标签字段断言：

```python
assert "theme_tags" in result
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_policy_knowledge_store.py tests/test_policy_knowledge_materials.py -v`

Expected: FAIL，提示 schema 未包含新字段或 `search()` 未过滤禁用记录。

- [ ] **Step 3: Write minimal implementation**

在 `app/policy_knowledge/store.py` 中扩展 schema：

```sql
alter table policy_documents add column theme_tags_json text not null default '[]';
alter table policy_documents add column region_tags_json text not null default '[]';
alter table policy_documents add column audience_tags_json text not null default '[]';
alter table policy_documents add column source_weight integer not null default 0;
alter table policy_documents add column is_enabled integer not null default 1;
alter table policy_documents add column disabled_reason text not null default '';
alter table policy_documents add column review_note text not null default '';
```

并在查询中过滤：

```python
sql += " where is_enabled = 1"
if category:
    sql += " and category = ?"
```

在 `_row_to_dict()` 中补回字段：

```python
"theme_tags": json.loads(row["theme_tags_json"] or "[]"),
"region_tags": json.loads(row["region_tags_json"] or "[]"),
"audience_tags": json.loads(row["audience_tags_json"] or "[]"),
"source_weight": int(row["source_weight"] or 0),
"is_enabled": bool(row["is_enabled"]),
```

在 `app/policy_knowledge/cli.py` 中补一个最小统计命令：

```python
stats = subparsers.add_parser("stats", help="查看政策库基本统计")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_policy_knowledge_store.py tests/test_policy_knowledge_materials.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/policy_knowledge/store.py app/policy_knowledge/cli.py tests/test_policy_knowledge_store.py tests/test_policy_knowledge_materials.py
git commit -m "feat: add policy knowledge tags and disable state"
```

### Task 5: 把共享研究层暴露为平台工具

**Files:**
- Modify: `app/platform/builtin_tools.py`
- Modify: `app/platform/app.py`
- Test: `tests/test_platform_builtin_tools.py`
- Test: `tests/test_platform_app.py`

- [ ] **Step 1: Write the failing test**

补工具调用测试：

```python
def test_policy_research_tool_returns_structured_result(tmp_path):
    result = policy_research(
        user_instruction="请写简报",
        materials=[{"title": "微众银行推出微贸贷", "text": "支持外贸小微企业。", "source": "user_text"}],
        db_path=tmp_path / "policies.sqlite3",
        usage_profile="brief",
        limit=3,
    )

    assert "should_attach_policy" in result
    assert "decision_reason" in result
```

在 `tests/test_platform_app.py` 中增加工具注册断言：

```python
assert "policy_research" in tools
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_platform_builtin_tools.py tests/test_platform_app.py -v`

Expected: FAIL，提示 `policy_research` 函数不存在或未注册。

- [ ] **Step 3: Write minimal implementation**

在 `app/platform/builtin_tools.py` 中新增：

```python
def policy_research(
    *,
    user_instruction: str,
    materials: list[object],
    db_path: str | Path,
    usage_profile: str,
    limit: int = 3,
) -> dict[str, object]:
    from app.policy_research.service import research_policy_attachment

    result = research_policy_attachment(
        user_instruction=user_instruction,
        materials=[item for item in materials if isinstance(item, dict)],
        db_path=db_path,
        usage_profile=usage_profile,
        limit=limit,
    )
    return result.model_dump()
```

在 `app/platform/app.py` 的 `build_platform_tools()` 中注册：

```python
"policy_research": lambda user_instruction, materials, usage_profile, limit=3: policy_research(
    user_instruction=user_instruction,
    materials=materials,
    db_path=config.policy_db_path,
    usage_profile=usage_profile,
    limit=limit,
),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_platform_builtin_tools.py tests/test_platform_app.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/platform/builtin_tools.py app/platform/app.py tests/test_platform_builtin_tools.py tests/test_platform_app.py
git commit -m "feat: expose shared policy research as platform tool"
```

### Task 6: 迁移 `direct_report` 到共享研究层

**Files:**
- Modify: `skills/direct_report/config.yaml`
- Modify: `skills/direct_report/workflow.py`
- Modify: `skills/direct_report/policy_research.py`
- Modify: `skills/writing_planner.py`
- Test: `tests/test_direct_report_workflow.py`
- Test: `tests/test_direct_report_policy_research.py`
- Test: `tests/test_direct_report_policy_gate.py`

- [ ] **Step 1: Write the failing test**

把 `tests/test_direct_report_workflow.py` 的调用预期从 `policy_search` 调整为 `policy_research`：

```python
assert calls[0][0] == "policy_research"
assert seen_payloads[0]["materials"][1]["source"] == "policy_knowledge"
```

补一条拒挂测试：

```python
assert result.output["sources"] == ["https://example.com/news"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_direct_report_workflow.py tests/test_direct_report_policy_research.py tests/test_direct_report_policy_gate.py -v`

Expected: FAIL，提示 skill 仍走旧路径。

- [ ] **Step 3: Write minimal implementation**

在 `skills/direct_report/config.yaml` 中增加工具授权：

```yaml
allowed_tools:
  - web_reader
  - policy_research
  - policy_search
  - search
  - word_reader
  - pdf_reader
  - llm_writer
```

在 `skills/direct_report/workflow.py` 中改为：

```python
research_payload = tools.call(
    "policy_research",
    user_instruction=str(inputs.get("text", "") or ""),
    materials=list(source_materials),
    usage_profile="direct_report",
    limit=2,
)

if research_payload.get("should_attach_policy") and research_payload.get("primary_policy"):
    materials.append(
        {
            "title": research_payload["primary_policy"]["title"],
            "text": (
                f"相关性说明：{research_payload['primary_policy']['selection_reason']}\n"
                f"政策摘录：{research_payload['primary_policy']['snippet']}"
            ),
            "url": research_payload["primary_policy"]["url"],
            "source": "policy_knowledge",
            "category": research_payload["primary_policy"]["category"],
            "publish_date": research_payload["primary_policy"]["publish_date"],
        }
    )
```

在 `skills/writing_planner.py` 中只读取“是否挂、主推荐标题、拒绝原因”，不读取共享层写作指导。

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_direct_report_workflow.py tests/test_direct_report_policy_research.py tests/test_direct_report_policy_gate.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add skills/direct_report/config.yaml skills/direct_report/workflow.py skills/direct_report/policy_research.py skills/writing_planner.py tests/test_direct_report_workflow.py tests/test_direct_report_policy_research.py tests/test_direct_report_policy_gate.py
git commit -m "feat: migrate direct report to shared policy research"
```

### Task 7: 迁移 `writer1` / `writer2` 到共享研究层

**Files:**
- Modify: `skills/writer1/config.yaml`
- Modify: `skills/writer2/config.yaml`
- Modify: `skills/writer1/workflow.py`
- Modify: `skills/writer2/workflow.py`
- Test: `tests/test_brief_writer_workflows.py`

- [ ] **Step 1: Write the failing test**

把 `tests/test_brief_writer_workflows.py` 增加“先研究、后注入”的调用顺序断言：

```python
assert [call[0] for call in calls] == ["policy_research", "policy_materials"]
```

补“研究层拒挂时仍可继续写作”测试：

```python
assert result.needs_clarification is False
assert result.title == "简报标题"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_brief_writer_workflows.py -v`

Expected: FAIL，提示 workflow 仍直接走 `policy_materials`。

- [ ] **Step 3: Write minimal implementation**

在 `skills/writer1/config.yaml` 和 `skills/writer2/config.yaml` 中增加授权：

```yaml
allowed_tools:
  - web_reader
  - bank_materials
  - bank_search
  - policy_research
  - policy_materials
  - policy_search
  - word_reader
  - pdf_reader
  - llm_writer
```

在 `skills/writer1/workflow.py` 中改为：

```python
research = tools.call(
    "policy_research",
    user_instruction=str(inputs.get("text", "")),
    materials=list(source_materials),
    usage_profile="brief",
    limit=3,
)
if research.get("should_attach_policy") and research.get("primary_policy"):
    materials.append(
        {
            "title": research["primary_policy"]["title"],
            "text": (
                f"相关性说明：{research['primary_policy']['selection_reason']}\n"
                f"政策摘录：{research['primary_policy']['snippet']}"
            ),
            "url": research["primary_policy"]["url"],
            "source": "policy_knowledge",
            "category": research["primary_policy"]["category"],
            "publish_date": research["primary_policy"]["publish_date"],
        }
    )
```

`skills/writer2/workflow.py` 采用同样模式，但允许把 1 条备选也追加进 `materials`。

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_brief_writer_workflows.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add skills/writer1/config.yaml skills/writer2/config.yaml skills/writer1/workflow.py skills/writer2/workflow.py tests/test_brief_writer_workflows.py
git commit -m "feat: migrate brief skills to shared policy research"
```

### Task 8: 更新文档并完成回归

**Files:**
- Modify: `docs/knowledge/policy.md`
- Modify: `docs/development/architecture.md`
- Modify: `docs/agent-platform/README.md`
- Modify: `docs/capabilities/README.md`
- Modify: `docs/development/README.md`
- Modify: `docs/development/TODO.md`
- Test: `tests/test_policy_research_service.py`
- Test: `tests/test_policy_research_profiles.py`
- Test: `tests/test_policy_knowledge_store.py`
- Test: `tests/test_policy_knowledge_materials.py`
- Test: `tests/test_platform_builtin_tools.py`
- Test: `tests/test_platform_app.py`
- Test: `tests/test_direct_report_workflow.py`
- Test: `tests/test_brief_writer_workflows.py`

- [ ] **Step 1: Update documentation**

把以下事实写入文档：

```text
1. 共享政策研究层位于 app/policy_research/
2. 第一阶段只服务 direct_report / writer1 / writer2
3. 共享层只返回能否挂靠、主推荐、备选和依据，不输出写作指导
4. 政策库新增标签字段和禁用状态字段
```

- [ ] **Step 2: Run the focused regression suite**

Run: `pytest tests/test_policy_research_service.py tests/test_policy_research_profiles.py tests/test_policy_knowledge_store.py tests/test_policy_knowledge_materials.py tests/test_platform_builtin_tools.py tests/test_platform_app.py tests/test_direct_report_workflow.py tests/test_brief_writer_workflows.py -v`

Expected: PASS

- [ ] **Step 3: Run the broader platform regression**

Run: `pytest tests/test_platform_registry.py tests/test_platform_router.py tests/test_platform_tools.py tests/test_platform_builtin_tools.py tests/test_platform_file_readers.py tests/test_platform_pydantic_runtime.py tests/test_direct_report_workflow.py tests/test_platform_runtime.py tests/test_platform_demo.py tests/test_platform_wecom_gateway.py tests/test_platform_storage.py tests/test_platform_identity.py tests/test_platform_app.py tests/test_platform_cli.py -v`

Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add docs/knowledge/policy.md docs/development/architecture.md docs/agent-platform/README.md docs/capabilities/README.md docs/development/README.md docs/development/TODO.md
git commit -m "docs: document shared policy research layer"
```
