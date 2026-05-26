import asyncio
import os
import random
import re

import asyncpg
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

PER_PAGE_MAX_ATTEMPTS = int(os.getenv("PER_PAGE_MAX_ATTEMPTS", 3))
CF_CHALLENGE_KEYWORDS = ("正在執行安全驗證", "Just a moment", "Checking your browser")

# 加速:不載入 image/font/media 與已知追蹤 domain。
# 保留 document/script/stylesheet/xhr/fetch — SPA 渲染與抓資料都需要。
# 若懷疑被偵測為 bot,設 BLOCK_RESOURCES=false 即可恢復原行為。
_BLOCKED_RESOURCE_TYPES = {"image", "font", "media"}
_BLOCKED_DOMAINS = (
    "google-analytics.com",
    "googletagmanager.com",
    "doubleclick.net",
    "facebook.net",
    "connect.facebook.net",
    "hotjar.com",
    "clarity.ms",
)


async def _register_resource_blocker(context):
    if os.getenv("BLOCK_RESOURCES", "true").lower() != "true":
        return

    async def _handler(route):
        req = route.request
        if req.resource_type in _BLOCKED_RESOURCE_TYPES:
            await route.abort()
            return
        if any(d in req.url for d in _BLOCKED_DOMAINS):
            await route.abort()
            return
        await route.continue_()

    await context.route("**/*", _handler)


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


async def _goto_with_retry(page, url, max_attempts=PER_PAGE_MAX_ATTEMPTS) -> bool:
    """單頁 goto + CF 挑戰處理,最多重試 max_attempts 次。"""
    for attempt in range(1, max_attempts + 1):
        try:
            await page.goto(url, wait_until="load", timeout=30000)
            if await _pass_cf_challenge(page):
                return True
            print(f"[Producer] ⚠️ CF 挑戰未解 (attempt {attempt}/{max_attempts})")
        except Exception as e:
            print(f"[Producer] ⚠️ goto 失敗 (attempt {attempt}/{max_attempts}): {e}")
        await asyncio.sleep(random.uniform(3, 6))
    return False


async def producer(page, total_pages, keyword, queue, title_tags, progress=None):
    """
    生產者：負責翻頁、擷取清單資料並推入 Queue。
    progress(optional dict): 由 app.py 傳入,寫入 current 反映目前頁數。
    """
    for p_num in range(1, total_pages + 1):
        if progress is not None:
            progress["current"] = p_num
        print(f"\n[Producer] 🚀 正在處理第 {p_num}/{total_pages} 頁...")

        # 構建分頁 URL
        base_url = "https://www.104.com.tw/jobs/search/"
        q_str = f"?keyword={keyword}&jobcat=2007000000&page={p_num}"
        target_url = base_url + q_str

        if not await _goto_with_retry(page, target_url):
            print(f"[Producer] ❌ 第 {p_num} 頁多次重試仍失敗,跳過。")
            continue

        # 1. 確保職務標籤已掛載
        try:
            await page.wait_for_selector(".info-job__text", timeout=10000)
            await page.evaluate(f"window.scrollBy(0, {random.randint(300, 600)})")
            await asyncio.sleep(random.uniform(1.2, 2.5))
        except Exception:
            print(f"[Producer] ⚠️ 第 {p_num} 頁載入異常，繼續...")

        # 2. 擷取數據
        js_code = """() => {
            const sels = '.info-job__text';
            const titles = Array.from(document.querySelectorAll(sels));
            return titles.map(titleEl => {
                let card = titleEl.parentElement;
                let limit = 0;
                while (card &&
                       !card.querySelector('.info-company__text') &&
                       limit < 10) {
                    card = card.parentElement;
                    limit++;
                    if (card && card.classList.contains('job-list-container')) break;
                }
                if (!card) return null;
                const compEl = card.querySelector('.info-company__text');
                const tBox = card.querySelector('.info-tags');
                const tags = tBox ?
                    Array.from(tBox.querySelectorAll('.info-tags__text')) : [];
                return {
                    title: titleEl.getAttribute('title') || titleEl.innerText,
                    job_link: titleEl.getAttribute('href'),
                    company_name: compEl ? compEl.getAttribute('title') : "",
                    company_link: compEl ? compEl.getAttribute('href') : "",
                    salary_text: tags.length > 0 ?
                        tags[tags.length - 1].innerText : "面議"
                };
            }).filter(item => item !== null);
        }"""
        jobs_data = await page.evaluate(js_code)

        count = 0
        for data in jobs_data:
            title = (data["title"] or "").strip()
            # 使用動態傳入的 title_tags 進行初步過濾
            if not any(tag.lower() in title.lower() for tag in title_tags):
                continue

            j_link = data["job_link"] or ""
            c_link = data["company_link"] or ""
            job_url = "https:" + j_link if j_link.startswith("//") else j_link
            comp_url = "https:" + c_link if c_link.startswith("//") else c_link

            payload = {
                "title": title,
                "company_name": data["company_name"],
                "company_link": comp_url,
                "job_link": job_url,
                "salary_text": data["salary_text"],
                "keyword": keyword,
            }
            await queue.put(payload)
            count += 1

        print(f"[Producer] ✅ 第 {p_num} 頁完成，符合 {count} 筆。")
        await asyncio.sleep(random.uniform(1.5, 3.0))

    await queue.put(None)


