import google.generativeai as genai
from notion_client import Client
from duckduckgo_search import DDGS
import time
import os

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
NOTION_TOKEN = os.environ.get("NOTION_API_KEY", "")
CRM_DATABASE_ID = "3c45c2f07cc58027a608ceee5756f87a"
INBOX_DATABASE_ID = "3c45c2f07cc58022986ff1726b012541"

try:
    notion = Client(auth=NOTION_TOKEN)
    print("1. 企業CRMマスターから企業一覧を取得中...")
    search_response = notion.search(filter={"property": "object", "value": "page"})
    
    company_pages = []
    for page in search_response.get("results", []):
        parent = page.get("parent", {})
        if parent.get("database_id", "").replace("-", "") == CRM_DATABASE_ID.replace("-", ""):
            props = page.get("properties", {})
            title_prop = props.get("Company Name", {}).get("title", [])
            if title_prop:
                company_name = title_prop[0].get("text", {}).get("content", "")
                company_pages.append({"id": page["id"], "name": company_name})

    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel("gemini-1.5-flash")

    for i, company in enumerate(company_pages):
        company_name = company["name"]
        company_id = company["id"]
        print(f"[{company_name}] の情報を収集中... ({i+1}/{len(company_pages)})")

        news_snippet = ""
        try:
            # 検索クエリを実用的かつシンプルに変更
            queries = [
                f"{company_name} ニュース",
                f"{company_name} プレスリリース",
                f"{company_name} ベトナム"
            ]
            ddgs = DDGS()
            for q in queries:
                try:
                    results = list(ddgs.text(q, max_results=2))
                    for r in results:
                        news_snippet += f"【検索: {q}】{r.get('body', '')}\n"
                except Exception:
                    continue
        except Exception:
            news_snippet = ""

        try:
            prompt = f"""
            対象企業: {company_name}
            収集データ:
            {news_snippet}
            
            上記データから、対象企業の直近のニュース、事業動向、プレスリリース等の最新情報を要約してください。
            もし有効な情報が一切見つからない場合や無関係な情報のみの場合は、必ず「News Summary and Insights: None」とだけ出力してください。
            有益な情報がある場合は、ビジネスパーソン向けに2〜3行で簡潔にまとめてください。
            """
            response = model.generate_content(prompt)
            ai_text = response.text.strip()
        except Exception:
            ai_text = "News Summary and Insights: None"

        if "News Summary and Insights: None" in ai_text or not ai_text:
            print(f"-> [{company_name}] 新着情報がないため、Notionの更新をスキップしました。")
        else:
            notion.pages.update(
                page_id=company_id,
                properties={"最新業界・競合ニュース": {"rich_text": [{"text": {"content": ai_text}}]}}
            )
            notion.pages.create(
                parent={"database_id": INBOX_DATABASE_ID},
                properties={
                    "トピック": {"title": [{"text": {"content": f"【グローバル巡回】{company_name} の最新情報チェック"}}]},
                    "Company Master": {"relation": [{"id": company_id}]},
                    "Emergency": {"multi_select": [{"name": "Middle"}]},
                    "メール要約": {"rich_text": [{"text": {"content": ai_text}}]},
                    "次のアクション": {"rich_text": [{"text": {"content": "内容を確認してアプローチ方針を検討する"}}]}
                }
            )
            print(f"-> [{company_name}] の情報更新を完了しました！")

        if i < len(company_pages) - 1:
            time.sleep(10)

    print("\n🎉 すべての企業の巡回が完了しました！")
except Exception as e:
    print(f"❌ エラーが発生しました: {e}")
