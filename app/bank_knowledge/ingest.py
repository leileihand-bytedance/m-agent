from __future__ import annotations

import hashlib
import re
import subprocess
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

from app.bank_knowledge.store import BankKnowledgeStore


SUPPORTED_SUFFIXES = {".docx", ".doc", ".pdf", ".txt", ".md"}

THEME_TERMS: dict[str, tuple[str, ...]] = {
    "profile": ("微众银行", "深圳前海微众银行", "民营银行", "数字银行", "普惠大众"),
    "small_micro": ("小微企业", "小微", "微业贷", "企业法人", "首贷", "融资服务"),
    "inclusive_finance": ("普惠金融", "普惠", "金融服务可得性", "长尾客群"),
    "digital_finance": ("数字化", "数字金融", "线上", "移动端", "金融科技", "远程"),
    "tech_finance": ("科技金融", "科创", "高新技术企业", "种子贷", "战新未来产业贷", "科技创新专项担保"),
    "ai_finance": ("人工智能", "大模型", "智能体", "AI", "数字员工"),
    "foreign_trade": ("外贸", "微贸贷", "稳外贸", "出口", "信保"),
    "consumption": ("国补商户", "促消费", "消费", "以旧换新", "家电", "3C", "新能源汽车"),
    "consumer_protection": ("消费者权益", "消保", "投诉", "适当性", "金融消费者"),
    "anti_fraud": ("反诈", "电信网络诈骗", "诈骗", "欺诈", "账户风险"),
    "accessibility": ("无障碍", "听障", "视障", "适老", "养老"),
    "green_finance": ("绿色金融", "绿色贷款", "绿色低碳", "新能源"),
    "esg": ("ESG", "社会责任", "可持续发展"),
    "financial_metric": ("营收", "净利润", "总资产", "不良贷款率", "资本充足率"),
    "honor": ("荣誉", "获奖", "入选", "案例", "排名"),
}


def import_folder(source_dir: str | Path, *, db_path: str | Path) -> dict[str, int]:
    source_path = Path(source_dir)
    if not source_path.exists() or not source_path.is_dir():
        raise FileNotFoundError(f"信息库文件夹不存在：{source_path}")

    store = BankKnowledgeStore(db_path)
    imported_files = 0
    imported_entries = 0
    for file_path in sorted(source_path.iterdir()):
        if file_path.name.startswith(".") or file_path.suffix.lower() not in SUPPORTED_SUFFIXES:
            continue
        sections = extract_sections(file_path)
        entries = build_entries_from_text(
            source_file=file_path.name,
            source_type=file_path.suffix.lower().lstrip("."),
            sections=sections,
        )
        store.replace_source_entries(file_path.name, entries)
        imported_files += 1
        imported_entries += len(entries)

    return {"files": imported_files, "entries": imported_entries}


def extract_sections(path: Path) -> list[tuple[str, str]]:
    suffix = path.suffix.lower()
    if suffix == ".docx":
        return [("", _extract_docx_text(path))]
    if suffix == ".doc":
        return [("", _extract_doc_text(path))]
    if suffix == ".pdf":
        return _extract_pdf_sections(path)
    if suffix in {".txt", ".md"}:
        return [("", path.read_text(encoding="utf-8"))]
    raise ValueError(f"暂不支持的文件类型：{path.suffix}")


def build_entries_from_text(
    *,
    source_file: str,
    source_type: str,
    sections: list[tuple[str, str]],
) -> list[dict[str, object]]:
    entries: list[dict[str, object]] = []
    for section, text in sections:
        for idx, chunk in enumerate(_chunk_text(text), 1):
            themes = _infer_themes(chunk)
            if not themes:
                continue
            title = _entry_title(source_file=source_file, section=section, text=chunk)
            entry_id = _entry_id(source_file=source_file, section=section, idx=idx, text=chunk)
            entries.append(
                {
                    "entry_id": entry_id,
                    "source_file": source_file,
                    "source_type": source_type,
                    "section": section,
                    "title": title,
                    "text": chunk,
                    "themes": themes,
                    "entity_type": _infer_entity_type(chunk, themes),
                    "usage_type": "writing_material",
                    "source_page": _source_page(section),
                    "metadata": {"chunk_index": idx},
                }
            )
    return entries


