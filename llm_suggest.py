"""LLM 助手:把使用者「想找什麼工作」的自然語言翻譯成 search_config 三欄。

對外只暴露 suggest_search_config(intent) -> dict。
回傳 schema:
  {
    "keyword": str,            # 1 個,送給 104 搜尋的主關鍵字
    "title_tags": str,         # 逗號分隔,標題層寬鬆收斂
    "content_tags": str,       # 逗號分隔,內文層精準匹配
    "reasoning": str,          # 給 UI 展開看「為什麼這樣建議」
  }

呼叫方:app.py 的 /api/llm/suggest-config endpoint。
Provider:Google Gemini(免費 tier 對個人專案綽綽有餘)。
"""

import json
import os

from google import genai
from google.genai import errors as genai_errors
from google.genai import types

# 預設 Gemini 2.5 Flash(免費 tier、繁中佳、有 JSON output 支援)
# 想要更強的可改 gemini-2.5-pro,但個人專案沒必要
_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

# JSON Schema(Gemini 的 response_schema 格式)
_RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "keyword": {
            "type": "STRING",
            "description": "送進 104 搜尋的主關鍵字,只能 1 個",
        },
        "title_tags": {
            "type": "ARRAY",
            "items": {"type": "STRING"},
            "description": "標題層寬鬆收斂類別,3-7 個",
        },
        "content_tags": {
            "type": "ARRAY",
            "items": {"type": "STRING"},
            "description": "內文層精準匹配,3-8 個,要有區別性",
        },
        "reasoning": {
            "type": "STRING",
            "description": "繁中 2-4 行,說明這樣建議的理由,使用 UI 名稱(關鍵字/標題過濾標籤/內文過濾標籤)",
        },
    },
    "required": ["keyword", "title_tags", "content_tags", "reasoning"],
}


_SYSTEM_PROMPT = """你是 104 求職關鍵字顧問,幫使用者把「想找什麼工作」翻譯成爬蟲設定。

【系統運作邏輯】
這份 search config 給 104 爬蟲用,執行三階段:
1. 用 keyword 去 104 搜尋,抓所有標題含 title_tags 任一的職缺
2. 對每筆打 104 API 拿內文,內文出現 content_tags 任一才標為「合格」
3. 補公司資本額與員工數(後續使用者另外在 admin 過濾規模)

【兩欄分工】
- title_tags(寬鬆收斂類別):
  用於初步篩選,只要標題碰到一個就保留。範圍要「寬」,抓得進來才有機會在 content_tags 階段精篩。
  通常 3-7 個。
- content_tags(精準內文匹配):
  用於把標題過了但內文不符的職缺排除。
  重點是「區別性」 — 要選**會出現在真正符合的職缺內文,但不會出現在假符合內文上**的詞。
  例:搜 PHP 時,光放 'php' 會被 .NET 職缺命中(因為 .NET 工程師職缺也常提「會 PHP 加分」)。
  應該加 'laravel' / 'composer' / 'phpunit' / 'php artisan' 這類「只有真的用 PHP 才會提」的工具/框架詞。
  通常 3-8 個。

【規則】
1. keyword 只能 1 個,選最廣的職務代表詞(送給 104 搜尋框用)
2. 年資詞(資深 / senior / 5年以上)放 content_tags(現有 substring 比對能 catch)
3. 公司規模 / 上市櫃 / 資本額 / 地區 → 不要產出 tag,這是後續 admin 列表頁過濾的事;但可以在 reasoning 提醒
4. 排除規則 / 負面關鍵字 → 不支援,如果使用者提到請在 reasoning 裡說明「建議事後人工排除」
5. reasoning 的措辭要用 UI 名稱:「關鍵字 / 標題過濾標籤 / 內文過濾標籤」,不要用英文欄位名

【Few-shot 範例】

意圖:"我想找資深 PHP 後端,有 Laravel 經驗"
回應:
{
  "keyword": "php",
  "title_tags": ["php", "後端", "backend", "網站"],
  "content_tags": ["laravel", "composer", "phpunit", "資深"],
  "reasoning": "關鍵字填入「php」送到 104 搜尋。\\n標題過濾標籤收進 PHP 後端類職缺。\\n\
內文過濾標籤用 laravel / composer / phpunit 避開「附帶會 PHP 的 .NET 職缺」這類假陽性\
 — 真正用 PHP 的職缺才會提這些工具。\\n年資詞「資深」放在內文過濾標籤,可被 substring 比對到。"
}

意圖:"中型公司的 React 前端,要會 TypeScript,不想去派遣"
回應:
{
  "keyword": "react",
  "title_tags": ["react", "前端", "frontend", "網頁"],
  "content_tags": ["react", "typescript", "next.js"],
  "reasoning": "關鍵字填入「react」。\\n標題過濾標籤收進 React / 前端類職缺。\\n\
內文過濾標籤用 typescript / next.js 補強區別性,避開「會 React 加分」的 jQuery / Vue 職缺。\\n\
中型公司請事後在 admin 列表頁用員工數過濾;排除派遣目前系統不支援,建議人工檢視。"
}

意圖:"DevOps,要會 K8s 跟 Terraform"
回應:
{
  "keyword": "devops",
  "title_tags": ["devops", "sre", "infrastructure", "雲端"],
  "content_tags": ["kubernetes", "k8s", "terraform", "aws", "gcp"],
  "reasoning": "關鍵字填入「devops」。\\n標題過濾標籤涵蓋 DevOps / SRE / Infra 等職缺類別。\\n\
內文過濾標籤加 terraform / aws / gcp 確保是真正的 IaC + 雲端職缺,\
避開「會用 Docker 就自稱 DevOps」的軟體工程師職缺。"
}

【動作】
針對使用者下面的意圖,**只回傳合法 JSON**,不要加任何 markdown 標籤或說明文字,直接 JSON。
"""