async def consumer(queue):
    """
    消費者：負責將資料寫入 PostgreSQL
    """
    try:
        conn = await asyncpg.connect(
            host=os.getenv("DB_HOST", "127.0.0.1"),
            port=int(os.getenv("DB_PORT", 5434)),
            user=os.getenv("DB_USERNAME"),
            password=os.getenv("DB_PASSWORD"),
            database=os.getenv("DB_DATABASE"),
            server_settings={"timezone": "+08:00"},
        )
    except Exception as e:
        print(f"[Consumer] ❌ 連線失敗: {e}")
        return

    try:
        while True:
            data = await queue.get()
            if data is None:
                await queue.put(None)
                break

            try:
                # PG 用 ON CONFLICT 取代 MySQL 的 ON DUPLICATE KEY UPDATE
                # job_link 上有 UNIQUE 約束(uk_job_link),衝突時把 status 拉回 active
                sql = """
                INSERT INTO vacancies
                (title, company_name, company_link, job_link,
                 salary_text, keyword, status)
                VALUES ($1, $2, $3, $4, $5, $6, 'active')
                ON CONFLICT (job_link) DO UPDATE SET status = 'active'
                """
                await conn.execute(
                    sql,
                    data["title"],
                    data["company_name"],
                    data["company_link"],
                    data["job_link"],
                    data["salary_text"],
                    data["keyword"],
                )
            except Exception as e:
                print(f"[Consumer] 寫入失敗: {e}")
            queue.task_done()
    finally:
        await conn.close()


async def run_list_scraper(keyword=None, title_tags=None, progress=None):
    """
    啟動任務，接受 API 傳入的關鍵字與篩選標籤。
    progress(optional dict): app.py 傳入,本 stage 會寫入 total=末頁數、current=目前頁。
    """
    kw = keyword or os.getenv("DEFAULT_KEYWORD", "php")
    tags = title_tags or ["php", "PHP", "軟體", "資訊", "後端"]
    hl = os.getenv("BROWSER_HEADLESS", "false").lower() == "true"
    # USE_REAL_CHROME=true 要求宿主裝有 Google Chrome (channel="chrome")
    # Docker 環境通常只有 bundled Chromium → 設 false
    use_real_chrome = os.getenv("USE_REAL_CHROME", "true").lower() == "true"

    async with Stealth().use_async(async_playwright()) as p:
        launch_kwargs = {
            "headless": hl,
            "args": [
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--no-sandbox",
            ],
        }
        if use_real_chrome:
            launch_kwargs["channel"] = "chrome"
        browser = await p.chromium.launch(**launch_kwargs)
        context = await browser.new_context(
            user_agent=random.choice(USER_AGENTS),
            viewport={
                "width": random.choice([1280, 1366, 1440, 1536, 1680, 1920]),
                "height": random.choice([720, 800, 900, 1080]),
            },
            locale="zh-TW",
            timezone_id="Asia/Taipei",
        )
        await _register_resource_blocker(context)
        page = await context.new_page()
        try:
            print(f"[Stage A] 初始化搜尋: {kw}...")
            base = "https://www.104.com.tw/jobs/search/"
            initial_url = base + "?keyword=" + kw + "&jobcat=2007000000"
            if not await _goto_with_retry(page, initial_url):
                raise RuntimeError("初始頁面多次重試仍無法通過 CF 挑戰")
            await page.wait_for_selector(".job-list-container", timeout=15000)

            # 嘗試翻到最後一頁來獲取總頁數
            await page.fill(".go-page__input input", "999")
            await page.keyboard.press("Enter")
            await asyncio.sleep(random.uniform(2, 3.5))

            total = 1
            page_match = re.search(r"page=(\d+)", page.url)
            if page_match:
                total = int(page_match.group(1))
            print(f"[Stage A] 探測完成，共計 {total} 頁。")

            if progress is not None:
                progress["total"] = total

            q = asyncio.Queue()
            workers = [consumer(q) for _ in range(3)]
            await asyncio.gather(
                producer(page, total, kw, q, tags, progress=progress),
                *workers,
            )
        except Exception as e:
            print(f"[Stage A] 異常: {e}")
            raise  # 由 app.py orchestrator 捕捉並寫入 progress.error
        finally:
            await browser.close()
            print("[Stage A] 系統關閉。")


if __name__ == "__main__":
    asyncio.run(run_list_scraper())
