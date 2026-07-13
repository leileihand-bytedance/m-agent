# Direct Report Quality Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a direct-report-only planning layer so the writer model selects a stronger mainline, prioritizes better data, and drafts closer to real reporting cases.

**Architecture:** Build a lightweight planner that converts source materials into a hidden writing note, inject that note into the writer payload, and update prompt assembly so the model reads the note before drafting. Keep the user-facing output schema unchanged.

**Tech Stack:** Python, existing `ToolGateway` workflows, `PydanticAIWriter`, pytest

---

### Task 1: Lock direct-report planning behavior with tests

**Files:**
- Modify: `tests/test_writing_planner.py`
- Modify: `tests/test_direct_report_workflow.py`
- Modify: `tests/test_platform_pydantic_runtime.py`
- Modify: `tests/test_platform_builtin_tools.py`

- [ ] Add failing tests for direct-report planning extraction.
- [ ] Add failing workflow test asserting `planning_note` is passed to `llm_writer`.
- [ ] Add failing prompt-builder tests asserting `## 写作规划` appears before materials.

### Task 2: Implement the direct-report planner

**Files:**
- Create: `skills/writing_planner.py`

- [ ] Add theme detection and sentence selection helpers.
- [ ] Add `build_direct_report_plan(...)`.
- [ ] Prefer data-bearing factual sentences over generic time/background sentences.

### Task 3: Inject the planning note into the direct-report flow

**Files:**
- Modify: `skills/direct_report/workflow.py`

- [ ] Generate `planning_note` before calling `llm_writer`.
- [ ] Keep revision flow unchanged for this phase.

### Task 4: Update prompt assembly and direct-report prompt rules

**Files:**
- Modify: `app/platform/pydantic_runtime.py`
- Modify: `app/platform/builtin_tools.py`
- Modify: `skills/direct_report/prompts/draft.md`
- Modify: `skills/direct_report/SKILL.md`

- [ ] Insert a `## 写作规划` prompt block when `planning_note` exists.
- [ ] Tell the model to follow the plan without copying it into the draft.

### Task 5: Verify direct-report regression coverage

**Files:**
- Test: `tests/test_writing_planner.py`
- Test: `tests/test_direct_report_workflow.py`
- Test: `tests/test_platform_pydantic_runtime.py`
- Test: `tests/test_platform_builtin_tools.py`

- [ ] Run focused direct-report and prompt tests.
- [ ] Run related regression tests to ensure no collateral breakage.
