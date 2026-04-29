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
        await page.goto(company_url, wait_until="load", timeout=30000)
        await page.wait_for_selector(".intro-table__head", timeout=10000)

        js_logic = """() => {
            const heads = Array.from(document.querySelectorAll('.intro-table__head'));
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


async def run_company_scraper(keyword=None):
    """
    主排程：從 DB 找出缺少資料的公司並進行採集。可接受動態關鍵字。
    """
    kw = keyword or os.getenv("DEFAULT_KEYWORD", "php")

    try:
        conn = await aiomysql.connect(
            host=os.getenv("DB_HOST", "127.0.0.1"),
            port=int(os.getenv("DB_PORT", 3308)),
            user=os.getenv("DB_USERNAME"),
            password=os.getenv("DB_PASSWORD"),
            db=os.getenv("DB_DATABASE"),
            charset="utf8mb4",
            autocommit=True,
            init_command="SET time_zone = '+08:00'",
        )
    except Exception as e:
        print(f"[Stage B] ❌ 資料庫連線失敗: {e}")
        return

    async with conn.cursor(aiomysql.DictCursor) as cur:
        # 找出需要補全資料的公司 (可選擇只補特定關鍵字的職缺，避免全表掃描過久)
        sql_fetch = (
            "SELECT company_name, company_link FROM vacancies "
            "WHERE keyword = %s AND (capital = '0' OR employee_count = '') "
            "AND company_link != '' GROUP BY company_link"
        )
        await cur.execute(sql_fetch, (kw,))
        companies = await cur.fetchall()

        if not companies:
            print(f"[Stage B] 🎉 '{kw}' 相關公司的資料都已經補齊囉！")
            conn.close()
            return

        print(f"[Stage B] 🚀 發現 {len(companies)} 家公司待查...")

        hl = os.getenv("BROWSER_HEADLESS", "false").lower() == "true"
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=hl)
            context = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/122.0.0.0 Safari/537.36"
                )
            )
            page = await context.new_page()

            for comp in companies:
                c_name = comp["company_name"]
                c_url = comp["company_link"]

                if not c_url or "javascript" in c_url:
                    continue

                print(f"[Stage B] 🔍 探查公司: {c_name}")
                info = await scrape_company_info(page, c_url)

                sql_update = """
                UPDATE vacancies
                SET capital = %s, employee_count = %s
                WHERE company_link = %s
                """
                args = (info["capital"], info["employees"], c_url)
                await cur.execute(sql_update, args)

                stat = f"資本: {info['capital']}, 人數: {info['employees']}"
                print(f"  [DB] 更新成功: {c_name} ({stat})")

                await asyncio.sleep(2)

            await browser.close()

    conn.close()
    print("[Stage B] ✅ 任務結束。")


if __name__ == "__main__":
    asyncio.run(run_company_scraper())
