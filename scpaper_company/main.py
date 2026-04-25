import asyncio
import os

import aiomysql
from dotenv import load_dotenv
from playwright.async_api import async_playwright

load_dotenv()


async def scrape_company_info(page, company_url):
    """
    進入公司頁面並擷取資本額與員工人數
    """
    try:
        print(f"  [Scraper] 正在前往: {company_url}")
        # 增加超時時間，因為公司頁面有時載入較慢
        await page.goto(company_url, wait_until="load", timeout=30000)
        await page.wait_for_selector(".intro-table__head", timeout=10000)

        # 使用 JS 一次性提取所有表格資訊
        js_logic = """() => {
            const heads = Array.from(
                document.querySelectorAll('.intro-table__head')
            );
            const res = { capital: '0', employees: '' };
            heads.forEach(h => {
                const headText = h.innerText;
                const valEl = h.parentElement.querySelector('.t3.mb-0');
                if (valEl) {
                    const valText = valEl.innerText.trim();
                    if (headText.includes('資本額')) res.capital = valText;
                    if (headText.includes('員工人數')) res.employees = valText;
                }
            });
            return res;
        }"""
        data = await page.evaluate(js_logic)
        return data
    except Exception as e:
        print(f"  [Scraper] 擷取失敗: {e}")
        return {"capital": "0", "employees": ""}


async def run_company_scraper():
    """
    主排程：從 DB 找出缺少資料的公司並進行採集
    """
    # 1. 連線資料庫
    try:
        conn = await aiomysql.connect(
            host=os.getenv("DB_HOST", "127.0.0.1"),
            port=int(os.getenv("DB_PORT", 3308)),
            user=os.getenv("DB_USERNAME"),
            password=os.getenv("DB_PASSWORD"),
            db=os.getenv("DB_DATABASE"),
            charset="utf8mb4",
            autocommit=True,
        )
    except Exception as e:
        print(f"[Stage B] ❌ 資料庫連線失敗: {e}")
        return

    async with conn.cursor(aiomysql.DictCursor) as cur:
        # 2. 找出需要補全資料的公司 (以 company_link 分組，避免重複爬取)
        sql_fetch = """
        SELECT company_name, company_link
        FROM vacancies
        WHERE capital = '0' OR employee_count = ''
        GROUP BY company_link
        """
        await cur.execute(sql_fetch)
        companies = await cur.fetchall()

        if not companies:
            print("[Stage B] 🎉 所有公司的資料都已經補齊囉！")
            conn.close()
            return

        print(f"[Stage B] 🚀 發現 {len(companies)} 家公司待查...")

        # 3. 啟動瀏覽器
        hl = os.getenv("BROWSER_HEADLESS", "false").lower() == "true"
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=hl)
            context = await browser.new_context()
            page = await context.new_page()

            for comp in companies:
                c_name = comp["company_name"]
                c_url = comp["company_link"]

                if not c_url or "javascript" in c_url:
                    continue

                print(f"[Stage B] 🔍 探查公司: {c_name}")
                info = await scrape_company_info(page, c_url)

                # 4. 回填資料至資料庫 (根據 company_link 更新)
                sql_update = """
                UPDATE vacancies
                SET capital = %s, employee_count = %s
                WHERE company_link = %s
                """
                args = (info["capital"], info["employees"], c_url)
                await cur.execute(sql_update, args)

                stat = f"資本: {info['capital']}, 人數: {info['employees']}"
                print(f"  [DB] 更新成功: {c_name} ({stat})")

                # 禮貌爬蟲：短暫休息
                await asyncio.sleep(2)

            await browser.close()

    conn.close()
    print("[Stage B] ✅ 任務結束。")


if __name__ == "__main__":
    asyncio.run(run_company_scraper())
