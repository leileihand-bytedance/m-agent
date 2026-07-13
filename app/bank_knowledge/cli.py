from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.bank_knowledge.ingest import import_folder
from app.bank_knowledge.store import BankKnowledgeStore
from app.platform.config import ROOT


DEFAULT_DB_PATH = ROOT / "data/bank_knowledge/bank.sqlite3"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="微众银行信息库导入和检索工具")
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH), help="SQLite 数据库路径")
    subparsers = parser.add_subparsers(dest="command", required=True)

    import_parser = subparsers.add_parser("import-folder", help="导入一个本地信息库文件夹")
    import_parser.add_argument("folder", help="包含 Word/PDF/TXT 的文件夹")

    search_parser = subparsers.add_parser("search", help="检索信息库")
    search_parser.add_argument("query", help="检索关键词")
    search_parser.add_argument("--limit", type=int, default=5)

    args = parser.parse_args(argv)
    db_path = Path(args.db)
    if args.command == "import-folder":
        result = import_folder(Path(args.folder), db_path=db_path)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if args.command == "search":
        store = BankKnowledgeStore(db_path)
        results = store.search(args.query, limit=args.limit)
        print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
