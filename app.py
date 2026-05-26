import asyncio
import logging
import os
import time

import asyncpg
from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from llm_suggest import LlmSuggestError, suggest_search_config
from scpaper_company.main import run_company_scraper
from scpaper_content.main import run_content_scraper

# 匯入各個階段的執行函式
from scraper_vacancies.main import run_list_scraper

load_dotenv()


# 過濾掉 admin 前端高頻 polling 的 access log,
# 不然 scraper 自己 print 的進度會被 GET /api/scrape/status/* 200 OK 洗掉。
class _SuppressStatusPollingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        return "/api/scrape/status/" not in msg


logging.getLogger("uvicorn.access").addFilter(_SuppressStatusPollingFilter())

app = FastAPI(title="Job Digger API Service")

logger = logging.getLogger("job_digger")


@app.on_event("startup")
async def run_init_sql():
    """容器啟動時把 init.sql 跑進 DB(類似 Laravel migrate)。

    所有 statements 都 idempotent(CREATE TABLE IF NOT EXISTS / DROP TRIGGER IF EXISTS
    / ON CONFLICT 等),每次 cold start 跑都安全。

    Cloud Run 偶爾會 cold start 撞到 Cloud SQL socket 還沒 ready,
    做輕量 retry 後仍失敗就 log 並放行(不阻擋 app 啟動,讓 readiness 失敗自動 restart)。
    """
    sql_path = os.path.join(os.path.dirname(__file__), "init.sql")
    if not os.path.exists(sql_path):
        logger.warning("init.sql not found at %s, skip migrate", sql_path)
        return

    with open(sql_path, "r", encoding="utf-8") as f:
        ddl = f.read()

    for attempt in range(1, 6):
        try:
            conn = await get_db_conn()
            try:
                await conn.execute(ddl)
                logger.info("init.sql applied successfully (attempt %d)", attempt)
                return
            finally:
                await conn.close()
        except Exception as exc:
            logger.warning("init.sql apply failed (attempt %d/5): %s", attempt, exc)
            await asyncio.sleep(2 * attempt)

    logger.error("init.sql failed after 5 attempts — app will start anyway")


