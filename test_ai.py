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

def generate_content_with_retry(client, prompt, max_retries=3):
    for attempt in range(max_retries):
        try:
            return client.models.generate_content(
                model='gemini-1.5-flash', # より安定した標準モデル名に変更
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
                
                comp_prop = props.get("Competitor", {}).get("rich_text", [])
                existing_competitor = "".join([t.get("text", {}).get("content", "") for t in comp_prop]) if comp_prop else ""

                ind_prop = props.get("Industry", {}).get("rich_text", [])
                existing_industry = "".join([t.get("text", {}).get("content", "") for t in ind_prop]) if ind_prop else ""

                if title_prop:
                    company_name = title_prop[0].get("text", {}).get("content", "")
                    company_pages.append({
                        "id": page["id"],
                        "name": company_name,
                        "existing_competitor": existing_competitor.strip(),
                        "existing_industry": existing_industry.strip()
                    })

        has_more = search_response.get("has_more", False)
        next_cursor = search_response.get("next_cursor", None)

    print(f"-> 合計 {len(company_pages)} 社のデータを取得しました。")

    client = genai.Client(api_key=GEMINI_API_KEY)

    for i, company in enumerate(company_pages):
        company_name = company["name"]
        company_id = company["id"]
        existing_competitor = company["existing_competitor"]
        existing_industry = company["existing_industry"]

        print(f"\n----------------------------------------")
        print(f"[{i+1}/{len(company_pages)}] 処理中: {company_name}")

        # 業界と競合の両方が既に入っている場合はスキップ（爆速化の要）
        if existing_industry and existing_industry not in ["不明", "None", ""] and existing_competitor and existing_competitor not in ["不明", "None", ""]:
            print(f"  -> ⏭️ 業界および競合が入力済みのためスキップします。")
            continue

        ddgs = DDGS()
        snippet = ""
        
        # 検索キーワードを「事業内容・競合」に最適化
        try:
            results = list(ddgs.text(f"{company_name} 事業内容 OR 企業情報 OR 競合", max_results=2))
            for r in results:
                snippet += f"{r.get('body', '')}\n"
            print(f"  -> 🔍 Web検索完了")
        except Exception as e:
            print(f"  -> ⚠️ Web検索エラー: {e}")

        # AIに業界と競合だけを考えさせる（処理の軽量化）
        prompt = f"""
対象企業名: {company_name}
検索結果: {snippet}

上記に基づき、以下の2つの情報を抽出し、必ずJSON形式のみで出力してください。
{{
  "COMPETITOR": "主な競合他社2〜3社（不明な場合は '不明'）",
  "INDUSTRY": "大分類-詳細の形式で業界分類（例: 製造業-空調機械。不明な場合は '不明'）"
}}
"""
        comp_text, industry_text = "", ""
        try:
            response = generate_content_with_retry(client, prompt)
            match = re.search(r'\{.*\}', response.text.strip(), re.DOTALL)
            if match:
                data = json.loads(match.group(0))
                comp_text = data.get("COMPETITOR", "")
                industry_text = data.get("INDUSTRY", "")
                print(f"  -> 🤖 AI解析完了 (業界: {industry_text}, 競合: {comp_text})")
        except Exception as e:
            print(f"  -> ⚠️ AI解析エラー: {e}")

        # Notionへ書き込み
        properties_to_update = {}

        if not existing_competitor and comp_text and comp_text != "不明":
            properties_to_update["Competitor"] = {"rich_text": [{"text": {"content": comp_text}}]}

        if not existing_industry and industry_text and industry_text != "不明":
            properties_to_update["Industry"] = {"rich_text": [{"text": {"content": industry_text}}]}

        if properties_to_update:
            try:
                notion.pages.update(page_id=company_id, properties=properties_to_update)
                print(f"  -> ✅ Notion更新完了: {list(properties_to_update.keys())}")
            except Exception as e:
                print(f"  -> ⚠️ Notion更新エラー: {e}")
        else:
            print(f"  -> ⏩ 更新する新しい情報がありませんでした。")

        # 429エラーを根本的に防ぐための4秒待機（1分間に15回の制限に絶対に引っかからないペース）
        time.sleep(4.5)

    print("\n🎉 マスターデータの自動入力がすべて完了しました！")
except Exception as e:
    print(f"❌ 処理が全体エラーで停止しました: {e}")