def _extract_docx_text(path: Path) -> str:
    with zipfile.ZipFile(path) as archive:
        xml_text = archive.read("word/document.xml").decode("utf-8")
    root = ET.fromstring(xml_text)
    namespace = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    paragraphs: list[str] = []
    for paragraph in root.findall(".//w:p", namespace):
        texts = [node.text or "" for node in paragraph.findall(".//w:t", namespace)]
        line = "".join(texts).strip()
        if line:
            paragraphs.append(line)
    return "\n".join(paragraphs)


def _extract_doc_text(path: Path) -> str:
    try:
        completed = subprocess.run(
            ["textutil", "-convert", "txt", "-stdout", str(path)],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"无法读取 Word .doc 文件：{path.name}") from exc
    return completed.stdout


def _extract_pdf_sections(path: Path) -> list[tuple[str, str]]:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise RuntimeError("缺少 pypdf，无法读取 PDF。请使用项目运行环境或安装 pypdf。") from exc

    reader = PdfReader(str(path))
    sections: list[tuple[str, str]] = []
    for index, page in enumerate(reader.pages, 1):
        text = (page.extract_text() or "").strip()
        if text:
            sections.append((f"第{index}页", text))
    return sections


def _chunk_text(text: str, *, max_chars: int = 900) -> list[str]:
    paragraphs = [part.strip() for part in re.split(r"\n+", text.replace("\u3000", " ")) if part.strip()]
    chunks: list[str] = []
    current = ""
    for paragraph in paragraphs:
        if len(paragraph) > max_chars:
            if current:
                chunks.append(current)
                current = ""
            chunks.extend(_split_long_paragraph(paragraph, max_chars=max_chars))
            continue
        candidate = f"{current}\n{paragraph}".strip() if current else paragraph
        if len(candidate) > max_chars and current:
            chunks.append(current)
            current = paragraph
        else:
            current = candidate
    if current:
        chunks.append(current)
    return [_normalize_text(chunk) for chunk in chunks if len(_normalize_text(chunk)) >= 20]


def _split_long_paragraph(paragraph: str, *, max_chars: int) -> list[str]:
    sentences = [part for part in re.split(r"(?<=[。！？；;])", paragraph) if part.strip()]
    chunks: list[str] = []
    current = ""
    for sentence in sentences:
        candidate = f"{current}{sentence}".strip()
        if len(candidate) > max_chars and current:
            chunks.append(current)
            current = sentence.strip()
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def _infer_themes(text: str) -> list[str]:
    return [theme for theme, terms in THEME_TERMS.items() if any(term in text for term in terms)]


def _infer_entity_type(text: str, themes: list[str]) -> str:
    if any(product in text for product in ("微业贷", "微粒贷", "微贸贷", "国补商户专享贷款")):
        return "product"
    if "financial_metric" in themes or re.search(r"(超过|累计|达到|增长|亿元|万户|万人次|%)", text):
        return "metric"
    if "honor" in themes:
        return "honor"
    if any(theme in themes for theme in ("digital_finance", "tech_finance", "ai_finance")):
        return "capability"
    if "profile" in themes:
        return "standard_expression"
    return "case"


def _entry_title(*, source_file: str, section: str, text: str) -> str:
    first_sentence = re.split(r"[。！？\n]", text.strip(), maxsplit=1)[0].strip()
    if len(first_sentence) > 32:
        first_sentence = first_sentence[:32]
    if section:
        return f"{section}：{first_sentence}"
    return first_sentence or Path(source_file).stem


def _entry_id(*, source_file: str, section: str, idx: int, text: str) -> str:
    raw = f"{source_file}\n{section}\n{idx}\n{text}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _source_page(section: str) -> str:
    return section if section.startswith("第") and section.endswith("页") else ""


def _normalize_text(text: str) -> str:
    return re.sub(r"[ \t]+", " ", text.replace("\u3000", " ")).strip()
