import asyncio
import os
import re

from dotenv import load_dotenv

load_dotenv()


async def producer(page, total_pages, keyword, queue):
    """
    生產者：負責翻頁、擷取清單資料並推入 Queue
    """
    for p_num in range(1, total_pages + 1):
        print(f"\n[Producer] 🚀 正在處理第 {p_num}/{total_pages} 頁...")

        # 構建分頁 URL
        base_url = "https://www.104.com.tw/jobs/search/"
        q_str = f"?keyword={keyword}&jobcat=2007000000&page={p_num}"
        target_url = base_url + q_str

        await page.goto(target_url, wait_until="load")

        # 1. 確保職務標籤已掛載
        try:
            await page.wait_for_selector(".info-job__text", timeout=10000)
            await page.evaluate("window.scrollBy(0, 400)")  # 觸發滾動
            await asyncio.sleep(1.5)
        except Exception:
            print(f"[Producer] ⚠️ 第 {p_num} 頁載入異常，繼續...")

        # 2. 以「職稱標籤」為錨點向上回溯擷取數據
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
                    if (card &&
                        card.classList.contains('job-list-container')) break;
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
        print(f"[Producer] 本頁偵測到 {len(jobs_data)} 個潛在項目...")

        filter_tags = ["php", "PHP", "軟體", "資訊", "後端"]
        count = 0
        for data in jobs_data:
            title = (data["title"] or "").strip()
            if not any(tag in title for tag in filter_tags):
                continue

            # 處理網址補全 (避開 f-string 冒號誤判)
            j_link = data["job_link"] or ""
            c_link = data["company_link"] or ""
            job_url = "https:" + j_link if j_link.startswith("//") else j_link
            comp_url = "https:" + c_link if c_link.startswith("//") else c_link

            # 推入數據流
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
        await asyncio.sleep(1)

    await queue.put(None)


async def consumer(queue):
    """
    消費者：負責將資料寫入 MariaDB
    """
    import aiomysql

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
        print(f"[Consumer] ❌ 連線失敗: {e}")
        return

    async with conn.cursor() as cur:
        while True:
            data = await queue.get()
            if data is None:
                await queue.put(None)
                break

            try:
                sql = """
                INSERT INTO vacancies
                (title, company_name, company_link, job_link,
                 salary_text, keyword, status)
                VALUES (%s, %s, %s, %s, %s, %s, 'active')
                ON DUPLICATE KEY UPDATE status = 'active'
                """
                args = (
                    data["title"],
                    data["company_name"],
                    data["company_link"],
                    data["job_link"],
                    data["salary_text"],
                    data["keyword"],
                )
                await cur.execute(sql, args)
            except Exception as e:
                print(f"[Consumer] 寫入失敗: {e}")
            queue.task_done()
    conn.close()


async def run_list_scraper():
    """
    啟動任務
    """
    from playwright.async_api import async_playwright

    kw = os.getenv("DEFAULT_KEYWORD", "php")
    hl = os.getenv("BROWSER_HEADLESS", "false").lower() == "true"

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=hl)
        context = await browser.new_context()
        page = await context.new_page()
        try:
            print(f"[Stage A] 初始化搜尋: {kw}...")
            base = "https://www.104.com.tw/jobs/search/"
            await page.goto(base + "?keyword=" + kw + "&jobcat=2007000000")
            await page.wait_for_selector(".job-list-container")

            await page.fill(".go-page__input input", "9999")
            await page.keyboard.press("Enter")
            await asyncio.sleep(3)

            total = 1
            page_match = re.search(r"page=(\d+)", page.url)
            if page_match:
                total = int(page_match.group(1))
            print(f"[Stage A] 探測完成，共計 {total} 頁。")

            q = asyncio.Queue()
            workers = [consumer(q) for _ in range(3)]
            await asyncio.gather(
                producer(page, total, kw, q),
                *workers,
            )
        except Exception as e:
            print(f"[Stage A] 異常: {e}")
        finally:
            await browser.close()
            print("[Stage A] 系統關閉。")


if __name__ == "__main__":
    asyncio.run(run_list_scraper())
