# HTML 文字审核 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让独立审核 Bot 安全接收单个 `.html/.htm` 文件，审核静态可见文字和页面内部数据一致性，并只返回审核消息。

**Architecture:** 新增标准库 HTML 静态解析器，把可见块级文字和表格行转换为现有通用审核引擎使用的段落列表。HTML 使用独立持久任务类型和 `html` 输入类型，复用现有队列、任务隔离、通用规则、报告归档和 Markdown 主动发送；不进入 Word 多文件组装或 Word 标注流程。

**Tech Stack:** Python 3.13.14、标准库 `html.parser` / `codecs` / `re`、现有 asyncio 审核引擎、SQLite 持久任务执行器、pytest、uv。

## Global Constraints

- 只处理上传 HTML 文件中的静态可见文字，不执行 JavaScript，不联网加载任何资源。
- 忽略明确隐藏元素和非正文标签；第一版不解释复杂 CSS 选择器。
- 必须审核同一 HTML 内日期、数量、金额、比例、状态和统计口径的前后一致性。
- 只返回审核消息和归档 `output/report.md`，不生成标记 HTML。
- HTML 不加入现有 `.docx` 多文件联合审核。
- 不新增第三方依赖；所有命令使用 `uv run --locked ...`。
- 行为变更后同步更新审核、底座、能力、路线和测试交付核心文档。

---

### Task 1: 静态可见文字解析器

**Files:**
- Create: `app/review/html_parser.py`
- Create: `tests/test_review_html.py`

**Interfaces:**
- Consumes: 当前任务 `input/` 下的 `Path`。
- Produces: `ParsedHtmlResult(paragraphs: list[str], encoding: str)`；`parse_html(path: Path | str) -> ParsedHtmlResult`。

- [x] **Step 1: 写解析顺序与隐藏内容的失败测试**

```python
def test_parse_html_extracts_visible_blocks_and_table_rows(tmp_path: Path):
    path = tmp_path / "report.html"
    path.write_text(
        """<html><head><title>标签标题</title><style>.secret{display:none}</style></head>
        <body><h1>经营情况</h1><p>本期收入100万元。</p>
        <ul><li>客户数20户</li></ul>
        <table><tr><th>指标</th><th>数值</th></tr><tr><td>收入</td><td>100万元</td></tr></table>
        <script>prompt injection</script><p hidden>隐藏一</p>
        <p aria-hidden="true">隐藏二</p><p style="display: none">隐藏三</p>
        <a href="https://internal.example">查看详情</a><img alt="属性文字"></body></html>""",
        encoding="utf-8",
    )

    parsed = parse_html(path)

    assert parsed.paragraphs == [
        "经营情况",
        "本期收入100万元。",
        "客户数20户",
        "指标 | 数值",
        "收入 | 100万元",
        "查看详情",
    ]
    assert "prompt injection" not in "\n".join(parsed.paragraphs)
    assert "internal.example" not in "\n".join(parsed.paragraphs)
    assert "属性文字" not in "\n".join(parsed.paragraphs)
```

- [x] **Step 2: 运行测试确认因模块不存在而失败**

Run: `uv run --locked pytest tests/test_review_html.py::test_parse_html_extracts_visible_blocks_and_table_rows -v`

Expected: FAIL，错误为 `ModuleNotFoundError: No module named 'app.review.html_parser'`。

- [x] **Step 3: 实现最小静态解析器**

```python
@dataclass(frozen=True)
class ParsedHtmlResult:
    paragraphs: list[str]
    encoding: str


def parse_html(path: Path | str) -> ParsedHtmlResult:
    content = Path(path).read_bytes()
    text, encoding = _decode_html(content)
    parser = _VisibleTextParser()
    parser.feed(text)
    parser.close()
    paragraphs = parser.paragraphs
    if not paragraphs:
        raise ValueError("HTML 文件中没有可审核的可见文字")
    return ParsedHtmlResult(paragraphs=paragraphs, encoding=encoding)
```

