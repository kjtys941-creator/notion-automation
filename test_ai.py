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
            
            # ★ 既存のNEWS欄の内容を取得（上書き防止用）
            news_prop = props.get("NEWS", {}).get("rich_text", [])
            existing_news = ""
            if news_prop:
                existing_news = "".join([t.get("text", {}).get("content", "") for t in news_prop])

            if title_prop:
                company_name = title_prop[0].get("text", {}).get("content", "")
                company_pages.append({
                    "id": page["id"], 
                    "name": company_name,
                    "existing_news": existing_news
                })

    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel("gemini-1.5-flash")

    for i, company in enumerate(company_pages):
        company_name = company["name"]
        company_id = company["id"]
        existing_news = company["existing_news"]
        print(f"[{company_name}] の情報を収集中... ({i+1}/{len(company_pages)})")

        news_snippet = ""
        try:
            # ★ 検索クエリを強化（ベトナム関連メディアを明示的に指定）
            queries = [
                f"{company_name} ニュース",
                f"{company_name} プレスリリース",
                f"{company_name} ベトナム",
                f"{company_name} site:viet-jo.com",
                f"{company_name} site:nna.jp",
                f"{company_name} site:jetro.go.jp",
                f"{company_name} site:viet-kabu.com"
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

        # ★ ロジック改善：空欄へのNo News記入と、既存データの上書き防止
        if "News Summary and Insights: None" in ai_text or not ai_text:
            if not existing_news:
                # 既存のNEWS欄が空欄なら "No News" と記入
                print(f"-> [{company_name}] 新着情報なし。Notionが空欄のため「No News」を記入します。")
                notion.pages.update(
                    page_id=company_id,
                    properties={"NEWS": {"rich_text": [{"text": {"content": "No News"}}]}}
                )
            else:
                # すでに文字が入っているなら上書きスキップ
                print(f"-> [{company_name}] 新着情報なし。既存データを維持するため更新をスキップしました。")
        else:
            # 有益な情報が見つかった場合は更新して受信トレイにも追加
            print(f"-> [{company_name}] 有益な情報が見つかりました！Notionを更新します。")
            notion.pages.update(
                page_id=company_id,
                properties={"NEWS": {"rich_text": [{"text": {"content": ai_text}}]}}
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

        if i < len(company_pages) - 1:
            time.sleep(10)

    print("\n🎉 すべての企業の巡回が完了しました！")
except Exception as e:
    print(f"❌ エラーが発生しました: {e}")
