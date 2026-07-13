import re

from skills.direct_report.schema import DirectReportViolation


_TITLE_LEADING_PUNCTUATION = re.compile(r'''^[《“"'\[（(]+''')
_BODY_LENGTH_MIN = 700
_BODY_LENGTH_MAX = 850

_SUBHEADING_PATTERNS = (
    re.compile(r"[\n\r]+(?:\s*)(?:一|二|三|四|五|六|七|八|九|十)[是、].+"),
    re.compile(r"[\n\r]+(?:\s*)(?:第一|第二|第三|第四|第五).+"),
    re.compile(r"[\n\r]+(?:\s*)\d+[\.、)）].+"),
    re.compile(r"[\n\r]+(?:\s*)[\(（][一二三四五六七八九十\d]+[\)）].+"),
    re.compile(r"[\n\r]+(?:\s*)##\s*.+"),
    re.compile(r"[\n\r]+(?:\s*)\*\*.+\*\*[:：]"),
)

_SINGLE_ENTERPRISE_CASE_RE = re.compile(r"(一家|某家|该|某)(?:[^\n，。；]{0,12})?(企业|公司|商户|工厂)")
_CASE_ACTION_RE = re.compile(r"(获批|授信|贷款|放款|融资|备货|周转|资金)")
_BROADER_SIGNAL_RE = re.compile(
    r"(模式|机制|批量|场景|体系|平台|名单制|担保|风险分担|共担|政策|落地|上线|推广|推出|产品|服务|机制|准入|风控|数据|累计|已服务|覆盖|支持)"
)
_BATCH_METRIC_RE = re.compile(r"(累计|已服务|覆盖|支持)\D{0,10}\d+(?:余|多|余家|多家)?(?:家|户|笔|万元|亿元|人次)")
_DIRECT_REPORT_FORBIDDEN_SUBJECT_NAMES = (
    "深圳前海微众银行",
    "深圳市微众银行股份有限公司",
    "深圳市的微众银行股份有限公司",
    "微众银行股份有限公司",
    "我行",
)


def validate_deterministic(title: str, body: str) -> list[DirectReportViolation]:
    """对直报标题和正文做确定性规则校验。"""
    violations: list[DirectReportViolation] = []
    violations.extend(check_title_format(title))
    violations.extend(check_body_length(body))
    violations.extend(check_no_subheadings(body))
    violations.extend(check_no_standalone_case_paragraph(body))
    violations.extend(check_direct_report_subject_name(body))
    return violations


def check_title_format(title: str) -> list[DirectReportViolation]:
    """标题必须包含“微众银行”。"""
    stripped = _TITLE_LEADING_PUNCTUATION.sub("", title.strip())
    if "微众银行" not in stripped:
        return [
            DirectReportViolation(
                rule="title-format",
                severity="hard",
                message="标题未包含“微众银行”。",
                suggestion='标题应体现“微众银行 + 核心举措/机制 + 结果/作用”，例如“微众银行推出某机制服务某群体”。',
            )
        ]
    if _has_space_split_title(stripped):
        return [
            DirectReportViolation(
                rule="title-format",
                severity="hard",
                message="标题存在用空格硬连接的两段式结构。",
                suggestion="如果标题分成两层意思，中间应用中文逗号或冒号连接，不要用空格断开。",
            )
        ]
    return []


def check_body_length(body: str) -> list[DirectReportViolation]:
    """正文篇幅应在 650–850 字之间（目标 700–800 字，留一定容差）。"""
    length = len(body)
    if length < _BODY_LENGTH_MIN:
        return [
            DirectReportViolation(
                rule="body-length",
                severity="hard",
                message=f"正文过短（{length} 字），未达到 700 字下限。",
                suggestion="补充背景、机制、成效或下一步安排，使正文不少于 700 字。",
            )
        ]
    if length > _BODY_LENGTH_MAX:
        return [
            DirectReportViolation(
                rule="body-length",
                severity="hard",
                message=f"正文过长（{length} 字），超过 850 字上限。",
                suggestion="删去重复表述、宣传性形容词和次要细节，保留主线事实。",
            )
        ]
    return []


