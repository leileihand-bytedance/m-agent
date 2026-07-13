from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.policy_knowledge.govcn import GovcnClient, fetch_govcn_policy_documents


def test_fetch_govcn_policy_documents_reads_policy_original_page_and_deduplicates():
    json_calls = []
    html_calls = []

    def json_requester(url):
        json_calls.append(url)
        if "q=%E4%BF%83%E8%BF%9B%E6%B6%88%E8%B4%B9" in url:
            return {
                "code": 200,
                "searchVO": {
                    "listVO": [
                        {
                            "id": "6974607",
                            "title": "国务院办公厅关于<em>促进消费</em>品以旧换新的通知",
                            "url": "https://www.gov.cn/zhengce/zhengceku/202409/content_6974607.htm",
                            "pcode": "国办发〔2024〕3号",
                            "puborg": "国务院办公厅",
                            "pubtimeStr": "2024.09.01",
                            "childtype": "商贸、海关、旅游\\国内贸易（含供销）",
                            "summary": "推动<em>消费</em>品以旧换新。",
                        },
                        {
                            "id": "6974607",
                            "title": "重复文件",
                            "url": "https://www.gov.cn/zhengce/zhengceku/202409/content_6974607.htm",
                            "pubtimeStr": "2024.09.01",
                        },
                    ]
                },
            }
        if "q=%E6%9C%AA%E6%9D%A5%E4%BA%A7%E4%B8%9A" in url:
            return {
                "code": 200,
                "searchVO": {
                    "listVO": [
                        {
                            "id": "6980001",
                            "title": "国务院关于推动<em>未来产业</em>创新发展的意见",
                            "url": "https://www.gov.cn/zhengce/zhengceku/202501/content_6980001.htm",
                            "pcode": "国发〔2025〕1号",
                            "puborg": "国务院",
                            "pubtimeStr": "2025.01.15",
                            "childtype": "科技、教育\\科技",
                        }
                    ]
                },
            }
        raise AssertionError(f"unexpected url: {url}")

    def html_requester(url):
        html_calls.append(url)
        if "content_6974607" in url:
            return """
            <html><body>
              <script>ignore()</script>
              <div class="pages_content">
                <p>各省、自治区、直辖市人民政府，国务院各部委：</p>
                <p>一、加大消费品以旧换新政策支持力度。</p>
              </div>
            </body></html>
            """
        if "content_6980001" in url:
            return """
            <html><body>
              <div class="TRS_Editor">
                <p>培育发展未来制造、未来信息、未来材料等未来产业。</p>
              </div>
            </body></html>
            """
        raise AssertionError(f"unexpected page url: {url}")

    docs = fetch_govcn_policy_documents(
        client=GovcnClient(json_requester=json_requester, html_requester=html_requester),
        topics={"consumption": ["促进消费"], "future_industries": ["未来产业"]},
        max_pages=1,
    )

    assert [doc["doc_id"] for doc in docs] == ["6974607", "6980001"]
    assert len(html_calls) == 2
    assert "t=zhengcelibrary_gw" in json_calls[0]
    assert docs[0]["source"] == "govcn"
    assert docs[0]["category"] == "policy_original"
    assert docs[0]["title"] == "国务院办公厅关于促进消费品以旧换新的通知"
    assert docs[0]["publish_date"] == "2024-09-01"
    assert "消费品以旧换新政策支持" in docs[0]["text"]
    assert docs[0]["metadata"]["source_name"] == "国务院政策文件库"
    assert docs[0]["metadata"]["topic"] == "consumption"
    assert docs[0]["metadata"]["puborg"] == "国务院办公厅"
    assert docs[0]["metadata"]["pcode"] == "国办发〔2024〕3号"
    assert docs[0]["metadata"]["summary"] == "推动消费品以旧换新。"


def test_fetch_govcn_policy_documents_skips_non_govcn_urls():
    def json_requester(url):
        return {
            "code": 200,
            "searchVO": {
                "listVO": [
                    {
                        "id": "external-1",
                        "title": "外部链接",
                        "url": "https://example.com/policy.htm",
                        "pubtimeStr": "2026.01.01",
                    }
                ]
            },
        }

    def html_requester(url):
        raise AssertionError(f"should not fetch external url: {url}")

    docs = fetch_govcn_policy_documents(
        client=GovcnClient(json_requester=json_requester, html_requester=html_requester),
        topics={"macro": ["宏观经济"]},
        max_pages=1,
    )

    assert docs == []


def test_clean_policy_page_text_prefers_real_article_content():
    from app.policy_knowledge.govcn import clean_policy_page_text

    html = """
    <html><body>
      <div class="pages_content"><p>首页 | 登录 | 无障碍</p></div>
    </body></html>
      <div id="UCAP-CONTENT">
        <p>国务院办公厅关于印发政策文件的通知</p>
        <p>各地区、各有关部门要加强政策设计。</p>
      </div>
    """

    text = clean_policy_page_text(html)

    assert "各地区、各有关部门" in text
    assert "首页 | 登录" not in text
