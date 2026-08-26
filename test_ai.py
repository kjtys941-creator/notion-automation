from google import genai
from notion_client import Client
from duckduckgo_search import DDGS
import time
import os
import json
import re

# 環境変数の読み込み
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
NOTION_TOKEN = os.environ.get("NOTION_API_KEY", "")

# Notion データベースID (ユーザーの提供データから)
CRM_DATABASE_ID = "3c45c2f07cc58027a608ceee5756f87a"
INBOX_DATABASE_ID = "3c45c2f07cc58022986ff1726b012541"

try:
    notion = Client(auth=NOTION_TOKEN)
    print("1. 企業CRMマスターから企業一覧を取得中...")

    company_pages = []
    has_more = True
    next_cursor = None

    # Notionから企業一覧を取得
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

                # Industry プロパティも取得する
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

    # 新SDKクライアントの初期化
    client = genai.Client(api_key=GEMINI_API_KEY)

    for i, company in enumerate(company_pages):
        company_name = company["name"]
        company_id = company["id"]
        existing_news = company["existing_news"]
        existing_competitor = company["existing_competitor"]
        existing_industry = company["existing_industry"]

        print(f"\n----------------------------------------")
        print(f"[{i+1}/{len(company_pages)}] 処理中: {company_name}")

        ddgs = DDGS()

        # Step 1: 企業個別ニュースの検索
        news_snippet = ""
        try:
            results = list(ddgs.text(f"{company_name} ニュース OR プレスリリース", max_results=2))
            for r in results:
                news_snippet += f"{r.get('body', '')}\n"
            print(f"  -> 🔍 Web検索完了 ({len(results)}件取得)")
        except Exception as e:
            print(f"  -> ⚠️ Web検索エラー: {e}")

        # Step 2: AI分析 (JSON抽出)
        prompt = f"""
対象企業名: {company_name}
検索結果: {news_snippet}

上記に基づき、以下の3つの情報を抽出し、必ずJSON形式のみで出力してください。
{{
  "NEWS": "直近ニュースや事業動向の要約（なければ 'None'）",
  "COMPETITOR": "主な競合他社2〜3社（不明な場合は '不明'）",
  "INDUSTRY": "大分類-詳細の形式で業界分類（例: 製造業-空調機械。不明な場合は '不明'）"
}}
"""
        news_text, comp_text, industry_text = "None", "", ""
        try:
            response = client.models.generate_content(
                model='gemini-2.5-flash',
                contents=prompt
            )
            match = re.search(r'\{.*\}', response.text.strip(), re.DOTALL)
            if match:
                data = json.loads(match.group(0))
                news_text = data.get("NEWS", "None")
                comp_text = data.get("COMPETITOR", "")
                industry_text = data.get("INDUSTRY", "")
                print(f"  -> 🤖 AI解析完了 (業界: {industry_text})")
        except Exception as e:
            print(f"  -> ⚠️ AI解析エラー: {e}")

        # Step 3: 業界動向ニュースの検索と要約 (修正ポイント1: AI判定の業界情報を最優先にする)
        ind_news_text = ""
        # ユーザーの要件に従い、既存の業界情報は無視して、AIが判定した業界情報を最優先で使用する。
        # target_industry = existing_industry if existing_industry else industry_text
        target_industry = industry_text

        if target_industry and target_industry != "不明" and target_industry != "None":
            print(f"  -> 🔍 業界【{target_industry}】の動向ニュースを検索・要約中...")
            try:
                ind_query = f"{target_industry} 業界 最新 ニュース"
                ind_results = list(ddgs.text(ind_query, max_results=2))
                ind_snippet = "\n".join([r.get('body', '') for r in ind_results])

                if ind_snippet:
                    ind_prompt = f"以下の【{target_industry}】に関するニュースを短く1〜2行で要約してください。\n{ind_snippet}"
                    ind_response = client.models.generate_content(
                        model='gemini-2.5-flash',
                        contents=ind_prompt
                    )
                    ind_news_text = ind_response.text.strip()
            except Exception as e:
                print(f"  -> ⚠️ 業界ニュース検索エラー: {e}")

        # Step 4: Notion更新データの作成と書き込み
        properties_to_update = {}
        has_new_news = False

        # NEWS プロパティの更新
        if "None" in news_text or not news_text:
            if not existing_news:
                properties_to_update["NEWS"] = {"rich_text": [{"text": {"content": "No News"}}]}
        else:
            properties_to_update["NEWS"] = {"rich_text": [{"text": {"content": news_text}}]}
            has_new_news = True

        # Competitor プロパティの更新 (既存がなければ更新)
        if comp_text and comp_text != "不明" and not existing_competitor:
            properties_to_update["Competitor"] = {"rich_text": [{"text": {"content": comp_text}}]}

        # Industry プロパティの更新 (修正ポイント2: 常にAIの判定結果で上書きする)
        if industry_text and industry_text != "不明":
            # properties_to_update["Industry"] = {"rich_text": [{"text": {"content": industry_text}}]}
            properties_to_update["Industry"] = {"rich_text": [{"text": {"content": industry_text}}]}

        # Industry-News- プロパティの更新
        if ind_news_text:
            properties_to_update["Industry-News-"] = {"rich_text": [{"text": {"content": ind_news_text}}]}

        # 更新データの書き込み
        if properties_to_update:
            try:
                notion.pages.update(page_id=company_id, properties=properties_to_update)
                print(f"  -> ✅ Notion更新完了: {list(properties_to_update.keys())}")
            except Exception as e:
                print(f"  -> ⚠️ Notion更新エラー: {e}")

        # 新着ニュース検知時のInboxタスク自動登録
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

        # 連続実行を避けるためのスリープ
        if i < len(company_pages) - 1:
            time.sleep(3)

    print("\n🎉 全企業の巡回・業界分析が正常に終了しました！")
except Exception as e:
    print(f"❌ 処理が全体エラーで停止しました: {e}")
