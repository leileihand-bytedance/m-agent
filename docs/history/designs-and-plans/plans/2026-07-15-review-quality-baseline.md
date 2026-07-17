# 通用审核真实文件质量基线实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立可重复运行的通用审核真实文件基线，并交付首批 5 份真实样本的人工评分包。

**Architecture:** 纯函数模块负责样本发现、去重、特征识别、选样和结果序列化；CLI 负责调用现有审核主链路并把敏感产物写到统一外部数据目录。审核引擎增加可选的线程安全调用计数器，不改变 Bot 默认行为。

**Tech Stack:** Python 3.13、uv、pytest、python-docx、现有 Anthropic 兼容审核客户端、`@oai/artifact-tool`。

## Global Constraints

- 所有 Python 命令使用 `uv run --locked ...`。
- 先写测试并确认失败，再写最小实现。
- 用户原件、原文、文件名、评分结果和密钥不得进入 Git。
- 行为变更后同步审核核心文档并运行项目文档检查。

---

### Task 1: 样本发现和选择

**Files:**
- Create: `app/review/quality_evaluation.py`
- Test: `tests/test_review_quality_evaluation.py`

**Interfaces:**
- Produces: `discover_general_candidates(review_tasks_root: Path) -> list[ReviewSampleCandidate]`
- Produces: `select_baseline_cases(candidates: Sequence[ReviewSampleCandidate], limit: int = 5) -> list[SelectedReviewCase]`

- [ ] **Step 1: Write the failing tests**

测试临时 Word 的 SHA-256 去重、内参/半月报过滤、问卷/表格/附件引用/长度特征，以及五类样本不重复选择。

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --locked pytest tests/test_review_quality_evaluation.py -v`

Expected: FAIL because `app.review.quality_evaluation` does not exist.

- [ ] **Step 3: Write minimal implementation**

实现不可变样本数据类、Word 特征检查、内容哈希去重和确定性贪心选样；不读取或写入评分结果。

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --locked pytest tests/test_review_quality_evaluation.py -v`

Expected: PASS.

### Task 2: 主链路运行和模型调用计数

**Files:**
- Modify: `app/review/general_reviewer.py`
- Modify: `app/review/quality_evaluation.py`
- Create: `scripts/review_quality.py`
- Test: `tests/test_review_quality_evaluation.py`

**Interfaces:**
- Produces: `ReviewRunMetrics.record_model_call() -> None`
- Produces: `run_baseline(...) -> BaselineRunSummary`

- [ ] **Step 1: Write the failing tests**

测试并发计数、结果 JSON 必含审核发现/耗时/调用次数、单样本失败继续和输出目录必须位于数据根目录。

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --locked pytest tests/test_review_quality_evaluation.py -v`

Expected: FAIL on missing run and metrics APIs.

- [ ] **Step 3: Write minimal implementation**

给 `review_general` 增加默认关闭的可选统计对象，在每次真实模型请求前计数；CLI 顺序运行样本、复制原件、生成标注文档并写 JSON/CSV。

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --locked pytest tests/test_review_quality_evaluation.py tests/test_review_general.py tests/test_review_general_rules.py -v`

Expected: PASS.

### Task 3: 首批真实运行和评分工作簿

**Files:**
- Runtime only: `M-Agent-Files/evaluations/review/<run_id>/`
- Temporary builder: `/tmp/review_quality_workbook.mjs`

**Interfaces:**
- Consumes: CLI 生成的 `manifest.json`、`summary.json` 和 `scoring.csv`
- Produces: `通用审核人工评分表.xlsx`

- [ ] **Step 1: Run the five-file baseline**

Run: `uv run --locked python scripts/review_quality.py run --limit 5 --run-id 20260715-baseline-v1`

Expected: 5 个互不重复的通用审核样本都有 `result.json`，失败样本明确记录错误。

- [ ] **Step 2: Build the workbook**

使用 `@oai/artifact-tool` 创建说明、样本清单、问题评分、漏报补充和汇总五个工作表，设置数据验证、筛选、冻结标题和汇总公式。

- [ ] **Step 3: Validate the workbook**

检查每个工作表关键范围和公式错误，渲染所有工作表并目视确认无截断、重叠或不可读内容。

### Task 4: 文档、回归和交付

**Files:**
- Modify: `app/review/README.md`
- Modify: `docs/development/TODO.md`
- Modify: `docs/development/testing-and-delivery.md`
- Modify: `docs/capabilities/README.md`
- Modify: `docs/development/README.md`

- [ ] **Step 1: Update core documentation**

记录评测命令、外部产物结构、人工评分口径、首批运行状态和下一步由人工评分驱动的优化流程。

- [ ] **Step 2: Run verification**

Run: `uv run --locked pytest tests/test_review_quality_evaluation.py tests/test_review_general.py tests/test_review_general_rules.py tests/test_error_marker.py -v`

Run: `uv run --locked python scripts/project_docs.py check`

Expected: all pass.

- [ ] **Step 3: Commit and push through the managed workflow**

只暂存代码、测试和核心文档，确认没有真实样本或评测产物后提交；使用受管推送命令同步 `main`，最后运行 `check-sync`。
