# Direct Report Quality Phase 1 Design

**Objective**

Improve `skills/direct_report` writing quality using real case patterns from the desktop case folder, without changing the unified入口逻辑 or touching brief skills in this phase.

## Problem

The previous flow mostly passed raw materials directly to the writer model. That made outputs prone to:

- weak mainline selection
- poor data prioritization
- news-style openings
- generic endings

## Case-Based Observations

From the current direct report samples on the desktop:

- openings cluster into `event-first` or `policy-background-first`
- the body usually follows one mainline, not multiple parallel highlights
- only one or two quantitative facts are usually emphasized
- closings are restrained and policy-facing

## Design

### 1. Add a direct report planning layer

Create `skills/writing_planner.py` with a direct-report-focused planner that extracts:

- opening strategy
- core theme
- title direction
- priority facts
- priority data
- optional policy anchor

### 2. Keep planning hidden from users

Inject a `planning_note` into the internal writer payload in `skills/direct_report/workflow.py`, but do not expose it in the final returned draft.

### 3. Update the prompt assembly

Update prompt builders to include a `## 写作规划` block before the materials so the model follows a structured prewriting guide.

### 4. Tighten prompt instructions

Update `skills/direct_report/prompts/draft.md` so the model:

- follows the planning note before drafting
- does not copy the planning note into the draft
- still prioritizes user-supplied facts and data

## Out of Scope

- `writer1`
- `writer2`
- multi-source brief logic
- automatic scoring framework
