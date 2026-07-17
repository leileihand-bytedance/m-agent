# PPT Low-Error Review Phase One Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在独立审核 Bot 中增加单个 `.pptx` 的低级错误审核，返回仅包含页码、原文证据和事实问题的企业微信文字结果。

**Architecture:** 新建 `app/review/ppt/` 独立业务包，只消费底座 `DocumentService` 的 PPTX 结构化解析结果。确定性规则、模型提示词、证据校验、结果格式和测试全部独立维护；现有持久任务服务只增加 `pptx` 输入类型和多段文字交付，不导入任何通用审核业务代码。

**Tech Stack:** uv 管理的 Python 3.13.14、python-pptx、`app.platform.documents.DocumentService`、Anthropic 兼容审核模型客户端、SQLite 持久任务执行器、pytest。

## Global Constraints

- 运行代码、测试和脚本统一使用 `uv run --locked ...`。
- 测试先行：先写失败测试，确认失败，再写最小实现。
- PPT 业务代码不得导入通用审核、文字审核、Word 审核、内参、半月报、公文格式审核或其规则加载器。
- 只审核单个 `.pptx` 中的可编辑文本框、表格和可读取图表；不审核备注，不做图片 OCR。
- 只判断同一 PPT 内部一致性，不联网核实外部事实。
- 输出只包含页码、原文证据和事实问题，不包含建议、推荐改法或替用户选择正确口径。
- `.ppt`、损坏文件、加密文件和没有可编辑文字的文件必须给出明确安全提示。
- 输入只允许当前任务 `input/`，解析产物只写当前任务 `work/`，结果只写当前任务 `output/`。
- 用户原件、真实原文、日志、密钥和本机任务路径不得进入 Git。
- 受影响核心文档：`app/review/README.md`、`docs/development/README.md`、`docs/development/architecture.md`、`docs/development/TODO.md`、`docs/agent-platform/README.md`、`docs/capabilities/README.md`、`docs/development/testing-and-delivery.md`。

---

## File Structure

### 新建文件

- `app/review/ppt/__init__.py`：公开 `review_pptx`、`format_ppt_review_messages` 和核心类型。
- `app/review/ppt/models.py`：独立页、对象、问题、警告和结果数据结构。
- `app/review/ppt/extractor.py`：把 `DocumentService` 结果转换为 PPT 审核结构，排除备注和图片。
- `app/review/ppt/rules.py`：占位符、引号、连续标点和同组序号规则。
- `app/review/ppt/evidence.py`：本地候选、跨页双边证据和去重校验。
- `app/review/ppt/reviewer.py`：独立模型调用、分页语言审核和全 PPT 一致性审核。
- `app/review/ppt/formatter.py`：企业微信 Markdown 文字结果及安全分段。
- `app/review/ppt/prompts/language.md`：分页低级文字错误提示词。
- `app/review/ppt/prompts/consistency.md`：同一 PPT 跨页一致性提示词。
- `tests/test_review_ppt_extractor.py`：结构提取和安全边界测试。
- `tests/test_review_ppt_rules.py`：确定性规则测试。
- `tests/test_review_ppt_reviewer.py`：模型候选、证据过滤和独立性测试。
- `tests/test_review_ppt_formatter.py`：无建议输出、零问题和长消息分段测试。
- `tests/test_review_ppt_bot.py`：任务提交、处理、入口分流和恢复集成测试。

### 修改文件

- `app/review/task_execution.py`：登记 PPT 专用任务和 `pptx` 输入类型；支持一次任务交付多段文字。
- `app/review/main.py`：审核 Bot 接收 `.pptx`、直接入 PPT 专用队列并调用独立审核包。
- `tests/test_review_task_execution.py`：PPT 输入冻结、多段文字交付和恢复回归。
- `tests/test_review_bot.py`：支持格式和拒接话术回归。
- 上述七份核心文档：同步实际能力、边界、测试命令和 `TODO-020` 状态。

---

### Task 1: 独立数据结构与 PPT 提取器

**Files:**
- Create: `app/review/ppt/__init__.py`
- Create: `app/review/ppt/models.py`
- Create: `app/review/ppt/extractor.py`
- Test: `tests/test_review_ppt_extractor.py`

**Interfaces:**
- Consumes: `DocumentService.parse(path, allowed_root=..., work_dir=...) -> DocumentArtifact`
- Produces: `extract_ppt_document(path: Path, *, task_dir: Path) -> PptReviewDocument`
- Produces: `PptElement`, `PptSlide`, `PptFinding`, `PptReviewResult`

