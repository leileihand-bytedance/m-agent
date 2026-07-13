from __future__ import annotations

import argparse
from datetime import date

from app.platform.config import load_config
from app.policy_knowledge.govcn import fetch_govcn_policy_documents
from app.policy_knowledge.nfra import fetch_recent_nfra_documents
from app.policy_knowledge.store import PolicyKnowledgeStore


def main() -> None:
    parser = argparse.ArgumentParser(description="M-Agent policy knowledge maintenance")
    subparsers = parser.add_subparsers(dest="command", required=True)

    update_all = subparsers.add_parser("update", help="按周更新全部政策知识库来源")
    update_all.add_argument("--days", type=int, default=92, help="金融监管总局抓取最近多少天，默认约 3 个月")
    update_all.add_argument("--nfra-max-pages", type=int, default=8, help="金融监管总局每个栏目最多抓取多少页")
    update_all.add_argument("--govcn-max-pages", type=int, default=1, help="国务院每个主题词最多抓取多少页")
    update_all.add_argument("--govcn-page-size", type=int, default=10, help="国务院每页数量")

    update = subparsers.add_parser("update-nfra", help="抓取金融监管总局最近政策资料")
    update.add_argument("--days", type=int, default=92, help="抓取最近多少天，默认约 3 个月")
    update.add_argument("--max-pages", type=int, default=8, help="每个栏目最多抓取多少页")

    update_govcn = subparsers.add_parser("update-govcn", help="抓取国务院政策文件库重点政策原文")
    update_govcn.add_argument("--max-pages", type=int, default=1, help="每个主题词最多抓取多少页，默认 1 页")
    update_govcn.add_argument("--page-size", type=int, default=10, help="每页数量，默认 10 条")

    search = subparsers.add_parser("search", help="检索本地政策知识库")
    search.add_argument("query")
    search.add_argument("--limit", type=int, default=5)
    search.add_argument("--category", choices=["policy_original", "policy_interpretation", "regulatory_update"])

    args = parser.parse_args()
    config = load_config()
    store = PolicyKnowledgeStore(config.policy_db_path)

    if args.command == "update":
        errors: list[str] = []
        nfra_count = 0
        govcn_count = 0
        try:
            nfra_documents = fetch_recent_nfra_documents(
                today=date.today(),
                days=args.days,
                max_pages=args.nfra_max_pages,
            )
            nfra_count = store.upsert_documents(nfra_documents)
        except Exception as exc:
            errors.append(f"金融监管总局更新失败：{type(exc).__name__}: {exc}")

        try:
            govcn_documents = fetch_govcn_policy_documents(
                max_pages=args.govcn_max_pages,
                page_size=args.govcn_page_size,
            )
            govcn_count = store.upsert_documents(govcn_documents)
        except Exception as exc:
            errors.append(f"国务院政策文件库更新失败：{type(exc).__name__}: {exc}")

        print(
            f"已更新 {nfra_count} 条监管政策资料、{govcn_count} 条国务院政策资料，"
            f"当前库内共 {store.count_documents()} 条。"
        )
        for error in errors:
            print(f"警告：{error}")
        return

    if args.command == "update-nfra":
        documents = fetch_recent_nfra_documents(today=date.today(), days=args.days, max_pages=args.max_pages)
        count = store.upsert_documents(documents)
        print(f"已更新 {count} 条监管政策资料，当前库内共 {store.count_documents()} 条。")
        return

    if args.command == "update-govcn":
        documents = fetch_govcn_policy_documents(max_pages=args.max_pages, page_size=args.page_size)
        count = store.upsert_documents(documents)
        print(f"已更新 {count} 条国务院政策资料，当前库内共 {store.count_documents()} 条。")
        return

    if args.command == "search":
        for result in store.search(args.query, limit=args.limit, category=args.category):
            print(f"- {result['publish_date']} {result['title']}")
            print(f"  {result['url']}")
            print(f"  {result['source']} / {result['category']}")
            print(f"  {result['snippet']}")
        return


if __name__ == "__main__":
    main()
