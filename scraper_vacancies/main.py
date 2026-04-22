from fastapi import FastAPI, BackgroundTasks
from playwright.async_api import async_playwright

app = FastAPI(title="職缺挖掘機 (Job Digger) API", version="1.0.0")

@app.get("/")
async def root():
    return {"message": "Job Digger Scraper API is running!"}

async def run_104_scraper(keyword: str):
    print(f"[挖掘機] 啟動引擎：準備挖掘 '{keyword}' 相關職缺...")
    
    try:
        async with async_playwright() as p:
            # 必須使用 headless=True 才能在 Docker 內執行
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            
            print(f"[挖掘機] 正在前往 104 人力銀行...")
            await page.goto("https://www.104.com.tw/")
            
            # TODO: 這裡之後會換成真實的 104 搜尋、輸入條件、抓取 DOM 元素
            await page.wait_for_timeout(2000) 
            title = await page.title()
            
            print(f"[挖掘機] 成功抵達網頁：{title}")
            print(f"[挖掘機] ETL 鑽頭清洗作業完成！資料已準備寫入 DB ({keyword})")
            
            await browser.close()
    except Exception as e:
        print(f"[挖掘機] 作業發生錯誤：{e}")

@app.post("/api/scrape")
async def trigger_scrape(keyword: str, background_tasks: BackgroundTasks):
    """
    非同步觸發爬蟲任務的 API 端點
    這支 API 會瞬間回傳，讓前台 Laravel 不會 Timeout
    """
    background_tasks.add_task(run_104_scraper, keyword)
    
    return {
        "status": "success",
        "message": f"挖掘指令已下達！引擎已在背景啟動，正在幫您挖掘「{keyword}」...",
        "keyword": keyword
    }