`_VisibleTextParser` 使用忽略栈屏蔽 `head/script/style/template/noscript/svg/canvas` 和明确隐藏元素；块级标签结束时刷新段落；表格单元格先写入当前行，`tr` 结束时以 `" | "` 连接。

- [x] **Step 4: 补编码、畸形 HTML 和空正文失败测试**

```python
def test_parse_html_uses_meta_gb18030(tmp_path: Path):
    path = tmp_path / "legacy.htm"
    path.write_bytes('<meta charset="gb18030"><p>本期收入100万元。</p>'.encode("gb18030"))
    assert parse_html(path).paragraphs == ["本期收入100万元。"]


def test_parse_html_rejects_document_without_visible_text(tmp_path: Path):
    path = tmp_path / "empty.html"
    path.write_text("<script>only code</script><p hidden>hidden</p>", encoding="utf-8")
    with pytest.raises(ValueError, match="没有可审核的可见文字"):
        parse_html(path)
```

- [x] **Step 5: 运行解析器测试并确认通过**

Run: `uv run --locked pytest tests/test_review_html.py -v`

Expected: PASS，覆盖 UTF-8/BOM/meta charset/GB18030、隐藏元素、属性忽略、表格行和空正文。

---

### Task 2: 为 HTML 开放短文通篇数据一致性检查

**Files:**
- Modify: `app/review/general_reviewer.py`
- Modify: `tests/test_review_general.py`

**Interfaces:**
- Consumes: `review_general(..., whole_document_logic_min_chars: int = 200)`。
- Produces: HTML 可显式传 `0`；Word 和直接文字不传参时维持 `200` 字下限。

- [x] **Step 1: 写默认行为不变与 HTML 短文强制检查的失败测试**

```python
def test_whole_document_logic_prompt_allows_explicit_zero_minimum_for_html():
    paragraphs = ["本期客户100户。", "同口径客户为120户。"]
    assert _build_whole_document_logic_prompt(paragraphs, "报告.html") is None
    prompt = _build_whole_document_logic_prompt(
        paragraphs,
        "报告.html",
        min_chars=0,
    )
    assert prompt is not None
    assert "金额、数量、比例" in prompt
```

- [x] **Step 2: 运行测试确认参数尚不存在**

Run: `uv run --locked pytest tests/test_review_general.py::test_whole_document_logic_prompt_allows_explicit_zero_minimum_for_html -v`

Expected: FAIL，错误为 `_build_whole_document_logic_prompt() got an unexpected keyword argument 'min_chars'`。

- [x] **Step 3: 增加显式下限参数并强化数据口径提示**

```python
def _build_whole_document_logic_prompt(
    paragraphs: list[str],
    filename: str,
    *,
    min_chars: int = _GENERAL_WHOLE_DOCUMENT_MIN_CHARS,
) -> str | None:
    total_chars = sum(len(paragraph.strip()) for paragraph in paragraphs)
    if not (min_chars <= total_chars <= _GENERAL_WHOLE_DOCUMENT_MAX_CHARS):
        return None
```

`review_general` 增加同名关键字参数并传给 prompt builder；提示中明确检查金额、数量、比例和统计口径，同时保留不同时间、对象、累计/当期不直接判冲突的现有约束。

- [x] **Step 4: 运行通用审核测试**

Run: `uv run --locked pytest tests/test_review_general.py tests/test_review_general_rules.py -v`

Expected: PASS，旧 Word/文字默认阈值和低置信数据过滤均不变。

---

### Task 3: HTML 持久任务类型和安全工作区

**Files:**
- Modify: `app/review/task_execution.py`
- Modify: `tests/test_review_task_execution.py`