- [ ] **Step 1: 写结构提取失败测试**

测试用 `python-pptx` 临时生成含标题、正文、表格、图片和备注的 PPTX：

```python
def test_extract_ppt_document_keeps_editable_content_and_excludes_notes_and_images(tmp_path):
    task_dir = tmp_path / "task"
    input_dir = task_dir / "input"
    input_dir.mkdir(parents=True)
    path = input_dir / "经营汇报.pptx"
    _make_pptx_with_text_table_picture_and_notes(path)

    document = extract_ppt_document(path, task_dir=task_dir)

    assert document.filename == "经营汇报.pptx"
    assert document.page_count == 1
    elements = document.slides[0].elements
    assert {element.kind for element in elements} == {"text", "table"}
    assert any("经营情况" in element.text for element in elements)
    assert any("客户数\t100万户" in element.text for element in elements)
    assert all("演讲备注" not in element.text for element in elements)
    assert document.excluded_image_count == 1
    assert (task_dir / "work" / "documents").is_dir()
```

再覆盖路径越界和无可编辑文字：

```python
def test_extract_ppt_document_rejects_path_outside_task_input(tmp_path):
    with pytest.raises(ValueError, match="任务 input"):
        extract_ppt_document(tmp_path / "outside.pptx", task_dir=tmp_path / "task")

def test_extract_ppt_document_rejects_ppt_without_editable_text(tmp_path):
    with pytest.raises(PptReviewInputError, match="没有可审核的可编辑文字"):
        extract_ppt_document(path, task_dir=task_dir)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run --locked pytest tests/test_review_ppt_extractor.py -v`

Expected: FAIL，提示 `app.review.ppt` 或 `extract_ppt_document` 尚不存在。

- [ ] **Step 3: 实现独立数据结构**

`models.py` 使用冻结 dataclass，核心定义保持以下签名：

```python
PptElementKind = Literal["text", "table", "chart"]
PptFindingCategory = Literal[
    "typo", "grammar", "punctuation", "name", "placeholder",
    "sequence", "data_inconsistency", "content_inconsistency",
]

@dataclass(frozen=True)
class PptElement:
    element_id: str
    slide_number: int
    kind: PptElementKind
    text: str
    bbox: tuple[int, int, int, int] | None = None

@dataclass(frozen=True)
class PptSlide:
    slide_number: int
    elements: tuple[PptElement, ...]

@dataclass(frozen=True)
class PptReviewDocument:
    filename: str
    page_count: int
    slides: tuple[PptSlide, ...]
    excluded_image_count: int = 0
    warnings: tuple[str, ...] = ()

@dataclass(frozen=True)
class PptFinding:
    rule_id: str
    category: PptFindingCategory
    slide_number: int
    element_id: str
    target_text: str
    description: str
    related_slide_number: int | None = None
    related_element_id: str = ""
    related_text: str = ""

@dataclass(frozen=True)
class PptReviewResult:
    filename: str
    page_count: int
    findings: tuple[PptFinding, ...]
    excluded_image_count: int = 0
    warnings: tuple[str, ...] = ()
    consistency_complete: bool = True

class PptReviewInputError(ValueError):
    """PPT文件无法进入低级错误审核。"""

@dataclass(frozen=True)
class PptLocalCandidate:
    category: PptFindingCategory
    slide_number: int
    element_id: str
    target_text: str
    description: str

@dataclass(frozen=True)
class PptCrossCandidate:
    category: Literal["data_inconsistency", "content_inconsistency"]
    slide_number: int
    element_id: str
    target_text: str
    related_slide_number: int
    related_element_id: str
    related_text: str
    description: str
    same_subject: bool
    same_time_scope: bool
    same_metric_scope: bool
```

不得定义 `suggestion` 字段。为 `PptReviewResult` 增加 `to_dict()`，只用于任务 `output/result.json`。

- [ ] **Step 4: 实现安全提取器**

`extractor.py`：