class LlmSuggestError(Exception):
    """suggest_search_config 失敗時拋的例外。app.py 接住轉成 HTTP error。"""


# Lazy-init client(每次 call 不必重建)
_client: genai.Client | None = None


def _get_client() -> genai.Client:
    global _client
    if _client is not None:
        return _client

    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise LlmSuggestError("未設定 GEMINI_API_KEY")

    _client = genai.Client(api_key=api_key)
    return _client


async def suggest_search_config(intent: str) -> dict:
    """主入口:吃自然語言意圖,回 search config dict。

    raises:
      LlmSuggestError: API key 未設、Gemini 失敗、回應不合預期等
    """
    intent = (intent or "").strip()
    if len(intent) < 5:
        raise LlmSuggestError("意圖描述太短(至少 5 個字)")
    if len(intent) > 500:
        raise LlmSuggestError("意圖描述太長(最多 500 字)")

    client = _get_client()

    config = types.GenerateContentConfig(
        system_instruction=_SYSTEM_PROMPT,
        response_mime_type="application/json",
        response_schema=_RESPONSE_SCHEMA,
        temperature=0.2,  # 低溫,讓建議穩定,但保留一點彈性補同義詞
        max_output_tokens=1024,
    )

    try:
        resp = await client.aio.models.generate_content(
            model=_MODEL,
            contents=f'意圖:"{intent}"',
            config=config,
        )
    except genai_errors.APIError as e:
        raise LlmSuggestError(f"Gemini API 錯誤: {e}") from e

    raw_text = (resp.text or "").strip()
    if not raw_text:
        raise LlmSuggestError("Gemini 回應為空")

    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as e:
        raise LlmSuggestError(
            f"Gemini 回應不是合法 JSON: {e}; raw={raw_text[:200]}"
        ) from e

    for required in ("keyword", "title_tags", "content_tags", "reasoning"):
        if required not in data:
            raise LlmSuggestError(f"Gemini 回應缺少必要欄位: {required}")

    # 把 list 轉成「a,b,c」字串(配合 admin form 欄位格式)
    title_tags = data["title_tags"]
    content_tags = data["content_tags"]
    if isinstance(title_tags, list):
        title_tags = ",".join(
            t.strip() for t in title_tags if isinstance(t, str) and t.strip()
        )
    if isinstance(content_tags, list):
        content_tags = ",".join(
            t.strip() for t in content_tags if isinstance(t, str) and t.strip()
        )

    return {
        "keyword": str(data["keyword"]).strip().lower(),
        "title_tags": title_tags,
        "content_tags": content_tags,
        "reasoning": str(data["reasoning"]),
    }


# 本機快測用 — 跑 `python llm_suggest.py` 看會回什麼
if __name__ == "__main__":
    import asyncio

    async def _smoke():
        try:
            out = await suggest_search_config("我想找資深 Golang 後端,要會 gRPC")
            print(json.dumps(out, indent=2, ensure_ascii=False))
        except LlmSuggestError as e:
            print(f"LlmSuggestError: {e}")

    asyncio.run(_smoke())