**Interfaces:**
- Produces: `GENERAL_HTML_REVIEW_TASK_TYPE = "review_general_html"`。
- Extends: `GeneralReviewWorkspace.input_kind` 为 `Literal["docx", "text", "html"]`。
- Preserves: 任务载荷仅含任务目录、安全相对输入路径、输入类型、文件名和发送者名称。

- [x] **Step 1: 写 HTML 提交、幂等和载荷隔离的失败测试**

```python
def test_html_review_submission_freezes_input_without_sqlite_body(tmp_path: Path):
    content = b"<p>本期收入100万元。</p>"
    submission = service.submit_file(
        channel="wecom",
        sender_userid="user-1",
        sender_name="User One",
        message_id="html-001",
        task_type=GENERAL_HTML_REVIEW_TASK_TYPE,
        filename="report.html",
        file_bytes=content,
    )
    assert submission.task.payload["input_kind"] == "html"
    assert content not in db_path.read_bytes()
    assert Path(submission.task.payload["task_dir"]).joinpath(
        submission.task.payload["input_file"]
    ).read_bytes() == content
```

同时增加伪造 `.docx` 作为 HTML、目录外引用和 HTML 重复消息只建一项任务的断言。

- [x] **Step 2: 运行专项测试确认 HTML 任务类型尚未定义**

Run: `uv run --locked pytest tests/test_review_task_execution.py -k html -v`

Expected: FAIL，错误为无法导入 `GENERAL_HTML_REVIEW_TASK_TYPE`。

- [x] **Step 3: 扩展任务类型、输入种类和后缀白名单**

```python
GENERAL_HTML_REVIEW_TASK_TYPE = "review_general_html"
_FILE_INPUT_SPEC = {
    GENERAL_REVIEW_TASK_TYPE: ("docx", frozenset({".docx"})),
    NEICAN_REVIEW_TASK_TYPE: ("docx", frozenset({".docx"})),
    HALF_MONTHLY_REVIEW_TASK_TYPE: ("docx", frozenset({".docx"})),
    OFFICIAL_FORMAT_REVIEW_TASK_TYPE: ("docx", frozenset({".docx"})),
    GENERAL_HTML_REVIEW_TASK_TYPE: ("html", frozenset({".html", ".htm"})),
}
```

`submit_file`、`_workspace_from_task`、`_create_workspace` 和 `_safe_input_name` 全部从这一白名单派生，`.htm` 不被静默改成 `.html`，任务目录仍限定在审核根目录。

- [x] **Step 4: 运行持久任务测试**

Run: `uv run --locked pytest tests/test_review_task_execution.py -v`

Expected: PASS，HTML 与原五类单项审核共享幂等、恢复和发送检查点。

---

### Task 4: 企业微信入口、HTML 审核处理和消息交付

**Files:**
- Modify: `app/review/main.py`
- Modify: `tests/test_review_bot.py`
- Modify: `tests/test_review_html.py`

**Interfaces:**
- Produces: `is_html_filename(filename) -> bool`、`is_supported_review_filename(filename) -> bool`。
- Consumes: `parse_html(workspace.input_file)` 和 `review_general(..., whole_document_logic_min_chars=0)`。
- Produces: `PreparedReviewDelivery.text(format_review_result(...))`，无标记文件。

- [x] **Step 1: 写后缀、处理分派和只返回消息的失败测试**

```python
def test_review_file_extensions_accept_docx_html_and_htm():
    assert is_supported_review_filename("report.docx") is True
    assert is_supported_review_filename("report.html") is True
    assert is_supported_review_filename("REPORT.HTM") is True
    assert is_supported_review_filename("report.pdf") is False


def test_persistent_html_review_returns_message_without_marked_file(...):
    delivery = asyncio.run(_process_queued_single_review(workspace, config=config, neican_rules_text=""))
    assert delivery.kind == "text"
    assert "前后逻辑不一致" in delivery.text
    assert (task_dir / "output" / "report.md").is_file()
    assert list((task_dir / "output").glob("marked_*")) == []
```

