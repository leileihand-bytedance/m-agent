# DeepSeek-Assisted Shenyinxie News Selection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent industry roundup articles from being treated as full-text WeBank news, add DeepSeek-assisted positive-achievement selection, and support audited extractive excerpts plus trusted-source expansion.

**Architecture:** Keep deterministic date, domain, readability, URL, deduplication, and excerpt validation in `selection.py`. Add one structured `llm_writer` assessment per hard-gated candidate, then select full-text WeBank features first, search expanded trusted sources only when needed, and use validated excerpts only as the final fallback. Preserve original URLs and add an editor note to excerpted Word blocks.

**Tech Stack:** Python 3.13.14, Pydantic v2, Pydantic AI through `ToolGateway`, python-docx, pytest, uv.

## Global Constraints

- Run Python, tests, scripts, and Bot commands only through `uv run --locked ...`.
- Search summaries never become article bodies; all output text comes from `web_reader` pages.
- WeBank-owned websites and official accounts remain excluded as sources.
- Excerpt bodies consist only of source paragraphs verified against the original article; no generated bridge text or invented facts.
- Date, domain, readability, URL safety, deduplication, and excerpt integrity are deterministic gates that the model cannot override.
- Update all affected core documents and run the staged documentation gate before delivery.

---

### Task 1: Structured editorial assessment and excerpt validation

**Files:**
- Modify: `skills/shenyinxie_news/schema.py`
- Modify: `skills/shenyinxie_news/selection.py`
- Test: `tests/test_shenyinxie_news_selection.py`

**Interfaces:**
- Produces: `ArticleAssessment`, `ContentMode`, `SubjectStrength` Pydantic types.
- Produces: `apply_editorial_assessment(candidate, assessment) -> NewsCandidate | None`.
- Produces: `validate_excerpt_paragraphs(source_body, paragraphs) -> str | None`.

- [x] **Step 1: Write failing selection tests**

Add tests proving that a title containing “微众” is not enough for full-text selection, a `primary + positive` assessment keeps the full text, a `substantial + positive` assessment accepts only exact ordered source paragraphs, and missing/reordered/one-line-list excerpts are rejected.

```python
assessment = ArticleAssessment(
    decision="extract",
    is_positive_achievement=True,
    subject_strength="substantial",
    suggested_title="微众银行连续两年实施利润分配",
    excerpt_paragraphs=[source_paragraph_1, source_paragraph_2],
    achievement_types=["经营成果"],
    reason="综合稿包含可独立成立的微众银行成果段落",
)
selected = apply_editorial_assessment(candidate, assessment)
assert selected is not None
assert selected.content_mode == "extract"
assert selected.body == f"{source_paragraph_1}\n{source_paragraph_2}"
```

- [x] **Step 2: Run RED tests**

Run: `uv run --locked pytest tests/test_shenyinxie_news_selection.py -q`

Expected: FAIL because the structured assessment types and validators do not exist and the current title rule still passes the roundup.

- [x] **Step 3: Implement minimal schema and deterministic validation**

Add the assessment fields from the approved design, add candidate fields `content_mode`, `source_title`, `editor_note`, and `achievement_types`, and replace title-based core-subject admission with assessment application. Normalize CRLF and surrounding whitespace only; each excerpt paragraph must occur in source order and the combined excerpt must contain at least two substantive paragraphs or one sufficiently detailed paragraph with multiple WeBank-specific facts.

- [x] **Step 4: Run GREEN tests**

Run: `uv run --locked pytest tests/test_shenyinxie_news_selection.py -q`

Expected: PASS.

### Task 2: Model-assisted staged workflow

**Files:**
- Modify: `skills/shenyinxie_news/workflow.py`
- Create: `skills/shenyinxie_news/prompts/select.md`
- Modify: `tests/test_shenyinxie_news_workflow.py`

**Interfaces:**
- Consumes: `ArticleAssessment` and `apply_editorial_assessment` from Task 1.
- Produces: `_assess_candidate(candidate, tools) -> ArticleAssessment` using `tools.call("llm_writer", payload)`.
- Produces: `_collect_candidates(queries, ..., seen_urls) -> list[NewsCandidate]` for staged search.