```python
_LOCATION_RE = re.compile(r"^slide:(\d+)(?:/shape:(\d+))?$")
_AUDITABLE_KINDS = {"text", "table", "chart"}

def extract_ppt_document(path: Path, *, task_dir: Path) -> PptReviewDocument:
    resolved_task = task_dir.resolve(strict=True)
    input_root = (resolved_task / "input").resolve(strict=True)
    resolved_path = path.resolve(strict=True)
    if not resolved_path.is_relative_to(input_root):
        raise ValueError("PPT审核文件必须位于当前任务 input 目录")
    work_dir = resolved_task / "work"
    work_dir.mkdir(parents=True, exist_ok=True)
    artifact = DocumentService().parse(
        resolved_path,
        allowed_root=input_root,
        work_dir=work_dir,
    )
    if artifact.format != DocumentFormat.PPTX:
        raise PptReviewInputError("PPT审核只支持 .pptx 文件")
    elements_by_slide: dict[int, list[PptElement]] = defaultdict(list)
    for block in artifact.blocks:
        if block.kind not in _AUDITABLE_KINDS or not block.text.strip():
            continue
        match = _LOCATION_RE.match(block.location)
        if not match:
            continue
        slide_number = int(match.group(1))
        elements_by_slide[slide_number].append(
            PptElement(
                element_id=block.location,
                slide_number=slide_number,
                kind=cast(PptElementKind, block.kind),
                text=block.text.strip(),
                bbox=block.bbox,
            )
        )
    if not elements_by_slide:
        raise PptReviewInputError("PPT中没有可审核的可编辑文字")
    return PptReviewDocument(
        filename=resolved_path.name,
        page_count=artifact.page_count or 0,
        slides=tuple(
            PptSlide(
                slide_number=number,
                elements=tuple(elements_by_slide.get(number, ())),
            )
            for number in range(1, (artifact.page_count or 0) + 1)
        ),
        excluded_image_count=len(artifact.assets),
        warnings=tuple(warning.message for warning in artifact.warnings),
    )
```

- [ ] **Step 5: 运行提取器测试**

Run: `uv run --locked pytest tests/test_review_ppt_extractor.py tests/test_platform_document_service.py -v`

Expected: PASS；现有底座 PPT 解析测试保持通过。

- [ ] **Step 6: 提交本任务**

```bash
git add app/review/ppt/__init__.py app/review/ppt/models.py app/review/ppt/extractor.py tests/test_review_ppt_extractor.py
git commit -m "feat(review): add independent PPT extraction model"
```

---

### Task 2: PPT 独立确定性规则

**Files:**
- Create: `app/review/ppt/rules.py`
- Test: `tests/test_review_ppt_rules.py`

**Interfaces:**
- Consumes: `PptReviewDocument`
- Produces: `check_ppt_rules(document: PptReviewDocument) -> tuple[PptFinding, ...]`

- [ ] **Step 1: 写规则失败测试**

```python
def test_sequence_rule_detects_skip_inside_one_element():
    document = _document_with_text("1、背景\n2、做法\n4、成效")
    findings = check_ppt_rules(document)
    assert [(item.rule_id, item.target_text) for item in findings] == [
        ("ppt-sequence-skip", "4、"),
    ]

def test_sequence_rule_does_not_join_different_elements_or_decimal_values():
    document = _document_with_elements("1、背景\n2、做法", "4、附录", "增长1.5个百分点")
    assert not [item for item in check_ppt_rules(document) if item.category == "sequence"]

def test_placeholder_quote_and_punctuation_rules_are_independent():
    document = _document_with_text("XX项目已完成。。“阶段目标")
    findings = check_ppt_rules(document)
    assert {item.rule_id for item in findings} == {
        "ppt-placeholder", "ppt-consecutive-punctuation", "ppt-quote-pair",
    }
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run --locked pytest tests/test_review_ppt_rules.py -v`

Expected: FAIL，提示 `check_ppt_rules` 尚不存在。

- [ ] **Step 3: 实现最小确定性规则**

`rules.py` 只使用 `app.review.ppt.models` 和标准库。序号正则分别覆盖阿拉伯数字、带括号数字和中文序号，同一 `PptElement` 内按编号家族独立推进状态：

```python
_ARABIC_RE = re.compile(r"^\s*(\d{1,3})([、.．])")
_PAREN_RE = re.compile(r"^\s*[（(](\d{1,3})[）)]")
_CHINESE_RE = re.compile(r"^\s*([一二三四五六七八九十]{1,3})、")
_PLACEHOLDER_RE = re.compile(r"(?<![A-Za-z])(?:X{2,}|待补充|待填写|待确认)(?![A-Za-z])", re.I)
_CONSECUTIVE_PUNCT_RE = re.compile(r"([，。！？；：、,.!?;:])\1+")

def check_ppt_rules(document: PptReviewDocument) -> tuple[PptFinding, ...]:
    findings: list[PptFinding] = []
    for slide in document.slides:
        for element in slide.elements:
            findings.extend(_check_element_sequence(element))
            findings.extend(_check_placeholders(element))
            findings.extend(_check_quote_pairs(element))
            findings.extend(_check_consecutive_punctuation(element))
    return tuple(_dedupe_rule_findings(findings))
```

