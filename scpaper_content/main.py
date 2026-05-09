import asyncio
import os
import random
from datetime import datetime

import aiomysql
from dotenv import load_dotenv
from playwright.async_api import async_playwright
from playwright_stealth import Stealth

load_dotenv()

USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
]

CONTEXT_ROTATE_EVERY = int(os.getenv("CONTEXT_ROTATE_EVERY", 50))
LONG_BREAK_EVERY = int(os.getenv("LONG_BREAK_EVERY", 40))
PER_JOB_MAX_ATTEMPTS = int(os.getenv("PER_JOB_MAX_ATTEMPTS", 3))
CONTENT_POLL_ROUNDS = int(os.getenv("CONTENT_POLL_ROUNDS", 6))
CF_CHALLENGE_KEYWORDS = ("正在執行安全驗證", "Just a moment", "Checking your browser")
RETRYABLE_RESULTS = ("CF_CHALLENGE", "偵測逾時，建議手動確認")


async def _new_context(browser):
    return await browser.new_context(
        user_agent=random.choice(USER_AGENTS),
        viewport={
            "width": random.choice([1280, 1366, 1440, 1536, 1680, 1920]),
            "height": random.choice([720, 800, 900, 1080]),
        },
        locale="zh-TW",
        timezone_id="Asia/Taipei",
    )


async def _has_cf_challenge(page) -> bool:
    try:
        return await page.evaluate(
            "(kws) => { const t = document.body ? document.body.innerText : ''; "
            "return kws.some(k => t.includes(k)); }",
            list(CF_CHALLENGE_KEYWORDS),
        )
    except Exception:
        return False


async def _pass_cf_challenge(page, timeout_ms=30000) -> bool:
    """偵測到 CF challenge 時等它自動解。等不過 fallback 重整一次,還是不過就放棄。"""
    if not await _has_cf_challenge(page):
        return True

    try:
        await page.wait_for_function(
            "(kws) => { const t = document.body ? document.body.innerText : ''; "
            "return !kws.some(k => t.includes(k)); }",
            arg=list(CF_CHALLENGE_KEYWORDS),
            timeout=timeout_ms,
        )
        return True
    except Exception:
        pass

    try:
        await page.reload(wait_until="load", timeout=30000)
        await asyncio.sleep(random.uniform(2, 4))
        return not await _has_cf_challenge(page)
    except Exception:
        return False


async def deep_analyze_job(page, url, tags):
    try:
        await page.goto(url, wait_until="load", timeout=30000)

        if not await _pass_cf_challenge(page):
            return "CF_CHALLENGE"

        is_loaded = False
        for i in range(1, CONTENT_POLL_ROUNDS + 1):
            if await page.query_selector(".job-description__content"):
                is_loaded = True
                break
            await page.evaluate(f"window.scrollBy(0, {random.randint(300, 700)})")
            await asyncio.sleep(random.uniform(2.5, 5.0))

        if not is_loaded:
            return "偵測逾時，建議手動確認"

        try:
            await page.mouse.move(random.randint(100, 800), random.randint(100, 600))
        except Exception:
            pass

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

            // 第 3 檢查:擅長工具
            // 以 .list-row__head 為 anchor — 找到 head 文字含「擅長工具」的 row,
            // 取整個 row 的純文字(innerText 不含 HTML 標籤)再檢查關鍵字。
            const heads = document.querySelectorAll('.list-row__head');
            for (const head of heads) {
                const headText = (head.innerText || '').replace(/\\s+/g, '');
                if (!headText.includes('擅長工具')) continue;
                const row = head.closest('.list-row') || head.parentElement;
                if (row && containsTag(row.innerText, tags)) {
                    return "僅有擅長工具含關鍵字,建議確認後再進行履歷投遞";
                }
            }
            return null;
        }"""
        return await page.evaluate(js_logic, tags) or "no_match"
    except Exception:
        return "404_ERROR"


async def run_content_scraper(keyword, content_tags, progress=None):
    """
    依照 API 傳入的 keyword 與 content_tags 進行內容清洗。
    content_tags 是「內文關鍵字」清單(從 search_configs.content_tags 來),
    跟 Stage A 的 title_tags 是不同欄位 — A 用標題收斂,B 用內文精準匹配。
    progress(optional dict): 寫入 total=待檢查職缺數,current=已處理數。
    """
    kw = keyword

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
        # 增量爬取:只挑「沒處理過」或「上次偵測逾時」的
        sql = (
            "SELECT id, job_link, title FROM vacancies "
            "WHERE status = 'active' AND keyword = %s "
            "AND (check_type IS NULL OR check_type = '偵測逾時，建議手動確認')"
        )
        await cur.execute(sql, (kw,))
        jobs = await cur.fetchall()

        if not jobs:
            print(f"[Stage C] 🎉 '{kw}' 內文資料都已處理完畢")
            conn.close()
            return

        if progress is not None:
            progress["total"] = len(jobs)

        hl = os.getenv("BROWSER_HEADLESS", "false").lower() == "true"
        use_real_chrome = os.getenv("USE_REAL_CHROME", "true").lower() == "true"
        async with Stealth().use_async(async_playwright()) as p:
            launch_kwargs = {
                "headless": hl,
                "args": [
                    "--disable-blink-features=AutomationControlled",
                    "--disable-features=IsolateOrigins,site-per-process",
                ],
            }
            if use_real_chrome:
                launch_kwargs["channel"] = "chrome"
            browser = await p.chromium.launch(**launch_kwargs)
            context = await _new_context(browser)
            page = await context.new_page()
            timeout_streak = 0

            for idx, job in enumerate(jobs, start=1):
                if progress is not None:
                    progress["current"] = idx

                if idx > 1 and idx % CONTEXT_ROTATE_EVERY == 1:
                    await context.close()
                    context = await _new_context(browser)
                    page = await context.new_page()

                check_res = None
                for attempt in range(1, PER_JOB_MAX_ATTEMPTS + 1):
                    check_res = await deep_analyze_job(
                        page, job["job_link"], content_tags
                    )
                    if check_res not in RETRYABLE_RESULTS:
                        break
                    if attempt < PER_JOB_MAX_ATTEMPTS:
                        print(
                            f"[Stage C] ⚠️ {check_res} → 第 {attempt} 次重試"
                            f" (id={job['id']}),換 context 後再試"
                        )
                        await context.close()
                        context = await _new_context(browser)
                        page = await context.new_page()
                        await asyncio.sleep(random.uniform(8, 15))
                    else:
                        print(
                            f"[Stage C] ❌ {check_res} 重試 {PER_JOB_MAX_ATTEMPTS}"
                            f" 次仍失敗 (id={job['id']}),記錄結果並繼續"
                        )

                if check_res in RETRYABLE_RESULTS:
                    timeout_streak += 1
                else:
                    timeout_streak = 0

                if timeout_streak >= 3:
                    cooldown = random.uniform(300, 600)
                    print(f"[Stage C] ⚠️ 連續多筆失敗，冷卻 {int(cooldown)}s 並更換指紋")
                    await context.close()
                    await asyncio.sleep(cooldown)
                    context = await _new_context(browser)
                    page = await context.new_page()
                    timeout_streak = 0

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

                if idx % LONG_BREAK_EVERY == 0:
                    await asyncio.sleep(random.uniform(20, 45))
                else:
                    await asyncio.sleep(random.uniform(3, 8))
            await browser.close()
    conn.close()
    print("[Stage C] ✅ 完成。")


if __name__ == "__main__":
    asyncio.run(run_content_scraper())
