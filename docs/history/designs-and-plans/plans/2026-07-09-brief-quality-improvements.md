# Brief Quality Improvements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Improve `writer1` and `writer2` so single-source briefs read less like news copy, multi-source briefs avoid collage-style stitching, and revision drafts stay inside the same briefing style.

**Architecture:** Add a shared brief planning and quality layer that both `writer1` and `writer2` call before and after drafting. Use deterministic checks for stable, high-value format rules and a lightweight semantic critic for news-style drift, weak multi-source integration, and revision instability. Keep the public output schema unchanged.

**Tech Stack:** Python, existing `ToolGateway` workflows, `PydanticAIWriter`, pytest

---

### Task 1: Lock brief planning and quality behavior with tests

**Files:**
- Create: `tests/test_brief_quality.py`
- Modify: `tests/test_brief_writer_workflows.py`
- Modify: `tests/test_writer_prompt_rules.py`

- [ ] Add failing unit tests for shared brief planning, weak multi-source detection, and deterministic brief rules.
- [ ] Add failing workflow tests asserting `writer1` passes a `planning_note`, retries with `revision_feedback` after hard violations, and `writer2` refuses obviously weakly related source sets.
- [ ] Add failing prompt-rule tests asserting both brief skills mention planning, revision feedback, and anti-news-style constraints.

### Task 2: Implement the shared brief planning and quality helpers

**Files:**
- Create: `skills/brief_quality.py`
- Modify: `skills/writer1/schema.py`

- [ ] Add shared brief critic result models and deterministic violation models.
- [ ] Add `build_brief_plan(...)` so single-source and multi-source drafts both get hidden writing guidance.
- [ ] Add `assess_multi_source_relation(...)` and reject clearly weak combinations instead of forcing stitched output.
- [ ] Add deterministic checks for high-value brief rules such as bank naming, title connector, and forbidden list-style structure.

### Task 3: Wire writer1 and writer2 to the shared planning and validation flow

**Files:**
- Modify: `skills/writer1/workflow.py`
- Modify: `skills/writer2/workflow.py`

- [ ] Generate `planning_note` before every initial draft and revision draft.
- [ ] Run deterministic checks after drafting; if hard violations appear, send `revision_feedback` and rewrite once.
- [ ] Run semantic critic checks so `writer1` catches news-style drift and `writer2` catches theme drift / collage-style stitching.
- [ ] Keep source-reading, material supplementation, and task-level outputs compatible with the current platform flow.

### Task 4: Tighten prompt and skill rules for both brief skills

**Files:**
- Modify: `skills/writer1/SKILL.md`
- Modify: `skills/writer2/SKILL.md`
- Modify: `skills/writer1/prompts/draft.md`
- Add: `skills/writer1/prompts/critic.md`
- Modify: `skills/writer2/prompts/draft.md`
- Add: `skills/writer2/prompts/critic.md`

- [ ] Make single-source brief rules explicitly require “brief-style” rewriting rather than press-release paraphrasing.
- [ ] Make multi-source brief rules explicitly require one unified theme, clear relation handling, and balanced structure.
- [ ] Tell both draft prompts how to consume `planning_note` and `revision_feedback`.
- [ ] Tell both critic prompts how to flag news tone, stitched structure, and revision drift.

### Task 5: Verify focused regressions and update project docs

**Files:**
- Modify: `docs/development/TODO.md`
- Modify: `docs/capabilities/README.md`
- Test: `tests/test_brief_quality.py`
- Test: `tests/test_brief_writer_workflows.py`
- Test: `tests/test_writer_prompt_rules.py`
- Test: `tests/test_platform_pydantic_runtime.py`

- [ ] Run the new focused brief tests and confirm the red-green cycle on the new coverage.
- [ ] Run the existing brief and prompt regression tests to confirm no collateral breakage.
- [ ] Update capability / TODO docs so the repo reflects the new brief quality layer and remaining gaps accurately.