序号跳号说明固定为“同一组序号由 N 跳到 M”；不生成改法。占位符只命中明确占位，不把产品名中的单个 `X` 当错误。

- [ ] **Step 4: 运行规则测试**

Run: `uv run --locked pytest tests/test_review_ppt_rules.py -v`

Expected: PASS。

- [ ] **Step 5: 提交本任务**

```bash
git add app/review/ppt/rules.py tests/test_review_ppt_rules.py
git commit -m "feat(review): add independent PPT deterministic rules"
```

---

### Task 3: 模型候选、双边证据与独立审核编排

**Files:**
- Create: `app/review/ppt/evidence.py`
- Create: `app/review/ppt/reviewer.py`
- Create: `app/review/ppt/prompts/language.md`
- Create: `app/review/ppt/prompts/consistency.md`
- Test: `tests/test_review_ppt_reviewer.py`

**Interfaces:**
- Consumes: `PptReviewDocument`、可注入 `PptModelRunner`
- Produces: `review_ppt_document(document, *, model_runner=None) -> PptReviewResult`
- Produces: `review_pptx(path, *, task_dir, model_runner=None) -> PptReviewResult`

- [ ] **Step 1: 写模型和证据失败测试**

测试注入异步 fake runner，不访问真实模型：

```python
async def fake_runner(stage: str, prompt: str) -> dict[str, object]:
    if stage == "language":
        return {"issues": [
            {"category": "grammar", "slide_number": 1,
             "element_id": "slide:1/shape:1", "target_text": "持续不断提升",
             "description": "语义重复，表述不通顺"},
            {"category": "typo", "slide_number": 99,
             "element_id": "slide:99/shape:1", "target_text": "虚构原文",
             "description": "虚构问题"},
        ]}
    return {"issues": [
        {"category": "data_inconsistency",
         "slide_number": 1, "element_id": "slide:1/shape:1", "target_text": "客户100万户",
         "related_slide_number": 3, "related_element_id": "slide:3/shape:2", "related_text": "客户120万户",
         "same_subject": True, "same_time_scope": True, "same_metric_scope": True,
         "description": "同一统计口径的客户数前后不一致"}
    ]}

def test_reviewer_keeps_real_evidence_and_discards_hallucinated_location():
    result = asyncio.run(review_ppt_document(document, model_runner=fake_runner))
    assert {item.target_text for item in result.findings} == {
        "持续不断提升", "客户100万户",
    }
```

补充口径过滤和独立性测试：

```python
def test_cross_slide_candidate_requires_both_exact_sources_and_same_scope():
    candidate = _cross_candidate(same_time_scope=False)
    assert validate_cross_candidate(document, candidate) is None

def test_ppt_package_does_not_import_other_review_engines():
    forbidden = {
        "app.review.general_reviewer", "app.review.reviewer",
        "app.review.halfmonthly_reviewer", "app.review.official_format_checker",
        "app.review.format_checker", "app.review.general_rule_checker",
    }
    assert not (_imported_modules_under(Path("app/review/ppt")) & forbidden)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run --locked pytest tests/test_review_ppt_reviewer.py -v`

Expected: FAIL，提示审核编排和证据函数不存在。

- [ ] **Step 3: 实现证据索引和过滤**

`evidence.py`：

```python
def build_element_index(document: PptReviewDocument) -> dict[tuple[int, str], PptElement]:
    return {
        (element.slide_number, element.element_id): element
        for slide in document.slides
        for element in slide.elements
    }

def validate_local_candidate(document, candidate) -> PptFinding | None:
    element = build_element_index(document).get(
        (candidate.slide_number, candidate.element_id)
    )
    if element is None or candidate.target_text not in element.text:
        return None
    return PptFinding(
        rule_id=f"ppt-{candidate.category}",
        category=candidate.category,
        slide_number=candidate.slide_number,
        element_id=candidate.element_id,
        target_text=candidate.target_text,
        description=candidate.description,
    )

def validate_cross_candidate(document, candidate) -> PptFinding | None:
    if not (
        candidate.same_subject
        and candidate.same_time_scope
        and candidate.same_metric_scope
    ):
        return None
    index = build_element_index(document)
    left = index.get((candidate.slide_number, candidate.element_id))
    right = index.get((candidate.related_slide_number, candidate.related_element_id))
    if left is None or right is None:
        return None
    if candidate.target_text not in left.text or candidate.related_text not in right.text:
        return None
    return PptFinding(
        rule_id=f"ppt-{candidate.category}",
        category=candidate.category,
        slide_number=candidate.slide_number,
        element_id=candidate.element_id,
        target_text=candidate.target_text,
        description=candidate.description,
        related_slide_number=candidate.related_slide_number,
        related_element_id=candidate.related_element_id,
        related_text=candidate.related_text,
    )
```

