import google.generativeai as genai
from notion_client import Client
from ddgs import DDGS
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

                ind_prop = props.get("Industry", {}).get("rich_text", [])
                existing_industry = "".join([t.get("text", {}).get("content", "") for t in ind_prop]) if ind_prop else ""

                if title_prop:
                    company_name = title_prop[0].get("text", {}).get("content", "")
                    company_pages.append({
                        "id": page["id"], 
                        "name": company_name,
                        "existing_news": existing_news.strip(),
                        "existing_competitor": existing_competitor.strip(),
                        "existing_industry": existing_industry.strip()
                    })
        
        has_more = search_response.get("has_more", False)
        next_cursor = search_response.get("next_cursor", None)

    print(f"-> 合計 {len(company_pages)} 社のデータを取得しました。")

    genai.configure(api_key=GEMINI_API_KEY)
    # モデルを Gemini 3.1 Pro に変更
    model = genai.GenerativeModel("gemini-3.1-pro")

    for i, company in enumerate(company_pages):
        company_name = company["name"]
        company_id = company["id"]
        existing_news = company["existing_news"]
        existing_competitor = company["existing_competitor"]
        existing_industry = company["existing_industry"]
        
        print(f"[{company_name}] の情報を収集中... ({i+1}/{len(company_pages)})")

        ddgs = DDGS()

        # 1. 企業個別ニュースの検索
        news_snippet = ""
        queries = [
            f"{company_name} ニュース",
            f"{company_name} プレスリリース",
            f"{company_name} ベトナム"
        ]
        for q in queries:
            try:
                results = list(ddgs.text(q, max_results=2))
                for r in results:
                    news_snippet += f"{r.get('body', '')}\n"
            except Exception:
                continue

        # 2. AIによる分析 (NEWS, COMPETITOR, INDUSTRY)
        prompt = f"""
        対象名称: {company_name}
        検索結果:
        {news_snippet}
        
        指示1 (NEWS): 検索結果から対象の直近ニュースや事業動向を2〜3行で要約。有効情報がなければ「None」と出力。
        指示2 (COMPETITOR): 対象または上位親企業の主な競合他社を2〜3社挙げてください（例: ダイキン工業, 三菱電機）。推測を含めて回答してください。
        指示3 (INDUSTRY): 対象が属する業界を「大分類-詳細（例: 製造業-空調（機械））」の形式で分類してください。名称から推測できる場合も分類してください。

        必ず以下のフォーマット通りに出力してください:
        NEWS: [要約、または None]
        COMPETITOR: [競合企業名]
        INDUSTRY: [業界分類]
        """
        
        news_text, comp_text, industry_text = "None", "", ""
        try:
            response = model.generate_content(prompt)
            ai_text = response.text.strip()
            
            for line in ai_text.split('\n'):
                if line.startswith("NEWS:"):
                    news_text = line.replace("NEWS:", "").strip()
                elif line.startswith("COMPETITOR:"):
                    comp_text = line.replace("COMPETITOR:", "").strip()
                elif line.startswith("INDUSTRY:"):
                    industry_text = line.replace("INDUSTRY:", "").strip()
        except Exception as e:
            print(f"  -> AI生成エラー: {e}")

        # 3. 業界ニュースの検索と要約
        ind_news_text = ""
        target_industry = existing_industry if existing_industry else industry_text
        if target_industry and target_industry != "不明" and target_industry != "None":
            try:
                ind_query = f"{target_industry} 業界 最新 ニュース"
                ind_results = list(ddgs.text(ind_query, max_results=3))
                ind_snippet = "\n".join([r.get('body', '') for r in ind_results])
                
                if ind_snippet:
                    ind_prompt = f"以下の【{target_industry}】に関する動向・ニュースを短く1〜2行で要約してください。\n{ind_snippet}"
                    ind_response = model.generate_content(ind_prompt)
                    ind_news_text = ind_response.text.strip()
            except Exception:
                ind_news_text = ""

        # 4. Notion更新データの作成
        properties_to_update = {}
        has_new_news = False

        # NEWS
        if "None" in news_text or not news_text:
            if not existing_news:
                properties_to_update["NEWS"] = {"rich_text": [{"text": {"content": "No News"}}]}
        else:
            properties_to_update["NEWS"] = {"rich_text": [{"text": {"content": news_text}}]}
            has_new_news = True

        # Competitor (未入力時のみ更新)
        if comp_text and "不明" not in comp_text and not existing_competitor:
            properties_to_update["Competitor"] = {"rich_text": [{"text": {"content": comp_text}}]}

        # Industry
        if industry_text and "不明" not in industry_text:
            properties_to_update["Industry"] = {"rich_text": [{"text": {"content": industry_text}}]}

        # Industry-News-
        if ind_news_text:
            properties_to_update["Industry-News-"] = {"rich_text": [{"text": {"content": ind_news_text}}]}

        # Notion更新の実行
        if properties_to_update:
            try:
                notion.pages.update(page_id=company_id, properties=properties_to_update)
                print(f"  -> Notion更新成功: {list(properties_to_update.keys())}")
            except Exception as e:
                print(f"  -> ⚠️ Notion更新エラー: {e}")

        # 新着ニュース時のInbox登録
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
            time.sleep(5)

    print("\n🎉 全企業の巡回・業界分析が完了しました！")
except Exception as e:
    print(f"❌ エラーが発生しました: {e}")
