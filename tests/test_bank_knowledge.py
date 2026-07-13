from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.bank_knowledge.ingest import build_entries_from_text, import_folder
from app.bank_knowledge.materials import build_bank_materials
from app.bank_knowledge.store import BankKnowledgeStore


def test_bank_knowledge_store_searches_by_product_and_theme(tmp_path):
    store = BankKnowledgeStore(tmp_path / "bank.sqlite3")
    store.replace_source_entries(
        "sample.docx",
        [
            {
                "entry_id": "e1",
                "source_file": "sample.docx",
                "source_type": "docx",
                "section": "微业贷",
                "title": "微业贷服务小微企业",
                "text": "微业贷累计申请企业法人客户超过760万，累计授信客户超过180万，累计贷款超过3万亿元。",
                "themes": ["small_micro", "inclusive_finance"],
                "entity_type": "product",
                "usage_type": "writing_material",
                "source_page": "",
                "metadata": {},
            }
        ],
    )

    results = store.search("小微企业 微业贷 普惠金融", limit=3)

    assert len(results) == 1
    assert results[0]["title"] == "微业贷服务小微企业"
    assert results[0]["themes"] == ["small_micro", "inclusive_finance"]
    assert "760万" in results[0]["snippet"]


def test_build_entries_from_text_tags_bank_themes():
    entries = build_entries_from_text(
        source_file="微众银行数字化企业金融服务情况介绍.docx",
        source_type="docx",
        sections=[
            (
                "",
                "微业贷通过数字化方式提升小微企业融资服务效率，累计申请企业法人客户超过760万。"
                "微众银行探索人工智能、大模型和智能体在金融服务中的应用。",
            )
        ],
    )

    assert len(entries) == 1
    assert "small_micro" in entries[0]["themes"]
    assert "ai_finance" in entries[0]["themes"]
    assert entries[0]["entity_type"] in {"product", "capability"}


def test_import_folder_imports_supported_documents(tmp_path):
    source_dir = tmp_path / "bank-source"
    source_dir.mkdir()
    (source_dir / "sample.txt").write_text(
        "微众银行是国内首家民营银行和数字银行。\n"
        "微业贷累计申请企业法人客户超过760万，持续服务小微企业融资需求。",
        encoding="utf-8",
    )

    imported = import_folder(source_dir, db_path=tmp_path / "bank.sqlite3")
    store = BankKnowledgeStore(tmp_path / "bank.sqlite3")

    assert imported["files"] == 1
    assert imported["entries"] >= 1
    assert store.count_entries() >= 1


def test_build_bank_materials_returns_relevant_writing_pack(tmp_path):
    store = BankKnowledgeStore(tmp_path / "bank.sqlite3")
    store.replace_source_entries(
        "sample.docx",
        [
            {
                "entry_id": "e1",
                "source_file": "sample.docx",
                "source_type": "docx",
                "section": "微业贷",
                "title": "微业贷服务小微企业",
                "text": "微业贷是线上无抵押企业流动资金贷款产品，累计申请企业法人客户超过760万。",
                "themes": ["small_micro", "inclusive_finance"],
                "entity_type": "product",
                "usage_type": "writing_material",
                "source_page": "",
                "metadata": {},
            }
        ],
    )

    materials = build_bank_materials(
        user_instruction="写简报：微众银行提升小微企业融资服务效率",
        materials=[{"title": "用户素材", "text": "微众银行扩大普惠金融覆盖面，服务小微企业融资。"}],
        db_path=tmp_path / "bank.sqlite3",
    )

    assert len(materials) == 1
    assert materials[0]["source"] == "bank_knowledge"
    assert materials[0]["url"] == "bank://e1"
    assert "微业贷" in materials[0]["text"]
    assert "来源文件：sample.docx" in materials[0]["text"]