`dedupe_findings()` 按 `(slide_number, element_id, target_text, related_slide_number, related_text)` 去重；同一位置多个类别时确定性规则优先，其次名称/数据问题，最后语病和标点。

- [ ] **Step 4: 实现独立模型 runner 和审核编排**

`reviewer.py` 不导入其他审核引擎，只复用 `app.review.model_config.build_anthropic_client`：

```python
PptModelRunner = Callable[[str, str], Awaitable[dict[str, object]]]

async def _default_model_runner(stage: str, prompt: str) -> dict[str, object]:
    client, model_name = build_anthropic_client()
    response = await asyncio.to_thread(
        client.messages.create,
        model=model_name,
        max_tokens=4096,
        temperature=0,
        messages=[{"role": "user", "content": prompt}],
    )
    return _parse_json_response(response)

def _parse_json_response(response: object) -> dict[str, object]:
    text = "\n".join(
        str(getattr(block, "text", "") or "")
        for block in getattr(response, "content", ())
    ).strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    payload = json.loads(fenced.group(1) if fenced else text)
    if not isinstance(payload, dict) or not isinstance(payload.get("issues", []), list):
        raise ValueError("PPT审核模型输出格式无效")
    return payload

async def review_ppt_document(
    document: PptReviewDocument,
    *,
    model_runner: PptModelRunner | None = None,
) -> PptReviewResult:
    runner = model_runner or _default_model_runner
    deterministic = check_ppt_rules(document)
    local_candidates = await _review_language_batches(document, runner)
    consistency_complete = True
    try:
        cross_candidates = await _review_consistency(document, runner)
    except Exception:
        cross_candidates = ()
        consistency_complete = False
    findings = dedupe_findings(
        (*deterministic,
         *filter(None, (validate_local_candidate(document, item) for item in local_candidates)),
         *filter(None, (validate_cross_candidate(document, item) for item in cross_candidates)))
    )
    return PptReviewResult(
        filename=document.filename,
        page_count=document.page_count,
        findings=tuple(findings),
        excluded_image_count=document.excluded_image_count,
        warnings=document.warnings,
        consistency_complete=consistency_complete,
    )

async def review_pptx(
    path: Path,
    *,
    task_dir: Path,
    model_runner: PptModelRunner | None = None,
) -> PptReviewResult:
    document = await asyncio.to_thread(
        extract_ppt_document,
        path,
        task_dir=task_dir,
    )
    return await review_ppt_document(document, model_runner=model_runner)
```

语言批次按约 6000 字符组装，标签固定为 `[slide=3 element=slide:3/shape:2 kind=text]`。一致性阶段最多保留 30 条候选，要求双边原文和三个 `same_*` 布尔字段。

- [ ] **Step 5: 写独立提示词**

`language.md` 明确：只检查错别字、语病、标点、名称错写或同一 PPT 内部不一致；不检查版式、图片、备注和外部事实；输出 JSON 不含建议字段。

`consistency.md` 明确：不同年份、对象、范围、单位、累计/当期、目标/实际不能直接判错；每条问题必须带两处页码、对象、真实原文和三个同口径布尔字段；不判断哪一处正确。

- [ ] **Step 6: 运行审核测试**

Run: `uv run --locked pytest tests/test_review_ppt_reviewer.py tests/test_review_ppt_rules.py tests/test_review_ppt_extractor.py -v`

Expected: PASS；测试不发真实网络请求。

- [ ] **Step 7: 提交本任务**

```bash
git add app/review/ppt/evidence.py app/review/ppt/reviewer.py app/review/ppt/prompts tests/test_review_ppt_reviewer.py
git commit -m "feat(review): add independent PPT semantic review"
```

---

### Task 4: 无建议的文字格式与多段持久交付

**Files:**
- Create: `app/review/ppt/formatter.py`
- Modify: `app/review/task_execution.py`
- Test: `tests/test_review_ppt_formatter.py`
- Test: `tests/test_review_task_execution.py`