测试中的假审核器断言 `whole_document_logic_min_chars == 0`，并返回一条 `general-logic-inconsistency`。

- [x] **Step 2: 运行测试确认入口仍只认识 Word**

Run: `uv run --locked pytest tests/test_review_html.py tests/test_review_bot.py -k 'html or review_file_extensions' -v`

Expected: FAIL，HTML 后缀或任务处理分支不存在。

- [x] **Step 3: 实现 HTML 独立入队分支和处理分支**

在文件下载和大小校验后：

```python
if is_html_filename(filename):
    submission = review_tasks.submit_file(
        channel="wecom",
        sender_userid=sender,
        sender_name=english_name,
        message_id=extract_message_id(frame),
        task_type=GENERAL_HTML_REVIEW_TASK_TYPE,
        filename=filename,
        file_bytes=buffer,
    )
    await _reply_queued_review_acceptance(
        ws_client,
        frame,
        generate_req_id("review-html-queued"),
        review_label="HTML文字审核",
        created=submission.created,
        input_label="这份HTML文件",
        acknowledgment_already_sent=True,
    )
    return
```

该分支在 `ReviewIntakeStore.add_file` 之前执行，因此不进入 Word 自动批次。后台处理 HTML 时解析静态文字、调用通用审核、保存原件和报告，并无论是否发现问题都格式化为文字交付；已知的“无可见文字”解析错误返回安全提示。

- [x] **Step 4: 更新所有用户提示**

把拒接、欢迎语、注册成功提示和文件指引统一改为可接收 `.docx`、`.html/.htm` 或直接文字；公文格式模式仍明确只接受 `.docx`。

- [x] **Step 5: 运行审核专项回归**

Run: `uv run --locked pytest tests/test_review_html.py tests/test_review_task_execution.py tests/test_review_general.py tests/test_review_general_rules.py tests/test_review_intake.py tests/test_review_bot.py -v`

Expected: PASS，HTML 单项任务和原 Word/文字/格式/多文件行为同时通过。

---

### Task 5: 核心文档、项目闸门和完整验证

**Files:**
- Modify: `app/review/README.md`
- Modify: `docs/development/README.md`
- Modify: `docs/development/architecture.md`
- Modify: `docs/development/TODO.md`
- Modify: `docs/agent-platform/README.md`
- Modify: `docs/capabilities/README.md`
- Modify: `docs/development/testing-and-delivery.md`

**Interfaces:**
- Documents: HTML 是审核模块独立能力，不扩大写作统一文档服务白名单。
- Tests: 新增 `tests/test_review_html.py` 并纳入审核专项命令。

- [x] **Step 1: 更新审核与核心文档**

把所有“HTML 文件支持暂缓”的绝对表述改为：写作统一文档服务仍不支持 HTML；独立审核 Bot 已支持单个静态 HTML 文字审核。记录不执行脚本/网络、显式隐藏规则、复杂 CSS 边界、短文数据一致性和只返回消息。

- [x] **Step 2: 更新路线与测试命令**

在 `docs/development/TODO.md` 登记并完成 HTML 审核节点；在测试交付文档加入：

```bash
uv run --locked pytest tests/test_review_html.py tests/test_review_task_execution.py tests/test_review_general.py tests/test_review_general_rules.py tests/test_review_intake.py tests/test_review_bot.py -v
```

- [x] **Step 3: 运行文档闸门和格式检查**

Run: `uv run --locked python scripts/project_docs.py check`

Expected: `核心文档检查通过。`

Run: `git diff --check`

Expected: 无输出，退出码 0。

- [x] **Step 4: 运行审核专项和项目离线回归**

Run: `uv run --locked pytest tests/test_review_html.py tests/test_review_task_execution.py tests/test_review_general.py tests/test_review_general_rules.py tests/test_review_intake.py tests/test_review_bot.py tests/test_official_format_review.py tests/test_review_multi_file.py -v`