- [x] **Step 1: Write failing workflow tests**

Add a fake `llm_writer` that returns assessments by URL. Cover: full-text features selected before excerpts; the real-failure-equivalent roundup becomes an excerpt instead of full text; neutral/negative/mention-only candidates are rejected; expanded queries run only when fewer than three full-text candidates survive; model failure skips one candidate and continues; all model failures return a safe explicit message.

```python
def llm_writer(payload):
    calls.append(("llm_writer", payload["candidate_url"]))
    return assessments[payload["candidate_url"]]
```

- [x] **Step 2: Run RED tests**

Run: `uv run --locked pytest tests/test_shenyinxie_news_workflow.py -q`

Expected: FAIL because the current workflow never calls `llm_writer` and has no staged search or excerpt fallback.

- [x] **Step 3: Implement the minimal staged workflow**

Use one structured model call per hard-gated unique candidate with `output_type=ArticleAssessment`. Collect regular and expanded results separately while sharing URL deduplication. Keep `full_text` candidates immediately, retain valid `extract` candidates in a fallback pool, and reject the rest. If fewer than three full-text candidates remain after both search layers, fill remaining slots from excerpt candidates. Deduplicate before final scoring and select no more than three.

- [x] **Step 4: Run GREEN tests**

Run: `uv run --locked pytest tests/test_shenyinxie_news_workflow.py -q`

Expected: PASS.

### Task 3: Word and structured-output excerpt disclosure

**Files:**
- Modify: `skills/shenyinxie_news/schema.py`
- Modify: `skills/shenyinxie_news/workflow.py`
- Modify: `skills/shenyinxie_news/docx_output.py`
- Modify: `tests/test_shenyinxie_news_docx.py`
- Modify: `tests/test_shenyinxie_news_workflow.py`

**Interfaces:**
- Produces: `SelectedArticle.content_mode`, `source_title`, and `editor_note`.
- Consumes: the same fields in template and scratch Word generation.

- [x] **Step 1: Write failing DOCX tests**

Assert that full-text articles have no editor note, while excerpt articles include the original title and `说明：本文根据原报道中微众银行相关内容摘编。` next to the original link in both placeholder-template and scratch output.

- [x] **Step 2: Run RED tests**

Run: `uv run --locked pytest tests/test_shenyinxie_news_docx.py tests/test_shenyinxie_news_workflow.py -q`

Expected: FAIL because current `SelectedArticle` and Word blocks lack excerpt metadata.

- [x] **Step 3: Implement minimal output changes**

Populate the new fields when converting candidates and render the note only for `content_mode="extract"`. Keep the displayed edited title at the top, and display `原报道标题：...` plus the editor note before or immediately after the original URL.

- [x] **Step 4: Run GREEN tests**

Run: `uv run --locked pytest tests/test_shenyinxie_news_docx.py tests/test_shenyinxie_news_workflow.py -q`

Expected: PASS.

### Task 4: Trusted source expansion

**Files:**
- Modify: `skills/shenyinxie_news/media_sources.yaml`
- Modify: `skills/shenyinxie_news/selection.py`
- Modify: `tests/test_shenyinxie_news_whitelist.py`
- Modify: `tests/test_shenyinxie_news_selection.py`

**Interfaces:**
- Produces: `generate_primary_search_queries(...)` and `generate_expanded_search_queries(...)`.
- Keeps: one `MediaWhitelist` with explicit source tier/category metadata; no free-form domain acceptance.

- [x] **Step 1: Verify candidate source domains from their own public pages**

Confirm exact current domains for selected national industry media, Guangdong/Shenzhen mainstream media, and authoritative public platforms. Do not add a domain based only on a search-result label.

- [x] **Step 2: Write failing media and query tests**

Assert that newly approved domains match, WeBank-owned domains remain rejected, expanded queries are separate from primary queries, and both query sets carry the exact publication period.