**Interfaces:**
- Consumes: `PptReviewResult`
- Produces: `format_ppt_review_messages(result, *, max_chars=3500) -> tuple[str, ...]`
- Extends: `PreparedReviewDelivery.multipart_text(parts: Iterable[str])`

- [ ] **Step 1: 写格式和多段交付失败测试**

```python
def test_formatter_returns_facts_without_suggestions():
    messages = format_ppt_review_messages(_result_with_local_and_cross_findings())
    joined = "\n".join(messages)
    assert "【第3页｜语病】" in joined
    assert "【第4页 ↔ 第12页｜数据不一致】" in joined
    assert "原文一：" in joined and "原文二：" in joined
    assert "建议" not in joined
    assert "修改为" not in joined

def test_formatter_splits_long_results_with_continuous_numbers():
    messages = format_ppt_review_messages(_result_with_findings(30), max_chars=600)
    assert len(messages) > 1
    assert "1.【" in messages[0]
    assert "30.【" in messages[-1]
    assert all(len(message) <= 600 for message in messages)
```

任务服务覆盖多段只处理一次：

```python
def test_text_parts_are_sent_in_order_and_not_reprocessed_after_completion(tmp_path):
    sent = []
    async def processor(_workspace):
        return PreparedReviewDelivery.multipart_text(("第一段", "第二段"))
    async def sender(_recipient, text):
        sent.append(text)
        return True
    result = asyncio.run(service.handle(submission.task))
    repeated = asyncio.run(service.handle(repository.get_task(submission.task.task_id)))
    assert result.status == repeated.status == "completed"
    assert sent == ["第一段", "第二段"]
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run --locked pytest tests/test_review_ppt_formatter.py tests/test_review_task_execution.py -v`

Expected: FAIL，提示 formatter 和 `multipart_text` 尚不存在。

- [ ] **Step 3: 实现事实型结果格式化**

```python
def format_ppt_review_messages(
    result: PptReviewResult,
    *,
    max_chars: int = 3500,
) -> tuple[str, ...]:
    boundary = "本次未审核图片文字和演讲者备注。"
    if not result.findings:
        status = "未发现低级文字或内部一致性问题。"
        if not result.consistency_complete:
            status = "已完成文字检查，但全篇一致性检查未完成。"
        return (f"PPT审核完成：{status}\n{boundary}",)
    blocks = [_format_finding(number, item) for number, item in enumerate(result.findings, 1)]
    header = f"PPT审核完成：共发现{len(blocks)}项问题。"
    if not result.consistency_complete:
        header += "\n注意：全篇一致性检查未完成。"
    return _pack_blocks(header, blocks, boundary, max_chars=max_chars)
```

`_format_finding()` 只输出 `原文`/`原文一`/`原文二` 和 `问题`，不输出建议。

- [ ] **Step 4: 为任务服务增加多段文字检查点**

`PreparedReviewDelivery` 增加：

```python
text_parts: tuple[str, ...] = ()

@classmethod
def multipart_text(cls, values: Iterable[str]) -> "PreparedReviewDelivery":
    parts = tuple(value.strip() for value in values if value.strip())
    if not parts:
        raise ValueError("多段文字交付内容不能为空")
    return cls(kind="text_parts", text_parts=parts)
```

`kind` 扩展为 `Literal["text", "text_parts", "attachment"]`。检查点 `result_kind="text_parts"`、`result_text_parts=[...]`；旧 `schema_version=1` 和 `result_kind="text"` 保持兼容。`_deliver()` 依次调用现有 `_text_sender`，任一段失败则整次交付失败；进入 `sending` 后中断仍按发送状态不确定处理，禁止自动重发。

- [ ] **Step 5: 运行格式和任务测试**

Run: `uv run --locked pytest tests/test_review_ppt_formatter.py tests/test_review_task_execution.py -v`

Expected: PASS；现有单段文字和附件交付测试保持通过。

- [ ] **Step 6: 提交本任务**

```bash
git add app/review/ppt/formatter.py app/review/task_execution.py tests/test_review_ppt_formatter.py tests/test_review_task_execution.py
git commit -m "feat(review): add multipart PPT text delivery"
```

---

### Task 5: PPT 专用任务与审核 Bot 入口

**Files:**
- Modify: `app/review/task_execution.py`
- Modify: `app/review/main.py`
- Modify: `app/review/ppt/__init__.py`
- Create: `tests/test_review_ppt_bot.py`
- Modify: `tests/test_review_bot.py`
- Modify: `tests/test_review_task_execution.py`

