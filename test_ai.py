from google import genai
from notion_client import Client
from duckduckgo_search import DDGS
import os

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
NOTION_TOKEN = os.environ.get("NOTION_API_KEY", "")
CRM_DATABASE_ID = "3c45c2f07cc58027a608ceee5756f87a"

print("=== 🔍 デバッグ処理開始 ===")

# 1. Notion取得テスト
try:
    notion = Client(auth=NOTION_TOKEN)
    search_response = notion.search(filter={"property": "object", "value": "page"})
    results = search_response.get("results", [])
    
    target_page = None
    for page in results:
        parent = page.get("parent", {})
        if parent.get("database_id", "").replace("-", "") == CRM_DATABASE_ID.replace("-", ""):
            target_page = page
            break
            
    if not target_page:
        print("❌ [1/4 Notion] CRMデータベース内に該当するページが見つかりませんでした。")
        exit(1)
        
    page_id = target_page["id"]
    props = target_page.get("properties", {})
    comp_name = props.get("Company Name", {}).get("title", [{}])[0].get("text", {}).get("content", "不明")
    print(f"✅ [1/4 Notion] 取得成功: テスト対象企業「{comp_name}」")
except Exception as e:
    print(f"❌ [1/4 Notion] 接続エラー: {e}")
    exit(1)

# 2. Web検索テスト
try:
    ddgs = DDGS()
    search_res = list(ddgs.text(f"{comp_name} ニュース", max_results=2))
    print(f"✅ [2/4 Web検索] 取得件数: {len(search_res)}件")
    if search_res:
        print(f"   検索結果サンプル: {search_res[0].get('body', '')[:60]}...")
    else:
        print("   ⚠️ 検索結果が0件でした")
except Exception as e:
    print(f"❌ [2/4 Web検索] エラー: {e}")

# 3. Gemini APIテスト
try:
    client = genai.Client(api_key=GEMINI_API_KEY)
    response = client.models.generate_content(
        model='gemini-2.5-flash',
        contents=f"{comp_name} の主要な業界分類を1行で答えてください。",
    )
    print(f"✅ [3/4 AI (Gemini)] 呼び出し成功: {response.text.strip()}")
except Exception as e:
    print(f"❌ [3/4 AI (Gemini)] エラー: {e}")

# 4. Notion書き込みテスト
try:
    notion.pages.update(
        page_id=page_id,
        properties={
            "Industry": {"rich_text": [{"text": {"content": "テスト用判定業界"}}]}
        }
    )
    print("✅ [4/4 Notion書き込み] 成功: 'Industry' 列にテストデータを書き込みました。")
except Exception as e:
    print(f"❌ [4/4 Notion書き込み] エラー: {e}")

print("=== 🔍 デバッグ処理終了 ===")
