import os
import google.generativeai as genai
from notion_client import Client
from duckduckgo_search import DDGS

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
NOTION_TOKEN = os.environ.get("NOTION_API_KEY", "")
CRM_DATABASE_ID = "3c45c2f07cc58027a608ceee5756f87a"

print("=== 🔍 デバッグ処理開始 ===")

# 1. Notion取得テスト
try:
    notion = Client(auth=NOTION_TOKEN)
    res = notion.databases.query(database_id=CRM_DATABASE_ID, page_size=1)
    pages = res.get("results", [])
    if not pages:
        print("❌ [1/4 Notion] データベースからページが取得できません。IDまたは権限を確認してください。")
        exit(1)
    
    page = pages[0]
    page_id = page["id"]
    props = page.get("properties", {})
    comp_name = props.get("Company Name", {}).get("title", [{}])[0].get("text", {}).get("content", "不明")
    print(f"✅ [1/4 Notion] 取得成功: テスト対象企業「{comp_name}」")
except Exception as e:
    print(f"❌ [1/4 Notion] 接続エラー: {e}")
    exit(1)

# 2. Web検索テスト
try:
    ddgs = DDGS()
    search_res = list(ddgs.text(f"{comp_name} ニュース", max_results=2))
    print(f"✅ [2/4 Web検索] 件数: {len(search_res)}件")
    if not search_res:
        print("   ⚠️ 検索結果が0件です（GitHub ActionsのIP制限の可能性あり）")
except Exception as e:
    print(f"❌ [2/4 Web検索] エラー: {e}")

# 3. Gemini APIテスト
try:
    genai.configure(api_key=GEMINI_API_KEY)
    # 利用可能なモデル一覧を取得
    available_models = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
    print(f"✅ [3/4 AI] 利用可能モデル一覧: {available_models[:3]}")
    
    model_name = available_models[0] if available_models else "gemini-1.0-pro"
    model = genai.GenerativeModel(model_name)
    ai_res = model.generate_content(f"{comp_name}の業界を1行で記述してください。")
    print(f"✅ [3/4 AI] 生成結果: {ai_res.text.strip()}")
except Exception as e:
    print(f"❌ [3/4 AI] エラー: {e}")

# 4. Notion書き込みテスト
try:
    notion.pages.update(
        page_id=page_id,
        properties={
            "Industry": {"rich_text": [{"text": {"content": "テスト業界"}}]}
        }
    )
    print("✅ [4/4 Notion書き込み] 成功: 'Industry' 列にテストデータを書き込みました。")
except Exception as e:
    print(f"❌ [4/4 Notion書き込み] エラー: {e}")

print("=== 🔍 デバッグ処理終了 ===")