**Interfaces:**
- Produces: `PPT_REVIEW_TASK_TYPE = "review_pptx"`
- Produces: `is_pptx_filename(filename) -> bool`
- Integrates: `_process_queued_single_review()` -> `review_pptx()` -> `PreparedReviewDelivery.multipart_text()`

- [ ] **Step 1: 写任务类型和入口失败测试**

```python
def test_ppt_submission_freezes_single_pptx_outside_sqlite_payload(tmp_path):
    submission = service.submit_file(
        channel="wecom", sender_userid="user-1", sender_name="User One",
        message_id="ppt-message-001", task_type=PPT_REVIEW_TASK_TYPE,
        filename="经营汇报.pptx", file_bytes=b"pptx-secret-body",
    )
    assert submission.task.payload["input_kind"] == "pptx"
    assert b"pptx-secret-body" not in db_path.read_bytes()
    assert Path(submission.task.payload["task_dir"]).joinpath(
        submission.task.payload["input_file"]
    ).suffix == ".pptx"

def test_review_bot_accepts_pptx_without_entering_docx_intake():
    assert is_supported_review_filename("经营汇报.pptx") is True
    assert is_pptx_filename("经营汇报.pptx") is True
```

处理分支用 mock 独立审核函数，确认只返回文字：

```python
def test_queued_ppt_review_uses_independent_processor_and_returns_text_parts(monkeypatch):
    monkeypatch.setattr("app.review.ppt.review_pptx", fake_review_pptx)
    delivery = asyncio.run(_process_queued_single_review(workspace, config=config, neican_rules_text=""))
    assert delivery.kind == "text_parts"
    assert delivery.file_path is None
    assert all("建议" not in part for part in delivery.text_parts)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run --locked pytest tests/test_review_ppt_bot.py tests/test_review_task_execution.py tests/test_review_bot.py -v`

Expected: FAIL，提示 PPT 任务类型和入口尚不存在。

- [ ] **Step 3: 登记 PPT 专用任务**

`task_execution.py` 增加：

```python
PPT_REVIEW_TASK_TYPE = "review_pptx"
ReviewInputKind = Literal["docx", "text", "html", "pptx"]
_FILE_INPUT_SPEC[PPT_REVIEW_TASK_TYPE] = ("pptx", frozenset({".pptx"}))
_DOCUMENT_TYPE_BY_TASK_TYPE[PPT_REVIEW_TASK_TYPE] = "ppt"
```

把 PPT 类型加入 `REVIEW_FILE_TASK_TYPES`、`REVIEW_TASK_TYPES`、`_safe_input_name()` 和 `__all__`。不把 PPT 映射为 `DocumentType.GENERAL`。

- [ ] **Step 4: 在队列处理器中增加独立分支**

`_process_queued_single_review()` 在任何 `DocumentType` 映射和 `.docx` 解析之前处理 PPT：

```python
if workspace.task_type == PPT_REVIEW_TASK_TYPE:
    from app.review.ppt import format_ppt_review_messages, review_pptx
    result = await review_pptx(workspace.input_file, task_dir=workspace.task_dir)
    output_dir = workspace.task_dir / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    result_path = output_dir / "result.json"
    temporary = output_dir / f".result.{uuid4().hex}.tmp"
    temporary.write_text(
        json.dumps(result.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(result_path)
    return PreparedReviewDelivery.multipart_text(
        format_ppt_review_messages(result)
    )
```

PPT 分支不调用 `load_rules()`、`review_general()`、`_parse_docx()`、`build_user_review_reply()` 或 Word 标注函数。

- [ ] **Step 5: 在企业微信文件入口直接分流 PPTX**

`main.py` 增加 `is_pptx_filename()`，把 `.pptx` 加入支持和拒接话术。下载和大小校验后、HTML 与 DOCX intake 之前处理：

```python
if is_pptx_filename(filename):
    if pending_mode in {"format", "multi"}:
        await ws_client.reply_stream(
            frame, generate_req_id("review-reject"),
            "PPT仅支持单文件低级错误审核，不参与公文格式或多文件联合审核。", True,
        )
        return
    submission = review_tasks.submit_file(
        channel="wecom", sender_userid=sender, sender_name=english_name,
        message_id=extract_message_id(frame), task_type=PPT_REVIEW_TASK_TYPE,
        filename=filename, file_bytes=buffer,
    )
    await _reply_queued_review_acceptance(
        ws_client, frame, generate_req_id("review-ppt-queued"),
        review_label="PPT低级错误审核", created=submission.created,
        input_label="这份PPT", acknowledgment_already_sent=True,
    )
    return
```