- [x] **Step 3: Run RED tests**

Run: `uv run --locked pytest tests/test_shenyinxie_news_whitelist.py tests/test_shenyinxie_news_selection.py -q`

Expected: FAIL because the new domains and staged query functions are absent.

- [x] **Step 4: Add the smallest verified expansion set**

Add only verified sources that fit the approved categories, with explicit names, domains, categories, and tiers. Keep all source acceptance whitelist-based.

- [x] **Step 5: Run GREEN tests**

Run: `uv run --locked pytest tests/test_shenyinxie_news_whitelist.py tests/test_shenyinxie_news_selection.py -q`

Expected: PASS.

### Task 5: Skill validation and core documentation

**Files:**
- Modify: `skills/shenyinxie_news/SKILL.md`
- Modify: `skills/shenyinxie_news/config.yaml`
- Modify: `docs/capabilities/README.md`
- Modify: `docs/development/architecture.md`
- Modify: `docs/development/README.md`
- Modify: `docs/development/TODO.md`
- Modify: `docs/development/testing-and-delivery.md`
- Modify: `app/writing/README.md`

- [x] **Step 1: Update the Skill contract and project facts**

Document the reporting purpose, the three selection layers, DeepSeek structured judgment, extract-only fallback, source expansion boundary, editor note, model-failure behavior, and exact verification commands. Remove statements that selection remains purely rule-based or that source expansion never occurs.

- [x] **Step 2: Validate the Skill and documentation**

Run:

```bash
uv run --locked python "$CODEX_HOME/skills/.system/skill-creator/scripts/quick_validate.py" skills/shenyinxie_news
uv run --locked python scripts/project_docs.py check
```

Project documentation check exits 0. The generic Codex skill validator is not applicable because this is an M-Agent business skill registered by `config.yaml`, not a `$CODEX_HOME/skills` package with YAML frontmatter; project registry and writing-entry tests are the authoritative validation.

### Task 6: Regression, live diagnostic, deployment, and restart

**Files:**
- Modify only if verification exposes a real defect in the approved scope.

- [x] **Step 1: Run focused and full offline regression**

Run:

```bash
uv run --locked pytest tests/test_shenyinxie_news_*.py tests/test_platform_builtin_tools.py tests/test_platform_app.py tests/test_writing_platform_bot.py -q
uv run --locked pytest --ignore=tests/test_reviewer.py -q
uv run --locked python tests/test_review_bot.py
```

Expected: all selected suites pass with zero failures.

- [x] **Step 2: Run a real DeepSeek diagnostic for the prior period**

Execute the Skill for `today=2026-07-16` in a temporary task output directory, record only candidate titles/domains, model decisions, selected modes, and output path. Verify the previous multi-bank roundup is not emitted as full text. Do not log credentials or full article bodies.

- [x] **Step 3: Stage and run the staged documentation gate**

Run:

```bash
git diff --check
git add skills/shenyinxie_news tests/test_shenyinxie_news_selection.py tests/test_shenyinxie_news_workflow.py tests/test_shenyinxie_news_docx.py tests/test_shenyinxie_news_whitelist.py docs/history/designs-and-plans/plans/2026-07-17-shenyinxie-news-selection.md docs/capabilities/README.md docs/development/architecture.md docs/development/README.md docs/development/TODO.md docs/development/testing-and-delivery.md app/writing/README.md
uv run --locked python scripts/project_docs.py check --staged
```

Expected: clean diff and documentation gate exit 0; no runtime files, secrets, real user materials, or local policy files staged.

- [ ] **Step 4: Commit, managed-push, and check sync**

Create one implementation commit, run the managed push command with a business-focused summary/impact/next-step, then run `uv run --locked python scripts/project_docs.py check-sync`.

- [ ] **Step 5: Restart and verify the writing Bot**

Stop only the existing `app.writing.bot` process, restart it with `uv run --locked python -m app.writing.bot`, and verify configuration, enterprise-WeChat authentication, heartbeat, and process liveness.
