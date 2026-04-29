import asyncio
import os
from datetime import datetime

import aiomysql
from dotenv import load_dotenv
from playwright.async_api import async_playwright

load_dotenv()


async def deep_analyze_job(page, url, tags):
    try:
        await page.goto(url, wait_until="load", timeout=30000)

        is_loaded = False
        for i in range(1, 4):
            if await page.query_selector(".job-description__content"):
                is_loaded = True
                break
            await page.evaluate("window.scrollBy(0, 500)")
            await asyncio.sleep(4)

        if not is_loaded:
            return "偵測逾時，建議手動確認"

        js_logic = """(tags) => {
            const containsTag = (text, tags) => {
                if (!text) return false;
                const lowerText = text.toLowerCase().replace(/\\s+/g, '');
                return tags.some(tag => lowerText.includes(
                    tag.toLowerCase().replace(/\\s+/g, '')
                ));
            };

            const descEl = document.querySelector('.job-description__content');
            if (descEl && containsTag(descEl.innerText, tags)) {
                return "工作內容有含關鍵字";
            }

            const reqContainer = document.querySelector('.job-requirement') ||
                                document.querySelector('.job-requirement-table');
            if (reqContainer && containsTag(reqContainer.innerText, tags)) {
                return "加分條件或必要條件內有含關鍵字";
            }

            const allTextRows = Array.from(
                document.querySelectorAll('.list-row, .job-requirement-table__row')
            );
            for (const row of allTextRows) {
                const text = row.innerText || "";
                if (text.includes('擅長工具') && containsTag(text, tags)) {
                    return "僅有擅長工具含關鍵字,建議確認後再進行履歷投遞";
                }
            }
            return null;
        }"""
        return await page.evaluate(js_logic, tags)
    except Exception:
        return "404_ERROR"


async def run_content_scraper(keyword, filter_tags):
    """
    依照 API 傳入的 keyword 與 filter_tags 進行內容清洗。
    """
    kw = keyword
    # 這裡直接使用 filter_tags 參數，不再另外賦值給 tags 變數以避免 unused warning

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
    except Exception:
        return

    async with conn.cursor(aiomysql.DictCursor) as cur:
        sql = (
            "SELECT id, job_link, title FROM vacancies "
            "WHERE status = 'active' AND keyword = %s"
        )
        await cur.execute(sql, (kw,))
        jobs = await cur.fetchall()

        if not jobs:
            conn.close()
            return

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

            for job in jobs:
                check_res = await deep_analyze_job(page, job["job_link"], filter_tags)
                if check_res == "404_ERROR":
                    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    sql_closed = (
                        "UPDATE vacancies SET status = 'closed', "
                        "deleted_at = %s WHERE id = %s"
                    )
                    await cur.execute(sql_closed, (now, job["id"]))
                elif check_res:
                    sql_upd = "UPDATE vacancies SET check_type = %s WHERE id = %s"
                    await cur.execute(sql_upd, (check_res, job["id"]))
                await asyncio.sleep(2)
            await browser.close()
    conn.close()
    print("[Stage C] ✅ 完成。")


if __name__ == "__main__":
    asyncio.run(run_content_scraper())