# CORS：僅允許 job_digger_admin 與本機開發 origin
# (正式環境請覆寫 ALLOWED_ORIGINS env var,以逗號分隔)
# 註:admin 原本想用 84,但被 macOS / Docker Desktop reserve,改用 8084
_default_origins = "http://localhost:8084,http://127.0.0.1:8084"
_allowed_origins = [
    o.strip()
    for o in os.getenv("ALLOWED_ORIGINS", _default_origins).split(",")
    if o.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 任務進度表:config_id → progress dict
#
# 設計:
#   - 不只追「正在跑」,也要保留剛結束的任務狀態(done / failed)讓 admin 前端
#     在最後一次 polling 看得到結果。下次同 id 觸發時直接覆蓋。
#   - 進度由 scraper 自己 mutate 同一個 dict(passed by reference),
#     避免引入 callback / event bus 等多餘抽象。
#
# Progress schema:
#   {
#     "config_id": int,
#     "stage": "init"|"A"|"B"|"C"|"done"|"failed",
#     "current": int, "total": int,        # 當前 stage 的進度,跨 stage 會 reset
#     "started_at": float,                 # epoch seconds
#     "finished_at": float | None,         # None 代表仍在跑
#     "error": str | None,
#     "keyword": str,
#   }
active_tasks: dict[int, dict] = {}


def _is_running(p: dict | None) -> bool:
    return p is not None and p.get("finished_at") is None


def _any_task_running() -> tuple[bool, int | None]:
    """全域鎖:任何一個 keyword 正在跑就回傳 True,並附上佔用的 config_id。"""
    for cfg_id, p in active_tasks.items():
        if _is_running(p):
            return True, cfg_id
    return False, None


def _build_status(config_id: int, p: dict | None) -> dict:
    """把內部 progress dict 轉成給前端用的 status payload(含 elapsed/eta)。"""
    if p is None:
        return {"config_id": config_id, "is_running": False, "stage": None}

    elapsed = (p.get("finished_at") or time.time()) - p["started_at"]
    eta = None
    cur, tot = p["current"], p["total"]
    if _is_running(p) and cur > 0 and tot > 0 and p["stage"] in ("A", "B", "C"):
        # 簡單線性外推 — 只反映「目前 stage 還剩多久」,不跨 stage 估計
        eta = max(0, int(elapsed * (tot - cur) / cur))

    return {
        "config_id": config_id,
        "is_running": _is_running(p),
        "stage": p["stage"],
        "current": cur,
        "total": tot,
        "elapsed_sec": int(elapsed),
        "eta_sec": eta,
        "error": p.get("error"),
        "finished_at": p.get("finished_at"),
        "keyword": p.get("keyword"),
    }


async def get_db_conn():
    # asyncpg 預設 autocommit;session timezone 透過 server_settings 設定
    return await asyncpg.connect(
        host=os.getenv("DB_HOST", "127.0.0.1"),
        port=int(os.getenv("DB_PORT", 5434)),
        user=os.getenv("DB_USERNAME"),
        password=os.getenv("DB_PASSWORD"),
        database=os.getenv("DB_DATABASE"),
        server_settings={"timezone": "+08:00"},
    )


async def start_scraping_task(config_id: int):
    """
    背景任務執行流：
    1. 讀取配置
    2. Stage A (清單抓取)
    3. Stage B (深度內容篩選)
    4. Stage C (公司資訊補全)
    """
    print(f"\n--- 🚀 任務啟動 (ID: {config_id}) ---")
    progress = {
        "config_id": config_id,
        "stage": "init",
        "current": 0,
        "total": 0,
        "started_at": time.time(),
        "finished_at": None,
        "error": None,
        "keyword": "",
    }
    active_tasks[config_id] = progress

    conn = await get_db_conn()
    config = await conn.fetchrow(
        "SELECT keyword, title_tags, content_tags " "FROM search_configs WHERE id = $1",
        config_id,
    )

    if not config:
        print(f"❌ 找不到配置 ID: {config_id}，任務終止。")
        progress["stage"] = "failed"
        progress["error"] = f"找不到 config_id={config_id}"
        progress["finished_at"] = time.time()
        await conn.close()
        return

    keyword = config["keyword"]
    title_tags = [
        t.strip() for t in (config["title_tags"] or "").split(",") if t.strip()
    ]
    content_tags = [
        t.strip() for t in (config["content_tags"] or "").split(",") if t.strip()
    ]
    progress["keyword"] = keyword
    print(
        f"取得配置: keyword='{keyword}', "
        f"title_tags={title_tags}, content_tags={content_tags}"
    )

    # 執行流程 — 各 scraper 透過 progress dict 回報進度
    try:
        progress["stage"] = "A"
        progress["current"], progress["total"] = 0, 0
        print("\n>>> [Stage A] 啟動清單採集...")
        await run_list_scraper(
            keyword=keyword, title_tags=title_tags, progress=progress
        )

        progress["stage"] = "B"
        progress["current"], progress["total"] = 0, 0
        print("\n>>> [Stage B] 啟動內文深度過濾...")
        await run_content_scraper(
            keyword=keyword, content_tags=content_tags, progress=progress
        )

        progress["stage"] = "C"
        progress["current"], progress["total"] = 0, 0
        print("\n>>> [Stage C] 啟動公司資訊補全...")
        await run_company_scraper(keyword=keyword, progress=progress)

        progress["stage"] = "done"
        await conn.execute(
            "UPDATE search_configs SET last_scraped_at = NOW() WHERE id = $1",
            config_id,
        )
        print(f"\n--- ✅ 任務完成 (ID: {config_id}) ---")
    except Exception as e:
        progress["stage"] = "failed"
        progress["error"] = str(e)[:500]  # 別把堆疊全塞進 status
        print(f"❌ 任務執行過程中發生異常: {e}")
    finally:
        progress["finished_at"] = time.time()
        await conn.close()


async def _validate_and_start(
    config_id: int,
    background_tasks: BackgroundTasks,
    *,
    require_today: bool,
):
    """共用守衛流程。require_today=True 給使用者觸發用,False 給排程繞過今日限制。"""
    conn = await get_db_conn()
    try:
        if require_today:
            # PG 用 CURRENT_DATE 取代 MySQL 的 CURDATE()
            row = await conn.fetchrow(
                "SELECT id, (DATE(created_at) = CURRENT_DATE) AS is_today "
                "FROM search_configs WHERE id = $1",
                config_id,
            )
            if not row:
                raise HTTPException(status_code=404, detail="Config ID not found")
            if not row["is_today"]:
                raise HTTPException(
                    status_code=403,
                    detail="此關鍵字非今日建立,將由排程自動執行,無法手動觸發",
                )
        else:
            row = await conn.fetchrow(
                "SELECT id FROM search_configs WHERE id = $1", config_id
            )
            if not row:
                raise HTTPException(status_code=404, detail="Config ID not found")
    finally:
        await conn.close()

    if _is_running(active_tasks.get(config_id)):
        raise HTTPException(status_code=400, detail="此關鍵字的抓取任務已在執行中")

    # 全域鎖:同時間只允許一個 keyword 在跑
    busy, busy_id = _any_task_running()
    if busy:
        raise HTTPException(
            status_code=409,
            detail=f"另一個關鍵字 (config_id={busy_id}) 正在執行中,請稍後再觸發",
        )

    background_tasks.add_task(start_scraping_task, config_id)
    return {
        "status": "accepted",
        "message": f"Scraping task for config {config_id} has been started.",
        "config_id": config_id,
    }


@app.post("/api/scrape/{config_id}")
async def trigger_scrape(config_id: int, background_tasks: BackgroundTasks):
    """使用者手動觸發 — 只允許今日建立的 keyword(避免使用者繞過排程)。"""
    return await _validate_and_start(config_id, background_tasks, require_today=True)


@app.post("/api/scrape/scheduled/{config_id}")
async def trigger_scrape_scheduled(config_id: int, background_tasks: BackgroundTasks):
    """排程觸發 — 不檢查今日守衛(因為排程的職責就是處理過往 keyword)。

    注意:這個 endpoint 沒有 today 守衛,呼叫者必須確保自己是排程而非使用者
    (例如綁定到 host.docker.internal 內部網路、或加 secret token)。
    """
    return await _validate_and_start(config_id, background_tasks, require_today=False)


@app.get("/api/scrape/status/{config_id}")
async def get_scrape_status(config_id: int):
    """
    回報任務狀態:當前 stage / 進度 / elapsed / ETA / 錯誤訊息。
    任務結束(done|failed)後仍保留狀態,讓前端最後一次 polling 看得到結果,
    直到下次同 id 觸發時被覆蓋。
    """
    return _build_status(config_id, active_tasks.get(config_id))


class SuggestConfigRequest(BaseModel):
    intent: str = Field(..., min_length=5, max_length=500)


@app.post("/api/llm/suggest-config")
async def llm_suggest_config(req: SuggestConfigRequest):
    """LLM 助手:把自然語言意圖翻譯成 (keyword, title_tags, content_tags, reasoning)。

    給 admin 新增關鍵字頁面用,使用者不知道怎麼下關鍵字時觸發。
    failure:LlmSuggestError → 503(讓前端顯示「AI 暫時無法回應」)。
    """
    try:
        return await suggest_search_config(req.intent)
    except LlmSuggestError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e


@app.get("/health")
async def health_check():
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("APP_PORT", 8000))
    print(f"🔥 Job Digger API 正在啟動，Port: {port}")
    uvicorn.run(app, host="0.0.0.0", port=port)
