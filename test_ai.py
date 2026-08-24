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
    
    company_pages = []
    has_more = True
    next_cursor = None

    # ★ 修正: 以前成功した確実な検索方法（search）を使い、全件ページめくりを行う
    while has_more:
        query_args = {"filter": {"property": "object", "value": "page"}}
        if next_cursor:
            query_args["start_cursor"] = next_cursor
            
        search_response = notion.search(**query_args)
        
        for page in search_response.get("results", []):
            parent = page.get("parent", {})
            if parent.get("database_id", "").replace("-", "") == CRM_DATABASE_ID.replace("-", ""):
                props = page.get("properties", {})
                title_prop = props.get("Company Name", {}).get("title", [])
                
                news_prop = props.get("NEWS", {}).get("rich_text", [])
                existing_news = "".join([t.get("text", {}).get("content", "") for t in news_prop]) if news_prop else ""
                
                comp_prop = props.get("Competitor", {}).get("rich_text", [])
                existing_competitor = "".join([t.get("text", {}).get("content", "") for t in comp_prop]) if comp_prop else ""

                if title_prop:
                    company_name = title_prop[0].get("text", {}).get("content", "")
                    company_pages.append({
                        "id": page["id"], 
                        "name": company_name,
                        "existing_news": existing_news.strip(),
                        "existing_competitor": existing_competitor.strip()
                    })
        
        has_more = search_response.get("has_more", False)
        next_cursor = search_response.get("next_cursor", None)

    print(f"-> 合計 {len(company_pages)} 社のデータを取得しました。")

    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel("gemini-1.5-flash")

    for i, company in enumerate(company_pages):
        company_name = company["name"]
        company_id = company["id"]
        existing_news = company["existing_news"]
        existing_competitor = company["existing_competitor"]
        
        print(f"[{company_name}] の情報を収集中... ({i+1}/{len(company_pages)})")

        news_snippet = ""
        try:
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
            
            指示1: 収集データから対象企業の直近のニュースや事業動向を2〜3行で要約してください。有効な情報がない場合は「None」と出力してください。
            指示2: あなたの持つ知識を用いて、対象企業の主な競合企業を3社程度挙げてください。（例: A社, B社, C社）。不明な場合は「不明」としてください。
            
            必ず以下のフォーマット通りに2行で出力してください。
            NEWS: [要約、または None]
            COMPETITOR: [競合企業名]
            """
            response = model.generate_content(prompt)
            ai_text = response.text.strip()
            
            news_text = "None"
            comp_text = ""
            for line in ai_text.split('\n'):
                if line.startswith("NEWS:"):
                    news_text = line.replace("NEWS:", "").strip()
                elif line.startswith("COMPETITOR:"):
                    comp_text = line.replace("COMPETITOR:", "").strip()
        except Exception:
            news_text = "None"
            comp_text = ""

        properties_to_update = {}
        has_new_news = False

        # 1. NEWSの更新判定
        if "None" in news_text or not news_text:
            if not existing_news:
                properties_to_update["NEWS"] = {"rich_text": [{"text": {"content": "No News"}}]}
                print(f"  -> 新着ニュースなし。「No News」を記入します。")
            else:
                print(f"  -> 新着ニュースなし。既存のNEWSを維持します。")
        else:
            properties_to_update["NEWS"] = {"rich_text": [{"text": {"content": news_text}}]}
            has_new_news = True
            print(f"  -> 新着ニュースあり！更新します。")

        # 2. Competitorの更新判定
        if not existing_competitor and comp_text and "不明" not in comp_text:
            properties_to_update["Competitor"] = {"rich_text": [{"text": {"content": comp_text}}]}
            print(f"  -> 競合企業を追加します: {comp_text}")

        # 3. NotionのCompany CRM Masterを更新
        if properties_to_update:
            try:
                notion.pages.update(page_id=company_id, properties=properties_to_update)
            except Exception as e:
                print(f"  -> ⚠️ Notion更新エラー: {e}")

        # 4. 新着ニュースがあった場合のみ、受信トレイにタスク作成
        if has_new_news:
            try:
                notion.pages.create(
                    parent={"database_id": INBOX_DATABASE_ID},
                    properties={
                        "トピック": {"title": [{"text": {"content": f"【グローバル巡回】{company_name} の最新情報チェック"}}]},
                        "Company Master": {"relation": [{"id": company_id}]},
                        "Emergency": {"multi_select": [{"name": "Middle"}]},
                        "メール要約": {"rich_text": [{"text": {"content": news_text}}]},
                        "次のアクション": {"rich_text": [{"text": {"content": "内容を確認してアプローチ方針を検討する"}}]}
                    }
                )
            except Exception:
                pass

        if i < len(company_pages) - 1:
            time.sleep(10)

    print("\n🎉 すべての企業の巡回が完了しました！")
except Exception as e:
    print(f"❌ エラーが発生しました: {e}")
