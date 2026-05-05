import os

import aiomysql
from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from scpaper_company.main import run_company_scraper
from scpaper_content.main import run_content_scraper

# 匯入各個階段的執行函式
from scraper_vacancies.main import run_list_scraper

load_dotenv()

app = FastAPI(title="Job Digger API Service")

# CORS：僅允許 job_digger_admin 與本機開發 origin
# (正式環境請覆寫 ALLOWED_ORIGINS env var,以逗號分隔)
_default_origins = "http://localhost:84,http://127.0.0.1:84"
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

# 追蹤正在執行中的任務 ID
active_tasks = set()


async def get_db_conn():
    return await aiomysql.connect(
        host=os.getenv("DB_HOST", "127.0.0.1"),
        port=int(os.getenv("DB_PORT", 3308)),
        user=os.getenv("DB_USERNAME"),
        password=os.getenv("DB_PASSWORD"),
        db=os.getenv("DB_DATABASE"),
        charset="utf8mb4",
        autocommit=True,
        init_command="SET time_zone = '+08:00'",
    )


async def start_scraping_task(config_id: int):
    """
    背景任務執行流：
    1. 讀取配置
    2. Stage A (清單抓取)
    3. Stage B (公司資訊補全)
    4. Stage C (深度內容篩選)
    """
    print(f"\n--- 🚀 任務啟動 (ID: {config_id}) ---")
    active_tasks.add(config_id)

    conn = await get_db_conn()
    async with conn.cursor(aiomysql.DictCursor) as cur:
        # 搜尋配置
        await cur.execute(
            "SELECT keyword, filter_tags FROM search_configs WHERE id = %s",
            (config_id,),
        )
        config = await cur.fetchone()

        if not config:
            print(f"❌ 找不到配置 ID: {config_id}，任務終止。")
            conn.close()
            return

        keyword = config["keyword"]
        filter_tags = [t.strip() for t in config["filter_tags"].split(",")]
        print(f"取得配置: keyword='{keyword}', tags={filter_tags}")

    # 執行流程
    try:
        # Stage A: 抓取清單
        print("\n>>> [Stage A] 啟動清單採集...")
        await run_list_scraper(keyword=keyword, filter_tags=filter_tags)

        # Stage C: 深度內容篩選 (先過濾掉不符要求的職缺)
        print("\n>>> [Stage C] 啟動內文深度過濾...")
        await run_content_scraper(keyword=keyword, filter_tags=filter_tags)

        # Stage B: 補全公司資訊 (只針對過濾後留下的職缺抓取公司資料)
        print("\n>>> [Stage B] 啟動公司資訊補全...")
        await run_company_scraper()

        print(f"\n--- ✅ 任務完成 (ID: {config_id}) ---")
    except Exception as e:
        print(f"❌ 任務執行過程中發生異常: {e}")
    finally:
        active_tasks.discard(config_id)
        conn.close()


@app.post("/api/scrape/{config_id}")
async def trigger_scrape(config_id: int, background_tasks: BackgroundTasks):
    """
    接收後端請求，啟動背景爬蟲任務
    """
    conn = await get_db_conn()
    async with conn.cursor() as cur:
        await cur.execute("SET time_zone = '+08:00'")
        await cur.execute("SELECT id FROM search_configs WHERE id = %s", (config_id,))
        if not await cur.fetchone():
            conn.close()
            raise HTTPException(status_code=404, detail="Config ID not found")
    conn.close()

    if config_id in active_tasks:
        raise HTTPException(status_code=400, detail="此關鍵字的抓取任務已在執行中")

    # 加入背景任務，立即回傳 HTTP 200
    background_tasks.add_task(start_scraping_task, config_id)

    return {
        "status": "accepted",
        "message": f"Scraping task for config {config_id} has been started.",
        "config_id": config_id,
    }


@app.get("/api/scrape/status/{config_id}")
async def get_scrape_status(config_id: int):
    """
    檢查特定 ID 是否正在執行中
    """
    return {"config_id": config_id, "is_running": config_id in active_tasks}


@app.get("/health")
async def health_check():
    return {"status": "ok", "port": 83}


if __name__ == "__main__":
    import uvicorn

    # 優先讀取環境變數 APP_PORT，若無則預設為 83
    port = int(os.getenv("APP_PORT", 83))
    print(f"🔥 Job Digger API 正在啟動，Port: {port}")
    uvicorn.run(app, host="0.0.0.0", port=port)