Expected: 全部 PASS。

Run: `uv run --locked pytest -m "not live_llm" -v`

Expected: 全部离线测试 PASS；若仓库没有该 marker，则改用项目既有排除真实模型用例的完整命令，并在交付中写明实际命令和结果。

- [x] **Step 5: 提交并受管推送**

```bash
git add app/review/html_parser.py app/review/main.py app/review/task_execution.py app/review/README.md tests/test_review_html.py tests/test_review_task_execution.py tests/test_review_general.py tests/test_review_bot.py docs/development/README.md docs/development/architecture.md docs/development/TODO.md docs/agent-platform/README.md docs/capabilities/README.md docs/development/testing-and-delivery.md docs/superpowers/plans/2026-07-16-review-html.md
git commit -m "feat(review): audit visible HTML text"
uv run --locked python scripts/project_docs.py push --summary "新增HTML静态文字审核" --impact "审核Bot可检查单个HTML的可见文字和前后数据一致性，并只返回审核消息" --next-step "使用真实脱敏HTML完成企业微信入口验收"
uv run --locked python scripts/project_docs.py check-sync
```

Expected: 提交成功、受管推送成功，`check-sync` 显示本地与远端已同步。

---

### Task 6: 网页 PPT 错误页码定位

**Files:**
- Modify: `app/review/html_parser.py`
- Modify: `app/review/output_formatter.py`
- Modify: `app/review/main.py`
- Modify: `tests/test_review_html.py`
- Modify: `tests/test_review_general.py`
- Modify: `app/review/README.md`
- Modify: `docs/development/README.md`
- Modify: `docs/development/architecture.md`
- Modify: `docs/development/TODO.md`
- Modify: `docs/capabilities/README.md`
- Modify: `docs/development/testing-and-delivery.md`

**Interfaces:**
- Extends: `ParsedHtmlResult(paragraphs: list[str], paragraph_pages: list[int | None], encoding: str)`；两个列表必须等长。
- Extends: `format_review_result(..., paragraph_pages: Sequence[int | None] | None = None)`；只在 HTML 调用时传值。
- Extends: `save_review_to_directory(..., paragraph_pages: Sequence[int | None] | None = None)`；归档报告与用户消息使用同一映射。
- Preserves: Word、直接文字、内参、半月报和格式审核不传映射，输出不变。

- [x] **Step 1: 写 slide 段落页码映射的失败测试**

```python
def test_parse_html_maps_visible_paragraphs_to_slide_pages(tmp_path: Path):
    path = tmp_path / "deck.html"
    path.write_text(
        """<div class="slide slide-cover"><h1>封面</h1></div>
        <div class="slide slide-content"><p>本期客户100户。</p>
        <table><tr><td>客户</td><td>120户</td></tr></table></div>
        <p>页外提示</p>""",
        encoding="utf-8",
    )
    parsed = parse_html(path)
    assert parsed.paragraphs == ["封面", "本期客户100户。", "客户 | 120户", "页外提示"]
    assert parsed.paragraph_pages == [1, 2, 2, None]
```

- [x] **Step 2: 写 HTML 消息、归档报告和普通 HTML 兜底的失败测试**

```python
def test_format_review_result_uses_html_page_or_paragraph_location():
    finding = Finding(
        rule_id="general-logic-inconsistency",
        paragraph_index=1,
        line_number=2,
        original_text="同口径客户为120户。",
        description="与前页同口径的100户不一致",
        target_text="120户",
    )
    result = ReviewResult(
        findings=[finding],
        total_rules=1,
        passed_rules=0,
        filename="deck.html",
    )
    paged = format_review_result(
        result,
        "deck.html",
        doc_type=DocumentType.GENERAL,
        paragraph_pages=[1, 2],
    )
    fallback = format_review_result(
        result,
        "article.html",
        doc_type=DocumentType.GENERAL,
        paragraph_pages=[None, None],
    )
    assert "位置：第2页" in paged
    assert "位置：第2段" in fallback
```

