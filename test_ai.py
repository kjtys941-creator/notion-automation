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

# Notion データベースID
CRM_DATABASE_ID = "3c45c2f07cc58027a608ceee5756f87a"
INBOX_DATABASE_ID = "3c45c2f07cc58022986ff1726b012541"

# 使用するモデル（テスト後に自動で書き換わります）
WORKING_MODEL = None

def generate_content_with_retry(client, prompt, max_retries=3):
    global WORKING_MODEL
    for attempt in range(max_retries):
        try:
            return client.models.generate_content(
                model=WORKING_MODEL,
                contents=prompt
            )
        except Exception as e:
            error_msg = str(e)
            if "429" in error_msg or "RESOURCE_EXHAUSTED" in error_msg:
                wait_time = 15.0
                print(f"  -> ⏳ API制限検知。{wait_time}秒待機して再試行します... ({attempt+1}/{max_retries})")
                time.sleep(wait_time)
            else:
                raise e
    raise Exception("API呼び出しの再試行上限に達しました。")

try:
    notion = Client(auth=NOTION_TOKEN)
    client = genai.Client(api_key=GEMINI_API_KEY)

    print("\n==============================================")
    print("🤖 利用可能なAIモデルを自動探索します...")
    
    # 新旧の主要モデルを網羅
    models_to_test = [
        "gemini-2.5-flash",
        "gemini-1.5-flash",
        "gemini-1.5-pro",
        "gemini-1.0-pro",
        "gemini-pro"
    ]
    
    for m in models_to_test:
        try:
            print(f"  -> 🧪 '{m}' の通信テスト中...")
            # 簡単な挨拶でテスト送信
            client.models.generate_content(model=m, contents="Hello")
            WORKING_MODEL = m
            print(f"  -> 🟢 成功！今回の実行では【 {WORKING_MODEL} 】を使用します。")
            break
        except Exception:
            # 失敗した場合は無視して次のモデルへ
            pass
            
    if not WORKING_MODEL:
        raise Exception("利用できるAIモデルが一つも見つかりませんでした。Google APIキーの有効性を確認してください。")
    print("==============================================\n")

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
    ddgs = DDGS()

    for i, company in enumerate(company_pages):
        company_name = company["name"]
        company_id = company["id"]
        existing_news = company["existing_news"]
        existing_competitor = company["existing_competitor"]
        existing_industry = company["existing_industry"]

        print(f"\n----------------------------------------")
        print(f"[{i+1}/{len(company_pages)}] 処理中: {company_name}")

        properties_to_update = {}

        # ========================================================
        # 【モードA】マスターデータ（業界・競合）が空欄の場合
        # ========================================================
        if not existing_industry or not existing_competitor:
            print(f"  -> 🏗️ [マスターデータ補完モード] 業界・競合を調査します。")
            snippet = ""
            try:
                results = list(ddgs.text(f"{company_name} 事業内容 OR 企業情報", max_results=2))
                snippet = "\n".join([r.get('body', '') for r in results])
                print(f"  -> 🔍 企業情報検索完了")
            except Exception as e:
                print(f"  -> ⚠️ 検索エラー: {e}")

            prompt = f"""
対象企業名: {company_name}
検索結果: {snippet}
上記に基づき、以下の2つの情報を抽出し、必ずJSON形式のみで出力してください。
{{
  "COMPETITOR": "主な競合他社2〜3社（不明な場合は '不明'）",
  "INDUSTRY": "大分類-詳細の形式で業界分類（例: 製造業-空調機械。不明な場合は '不明'）"
}}
"""
            try:
                response = generate_content_with_retry(client, prompt)
                match = re.search(r'\{.*\}', response.text.strip(), re.DOTALL)
                if match:
                    data = json.loads(match.group(0))
                    if not existing_competitor:
                        properties_to_update["Competitor"] = {"rich_text": [{"text": {"content": data.get("COMPETITOR", "")}}]}
                    if not existing_industry:
                        properties_to_update["Industry"] = {"rich_text": [{"text": {"content": data.get("INDUSTRY", "")}}]}
                    print(f"  -> 🤖 AI解析完了 (業界: {data.get('INDUSTRY', '')})")
            except Exception as e:
                print(f"  -> ⚠️ AI解析エラー: {e}")

        # ========================================================
        # 【モードB】マスターデータ入力済みの場合（ニュース更新のみ）
        # ========================================================
        else:
            print(f"  -> 📰 [ニュース更新モード] 最新ニュースを調査します。 (業界: {existing_industry})")
            
            # 1. 企業ニュースの取得
            news_snippet = ""
            news_urls = []
            has_new_news = False
            try:
                results = list(ddgs.text(f"{company_name} 最新 ニュース OR プレスリリース", max_results=2))
                for r in results:
                    news_snippet += f"{r.get('body', '')}\n"
                    if r.get('href') and r.get('href') not in news_urls:
                        news_urls.append(r.get('href'))
            except Exception:
                pass

            if news_snippet:
                prompt_news = f"対象企業名: {company_name}\n以下の検索結果から直近ニュースを要約してください。ニュースが無ければ 'None' と出力してください。\n{news_snippet}"
                try:
                    res_news = generate_content_with_retry(client, prompt_news)
                    news_text = res_news.text.strip()
                    if news_text and "None" not in news_text:
                        if news_urls:
                            news_text += "\n\n【ソース】\n" + "\n".join(news_urls)
                        properties_to_update["NEWS"] = {"rich_text": [{"text": {"content": news_text}}]}
                        has_new_news = True
                    else:
                        properties_to_update["NEWS"] = {"rich_text": [{"text": {"content": "No News"}}]}
                except Exception as e:
                    print(f"  -> ⚠️ 企業ニュースAIエラー: {e}")

            # 2. 業界ニュースの取得
            ind_snippet = ""
            ind_urls = []
            try:
                ind_results = list(ddgs.text(f"{existing_industry} 業界 最新 ニュース", max_results=2))
                for r in ind_results:
                    ind_snippet += f"{r.get('body', '')}\n"
                    if r.get('href') and r.get('href') not in ind_urls:
                        ind_urls.append(r.get('href'))
            except Exception:
                pass

            if ind_snippet:
                prompt_ind = f"以下の【{existing_industry}】に関するニュースを短く1〜2行で要約してください。\n{ind_snippet}"
                try:
                    res_ind = generate_content_with_retry(client, prompt_ind)
                    ind_text = res_ind.text.strip()
                    if ind_text and ind_urls:
                        ind_text += "\n\n【ソース】\n" + "\n".join(ind_urls)
                    properties_to_update["Industry-News-"] = {"rich_text": [{"text": {"content": ind_text}}]}
                except Exception:
                    pass

            # 新着ニュースがあればInboxタスク作成
            if has_new_news:
                try:
                    notion.pages.create(
                        parent={"database_id": INBOX_DATABASE_ID},
                        properties={
                            "トピック": {"title": [{"text": {"content": f"【グローバル巡回】{company_name} の最新情報"}}]},
                            "Company Master": {"relation": [{"id": company_id}]},
                            "Emergency": {"multi_select": [{"name": "Middle"}]},
                            "メール要約": {"rich_text": [{"text": {"content": properties_to_update["NEWS"]["rich_text"][0]["text"]["content"]}}]},
                            "次のアクション": {"rich_text": [{"text": {"content": "内容を確認してアプローチ方針を検討する"}}]}
                        }
                    )
                except Exception:
                    pass

        # Notionの更新実行
        if properties_to_update:
            try:
                notion.pages.update(page_id=company_id, properties=properties_to_update)
                print(f"  -> ✅ Notion更新完了: {list(properties_to_update.keys())}")
            except Exception as e:
                print(f"  -> ⚠️ Notion更新エラー: {e}")
        else:
            print(f"  -> ⏩ 更新する新しい情報がありませんでした。")

        # 安定動作のためのインターバル
        time.sleep(10)

    print("\n🎉 処理がすべて正常に完了しました！")
except Exception as e:
    print(f"❌ 処理が全体エラーで停止しました: {e}")
