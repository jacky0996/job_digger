import asyncio
import os
from urllib.parse import urlparse, parse_qs
from dotenv import load_dotenv
from playwright.async_api import async_playwright

# 載入 .env 設定
load_dotenv()

async def run_list_scraper():
    """
    Stage A: List Fetcher (生產者)
    功能：啟動瀏覽器並前往 104 搜尋頁面
    """
    # 讀取環境變數設定
    is_headless = os.getenv("BROWSER_HEADLESS", "True").lower() == "true"
    keyword = os.getenv("DEFAULT_KEYWORD", "php")
    
    print(f"[Stage A] 正在初始化 Playwright 引擎 (Headless={is_headless}, Keyword={keyword})...")
    
    async with async_playwright() as p:
        # 1. 啟動瀏覽器
        browser = await p.chromium.launch(headless=is_headless, slow_mo=500)
        
        # 2. 建立新頁面並設定 User-Agent
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        )
        page = await context.new_page()
        
        # 1. 定義基礎搜尋目標網址
        target_url = f"https://www.104.com.tw/jobs/search/?keyword={keyword}&jobcat=2007000000"
        
async def producer(page, total_pages, keyword, queue):
    """
    生產者 (Stage A)：負責翻頁、滾動、擷取數據並推入 Queue
    """
    for p_num in range(1, total_pages + 1):
        print(f"\n[Producer] 🚀 正在處理第 {p_num}/{total_pages} 頁...")
        
        # 構建分頁 URL
        target_url = f"https://www.104.com.tw/jobs/search/?keyword={keyword}&jobcat=2007000000&page={p_num}"
        await page.goto(target_url, wait_until="domcontentloaded")
        
        # 處理延遲載入：滾動到底部觸發 Vue 渲染
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await asyncio.sleep(1.5)
        
        # 擷取本頁資料
        job_items = await page.query_selector_all(".job-list-item")
        count = 0
        for item in job_items:
            title_elem = await item.query_selector("a.js-job-link")
            if not title_elem: continue
            
            title = await title_elem.inner_text()
            job_link = "https:" + await title_elem.get_attribute("href")
            
            company_elem = await item.query_selector("ul.job-list-item__company-info a")
            company_name = (await company_elem.get_attribute("title")).strip() if company_elem else "未知公司"
            company_link = "https:" + await company_elem.get_attribute("href") if company_elem else ""
            
            salary_elem = await item.query_selector("span.b-tag--default, a.b-tag--default")
            salary = await salary_elem.inner_text() if salary_elem else "面議"
            
            # 將資料推入 Queue
            await queue.put({
                "title": title,
                "company": company_name,
                "salary": salary,
                "job_link": job_link,
                "company_link": company_link,
                "keyword": keyword
            })
            count += 1
            
        print(f"[Producer] ✅ 第 {p_num} 頁擷取完成，共 {count} 筆。")
        
        # 為了避免被 104 鎖定，每頁稍微停頓一下
        await asyncio.sleep(1)

    # 全部生產完畢後，發送結束訊號 (None) 給消費者
    await queue.put(None)

async def consumer(queue):
    """
    消費者 (Stage B 雛形)：負責從 Queue 取出資料進行後續處理
    """
    print("[Consumer] 📥 消費者啟動，等待數據流...")
    total_processed = 0
    while True:
        data = await queue.get()
        if data is None: # 收到結束訊號
            break
        
        # 這裡未來會是「進入公司頁面爬資本額」的入口
        total_processed += 1
        if total_processed % 10 == 0:
            print(f"  [Consumer] 目前已累積整理 {total_processed} 筆資料...")
            
        queue.task_done()
    
    print(f"\n[Consumer] 🏁 任務全部完成，共處理了 {total_processed} 筆職缺。")

async def run_list_scraper():
    """
    主控制流程
    """
    # 讀取環境變數設定
    is_headless = os.getenv("BROWSER_HEADLESS", "True").lower() == "true"
    keyword = os.getenv("DEFAULT_KEYWORD", "php")
    
    print(f"[Stage A] 正在初始化 Playwright 引擎 (Headless={is_headless}, Keyword={keyword})...")
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=is_headless, slow_mo=200)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        )
        page = await context.new_page()
        page.set_default_timeout(60000) # 設定全域超時為 60 秒
        
        # 1. 探測階段 (9999 法)
        init_url = f"https://www.104.com.tw/jobs/search/?keyword={keyword}&jobcat=2007000000"
        try:
            print(f"[Stage A] 正在執行初始化探測：{init_url}")
            await page.goto(init_url, wait_until="commit")
            
            # 檢查是否正常進入
            try:
                # 使用您在 Inspect 觀察到的精確容器名稱
                await page.wait_for_selector(".job-list-container", timeout=30000, state="attached")
                print("[Stage A] 職缺容器 (.job-list-container) 已掛載。")
            except Exception as e:
                await page.screenshot(path="debug_init_failed.png")
                print(f"[Stage A] 探測失敗，已存截圖至 debug_init_failed.png。")
                raise e
            
            # 使用 9999 跳轉探測總頁數
            print("[Stage A] 正執行 9999 跳轉...")
            jump_input = await page.query_selector(".go-page__input input")
            if jump_input:
                await jump_input.fill("9999")
                await jump_input.press("Enter")
                
                # 104 重定向與 URL 修正需要時間，使用緩衝與 load 檢查
                print("[Stage A] 等待分頁重定向與 URL 修正...")
                await asyncio.sleep(5) 
                await page.wait_for_load_state("load")
            
            parsed_query = parse_qs(urlparse(page.url).query)
            total_pages = int(parsed_query.get('page', [1])[0])
            print(f"[Stage A] 探測完成，總共有 {total_pages} 頁需要爬取。")
            
            # 2. 併發執行階段 (Producer-Consumer)
            queue = asyncio.Queue()
            
            # 同時啟動生產者與消費者
            await asyncio.gather(
                producer(page, total_pages, keyword, queue),
                consumer(queue)
            )

        except Exception as e:
            print(f"[Stage A] 發生錯誤：{e}")
        finally:
            await browser.close()
            print("[Stage A] 系統關閉作業結束。")

if __name__ == "__main__":
    asyncio.run(run_list_scraper())