持久任务测试把两段正文分别放进两个 `class="slide"` 容器，并同时断言 `delivery.text` 与 `output/report.md` 含 `位置：第2页`。

- [x] **Step 3: 运行测试确认页码接口尚不存在**

Run: `uv run --locked pytest tests/test_review_html.py -k 'slide_pages or html_page_or_paragraph or persistent_html' -v`

Expected: FAIL，`ParsedHtmlResult` 没有 `paragraph_pages`，或 `format_review_result` 不接受该参数。

- [x] **Step 4: 在静态解析器中保留 slide 页码**

`_VisibleTextParser` 只把 class token 精确等于 `slide` 的可见元素视为页面容器，按 DOM 顺序编号。每次 `_flush_text()` 或 `_flush_row()` 写入段落时，同时写入当前 slide 页码；没有活动 slide 时写 `None`。关闭或畸形标签清理时同步退出页面上下文，不读取 `page-label` 文本推算页码。

```python
@dataclass(frozen=True)
class ParsedHtmlResult:
    paragraphs: list[str]
    paragraph_pages: list[int | None]
    encoding: str
```

- [x] **Step 5: 仅为 HTML 格式化位置并贯通归档**

`format_review_result` 增加可选页码映射：映射有效且当前段落有页码时输出 `位置：第 N 页`；传入映射但当前段落为 `None` 时输出 `位置：第 N 段`；未传映射时不增加位置行。HTML 处理分支把 `parsed_html.paragraph_pages` 同时传给 `save_review_to_directory` 和最终 `PreparedReviewDelivery.text(...)`。

```python
if paragraph_pages is not None:
    page = paragraph_pages[f.paragraph_index]
    location = f"第{page}页" if page is not None else f"第{f.paragraph_index + 1}段"
    lines.append(f"位置：{location}")
```

- [x] **Step 6: 运行专项测试并确认 Word 输出不变**

Run: `uv run --locked pytest tests/test_review_html.py tests/test_review_general.py tests/test_review_bot.py -v`

Expected: PASS；HTML 有页码或段落定位，未传 `paragraph_pages` 的 Word 通用审核输出不新增位置行。

- [x] **Step 7: 同步核心文档与完成验证**

在审核 README、能力、架构、路线和测试交付文档中记录：网页 PPT 按 `class="slide"` 的 DOM 顺序定位页码；普通 HTML 回退到段落位置；不计算浏览器打印分页。然后运行：

```bash
uv run --locked python scripts/project_docs.py check
uv run --locked pytest tests --ignore=tests/test_reviewer.py -q
uv run --locked python tests/test_review_bot.py
git diff --check
```

Expected: 核心文档检查通过、全仓离线测试和审核 Bot 独立保护测试全部通过、差异格式检查无输出。

- [x] **Step 8: 提交、受管推送并重启审核 Bot**

```bash
git add app/review/html_parser.py app/review/output_formatter.py app/review/main.py tests/test_review_html.py tests/test_review_general.py app/review/README.md docs/development/README.md docs/development/architecture.md docs/development/TODO.md docs/capabilities/README.md docs/development/testing-and-delivery.md docs/superpowers/plans/2026-07-16-review-html.md
git commit -m "fix(review): show HTML slide locations"
uv run --locked python scripts/project_docs.py push --summary "补充HTML审核页码定位" --impact "网页PPT审核消息和归档报告可显示每条错误所在页，普通HTML回退到段落位置" --next-step "使用同一份脱敏HTML复测页码与数据一致性结果"
uv run --locked python scripts/project_docs.py check-sync
```

停止旧审核 Bot 进程后，用 `uv run --locked python -u -m app.review.main --console` 启动新进程，并确认企业微信认证成功。
