from datetime import date
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.policy_knowledge.nfra import NfraClient, fetch_recent_nfra_documents


def test_fetch_recent_nfra_documents_fetches_policy_originals_from_interpretation_links():
    calls = []

    def requester(url):
        calls.append(url)
        if "SelectDocByItemIdAndChild" in url and "itemId=917" in url:
            return {
                "rptCode": 200,
                "data": {
                    "rows": [
                        {
                            "docId": 1001,
                            "docSubtitle": "人工智能指导意见答记者问",
                            "publishDate": "2026-06-18 18:47:44",
                            "generaltype": "0",
                        },
                        {
                            "docId": 9001,
                            "docSubtitle": "过期政策解读",
                            "publishDate": "2026-02-01 10:00:00",
                            "generaltype": "0",
                        },
                    ]
                },
            }
        if "SelectDocByItemIdAndChild" in url and "itemId=915" in url:
            return {
                "rptCode": 200,
                "data": {
                    "rows": [
                        {
                            "docId": 2001,
                            "docSubtitle": "金融监管总局赴广东调研",
                            "publishDate": "2026-06-29 16:49:35",
                            "generaltype": "0",
                        }
                    ]
                },
            }
        if "docId=1001" in url:
            return {
                "rptCode": 200,
                "data": {
                    "docId": 1001,
                    "docTitle": "人工智能指导意见答记者问",
                    "publishDate": "2026-06-18 18:47:44",
                    "docClob": """
                    <html><body>
                      <script>ignore()</script>
                      <p>一、政策背景。</p>
                      <p>附：<a href="https://www.nfra.gov.cn/cn/view/pages/governmentDetail.html?docId=3001&itemId=&generaltype=1">原文</a></p>
                    </body></html>
                    """,
                },
            }
        if "docId=3001" in url:
            return {
                "rptCode": 200,
                "data": {
                    "docId": 3001,
                    "docTitle": "国家金融监督管理总局关于银行业保险业人工智能安全开发应用的指导意见",
                    "publishDate": "2026-06-18 18:48:00",
                    "generaltype": "1",
                    "docClob": """
                    <html><body>
                      <p>第一条 金融机构开发应用人工智能应坚持谁使用谁负责。</p>
                      <p>第二条 加强人工智能风险分类分级管理。</p>
                    </body></html>
                    """,
                },
            }
        if "docId=2001" in url:
            return {
                "rptCode": 200,
                "data": {
                    "docId": 2001,
                    "docTitle": "金融监管总局赴广东调研",
                    "publishDate": "2026-06-29 16:49:35",
                    "docClob": "<html><body><p>部署金融风险防控和严监管强监管工作。</p></body></html>",
                },
            }
        raise AssertionError(f"unexpected url: {url}")

    docs = fetch_recent_nfra_documents(
        client=NfraClient(requester=requester),
        today=date(2026, 7, 3),
        days=92,
        max_pages=1,
    )

    assert [doc["doc_id"] for doc in docs] == ["1001", "3001", "2001"]
    assert docs[0]["category"] == "policy_interpretation"
    assert docs[0]["text"] == "一、政策背景。\n附： 原文"
    assert docs[0]["original_links"][0]["doc_id"] == "3001"
    assert docs[1]["category"] == "policy_original"
    assert docs[1]["metadata"]["linked_from_doc_id"] == "1001"
    assert "谁使用谁负责" in docs[1]["text"]
    assert docs[2]["category"] == "regulatory_update"
    assert "风险防控" in docs[2]["text"]
    assert not any("docId=9001" in call for call in calls)


def test_fetch_recent_nfra_documents_continues_when_original_fetch_fails():
    def requester(url):
        if "SelectDocByItemIdAndChild" in url and "itemId=917" in url:
            return {
                "rptCode": 200,
                "data": {
                    "rows": [
                        {
                            "docId": 1001,
                            "docSubtitle": "人工智能指导意见答记者问",
                            "publishDate": "2026-06-18 18:47:44",
                            "generaltype": "0",
                        }
                    ]
                },
            }
        if "SelectDocByItemIdAndChild" in url and "itemId=915" in url:
            return {"rptCode": 200, "data": {"rows": []}}
        if "docId=1001" in url:
            return {
                "rptCode": 200,
                "data": {
                    "docId": 1001,
                    "docTitle": "人工智能指导意见答记者问",
                    "publishDate": "2026-06-18 18:47:44",
                    "docClob": """
                    <html><body>
                      <p>解读正文。</p>
                      <p>附：<a href="https://www.nfra.gov.cn/cn/view/pages/governmentDetail.html?docId=3001&generaltype=1">原文</a></p>
                    </body></html>
                    """,
                },
            }
        if "docId=3001" in url:
            raise TimeoutError("detail timeout")
        raise AssertionError(f"unexpected url: {url}")

    docs = fetch_recent_nfra_documents(
        client=NfraClient(requester=requester),
        today=date(2026, 7, 3),
        days=92,
        max_pages=1,
    )

    assert [doc["doc_id"] for doc in docs] == ["1001"]
    assert docs[0]["metadata"]["original_fetch_errors"][0]["doc_id"] == "3001"