`.ppt` 拒接话术明确提示另存为 `.pptx`。

- [ ] **Step 6: 运行入口和持久任务测试**

Run: `uv run --locked pytest tests/test_review_ppt_bot.py tests/test_review_task_execution.py tests/test_review_bot.py -v`

Expected: PASS；PPT 重复消息不重复建任务，已有 Word/文字/HTML 分流保持通过。

- [ ] **Step 7: 提交本任务**

```bash
git add app/review/main.py app/review/task_execution.py app/review/ppt/__init__.py tests/test_review_ppt_bot.py tests/test_review_task_execution.py tests/test_review_bot.py
git commit -m "feat(review): route PPTX through persistent review queue"
```

---

### Task 6: 核心文档、路线状态与完整回归

**Files:**
- Modify: `app/review/README.md`
- Modify: `docs/development/README.md`
- Modify: `docs/development/architecture.md`
- Modify: `docs/development/TODO.md`
- Modify: `docs/agent-platform/README.md`
- Modify: `docs/capabilities/README.md`
- Modify: `docs/development/testing-and-delivery.md`
- Modify: `app/config.example.env` only if implementation adds configuration

**Interfaces:**
- Documents the exact implemented behavior and remaining real-PPT quality gate.

- [ ] **Step 1: 修正路线状态和能力说明**

把 `TODO-020` 从错误的“已完成”修正为“进行中”。记录第一阶段代码范围：单 PPTX、可编辑文字/表格/图表、内部一致性、纯文字结果、无建议、不审图片和备注；第二阶段跨 PPT 保持未开始。

`app/review/README.md` 增加用户流程、结果示例、独立性、边界和测试命令。其他核心文档只写已经实现并有测试证明的行为，不把用户尚未完成的真实 PPT 验收写成已完成。

- [ ] **Step 2: 运行 PPT 专项回归**

Run:

```bash
uv run --locked pytest \
  tests/test_review_ppt_extractor.py \
  tests/test_review_ppt_rules.py \
  tests/test_review_ppt_reviewer.py \
  tests/test_review_ppt_formatter.py \
  tests/test_review_ppt_bot.py \
  tests/test_review_task_execution.py \
  tests/test_platform_document_service.py \
  tests/test_review_bot.py -v
```

Expected: PASS。

- [ ] **Step 3: 运行审核模块回归**

Run:

```bash
uv run --locked pytest \
  tests/test_review_general.py \
  tests/test_review_general_rules.py \
  tests/test_review_halfmonthly.py \
  tests/test_official_format_review.py \
  tests/test_review_intake.py \
  tests/test_review_multi_file.py \
  tests/test_review_main_flow_optimization.py -v
uv run --locked python tests/test_review_bot.py
```

Expected: PASS。

- [ ] **Step 4: 运行全仓离线回归和文档检查**

Run:

```bash
uv run --locked pytest -q --ignore=tests/test_reviewer.py
uv run --locked python scripts/project_docs.py check
```

Expected: 全部离线测试 PASS；核心文档检查通过。真实模型测试如因网络失败，单独说明，不归因于代码。

- [ ] **Step 5: 检查交付范围**

Run:

```bash
git status --short
git diff --check
git diff --stat
```

Expected: 不包含 `.env`、真实 PPT、`M-Agent-Files/`、缓存、临时任务目录或本机绝对路径；并明确区分当前工作区中其他任务的变更。

- [ ] **Step 6: 提交文档和最终回归节点**

```bash
git add app/review/README.md docs/development/README.md docs/development/architecture.md docs/development/TODO.md docs/agent-platform/README.md docs/capabilities/README.md docs/development/testing-and-delivery.md
git commit -m "docs(review): document PPT low-error review phase one"
```

- [ ] **Step 7: 受管推送并核对远端同步**

在确认没有夹带其他任务提交后运行：

```bash
uv run --locked python scripts/project_docs.py push \
  --summary "完成PPT低级错误审核第一阶段" \
  --impact "审核Bot可独立审核单个PPTX的可编辑文字、序号和内部数据一致性，并通过企业微信返回无建议的问题清单" \
  --next-step "由用户使用经授权真实PPT测试误报、漏报和页码定位，再建设跨PPT审核"
uv run --locked python scripts/project_docs.py check-sync
```

Expected: 受管推送成功，`check-sync` 显示本地与远端同步；如果当前主分支包含其他任务的未推送提交，先报告并等待对应任务收口，不混合推送。