def check_no_subheadings(body: str) -> list[DirectReportViolation]:
    """正文中不得出现小标题或“一是/二是/三是”等分点结构。"""
    for pattern in _SUBHEADING_PATTERNS:
        if pattern.search(body):
            return [
                DirectReportViolation(
                    rule="no-subheadings",
                    severity="hard",
                    message="正文出现分点或小标题结构。",
                    suggestion="删除序号、小标题和“一是/二是”结构，改为围绕一条主线的连贯叙述。",
                )
            ]
    return []


def check_no_standalone_case_paragraph(body: str) -> list[DirectReportViolation]:
    """单个企业受益个案不得单独成段；只能作为主体段中的一句辅助例证。"""
    paragraphs = [p.strip() for p in body.split("\n") if p.strip()]
    if not paragraphs:
        return []

    for paragraph in paragraphs:
        if _is_standalone_case_paragraph(paragraph):
            return [
                DirectReportViolation(
                    rule="no-standalone-case-paragraph",
                    severity="hard",
                    message="单个企业受益个案单独成段。",
                    suggestion="把该企业个案压缩到主体段中的一句辅助例证，段落主线应回到微众银行的机制、批量成效或模式创新。",
                )
            ]
    return []


def check_direct_report_subject_name(body: str) -> list[DirectReportViolation]:
    """直报中主体直接写“微众银行”，不使用全称或简称引入。"""
    for name in _DIRECT_REPORT_FORBIDDEN_SUBJECT_NAMES:
        if name in body:
            return [
                DirectReportViolation(
                    rule="direct-report-subject-name",
                    severity="hard",
                    message=f"直报中出现了不应使用的主体名称“{name}”。",
                    suggestion="直报正文中全部直接使用“微众银行”，不要写全称，也不要写“以下简称”。",
                )
            ]

    if "以下简称" in body:
        return [
            DirectReportViolation(
                rule="direct-report-subject-name",
                severity="hard",
                message="直报中出现了“以下简称”简称引入。",
                suggestion="直报正文中全部直接使用“微众银行”，不要写全称，也不要写“以下简称”。",
            )
        ]
    return []


def _is_standalone_case_paragraph(paragraph: str) -> bool:
    """判断一个段落是否为单独成段的企业个案。"""
    if len(paragraph) < 20:
        return False

    sentences = _split_sentences(paragraph)
    if not sentences:
        return False

    case_sentences = [s for s in sentences if _is_single_enterprise_case_sentence(s)]
    if not case_sentences:
        return False

    # 如果段落中同时存在批量/机制信号，说明不只是个案，不判违规
    for sentence in sentences:
        if _BROADER_SIGNAL_RE.search(sentence) or _BATCH_METRIC_RE.search(sentence):
            return False

    # 若个案句占段落主体（超过一半句子或段落明显围绕个案展开），判为单独成段
    if len(case_sentences) >= max(1, len(sentences) // 2):
        return True

    return False


def _split_sentences(text: str) -> list[str]:
    return [chunk.strip() for chunk in re.split(r"[。！？；\n]", text) if chunk.strip()]


def _is_single_enterprise_case_sentence(sentence: str) -> bool:
    if not _SINGLE_ENTERPRISE_CASE_RE.search(sentence):
        return False
    return bool(_CASE_ACTION_RE.search(sentence))


def _has_space_split_title(title: str) -> bool:
    if "，" in title or "：" in title:
        return False
    parts = [part for part in title.split() if part]
    if len(parts) < 2:
        return False
    chinese_parts = sum(1 for part in parts if re.search(r"[\u4e00-\u9fff]", part))
    return chinese_parts >= 2
