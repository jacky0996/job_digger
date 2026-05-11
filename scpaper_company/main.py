"""Stage C — 透過 104 公開 API 補全公司資本額與員工人數。

API 路徑:GET https://www.104.com.tw/api/companies/{company_no}/content
回傳 JSON 中我們只取兩個欄位:
  - data.capital → vacancies.capital      (例: "6000萬元")
  - data.empNo   → vacancies.employee_count (例: "16人")

格式跟原本 Playwright 從頁面抽出來的字串完全一致,所以 DB 不會混兩種格式。

架構:Producer / Worker / Writer
  - Producer: 一次撈出待補公司清單
  - N 個 Worker(預設 5)平行打 API + 比對欄位
  - 單一 Writer 負責 DB UPDATE + 進度計數,避開 aiomysql 連線無法併發的問題
"""

import asyncio
import os
import re
import time

import aiomysql
import httpx
from dotenv import load_dotenv

load_dotenv()

# 平行度與時間參數(env 可調)
STAGE_C_WORKERS = int(os.getenv("STAGE_C_WORKERS", 5))
STAGE_C_REQUEST_DELAY = float(os.getenv("STAGE_C_REQUEST_DELAY", 0.3))
STAGE_C_TIMEOUT = float(os.getenv("STAGE_C_TIMEOUT", 10))

# 預設值(沿用原本 Playwright 失敗時的回傳)
_DEFAULT_CAPITAL = "0"
_DEFAULT_EMPLOYEES = ""

# 從 /company/{no}?... 抽流水碼
_COMPANY_NO_RE = re.compile(r"/company/([^/?#]+)")

_API_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
}

# Sentinel:Worker 通知 Writer 結束
_WORKER_DONE = object()


def _extract_company_no(url: str) -> str | None:
    """從 company_link 抽出流水碼。失敗回傳 None。"""
    if not url:
        return None
    m = _COMPANY_NO_RE.search(url)
    return m.group(1) if m else None


async def _fetch_company_info(
    client: httpx.AsyncClient, company_no: str
) -> tuple[str, str] | None:
    """打 API 拿 (capital, employees)。失敗回傳 None(代表跳過,不寫 DB)。

    成功但 API 沒提供欄位 → 回傳 default 值(寫進 DB)。
    """
    url = f"https://www.104.com.tw/api/companies/{company_no}/content"
    referer = f"https://www.104.com.tw/company/{company_no}"
    try:
        resp = await client.get(url, headers={**_API_HEADERS, "Referer": referer})
    except (httpx.HTTPError, asyncio.TimeoutError) as e:
        print(f"  [Stage C] ⚠️ 連線失敗 (company_no={company_no}): {e}")
        return None

    if resp.status_code in (403, 429):
        # 退避 30 秒重試一次
        print(
            f"  [Stage C] ⚠️ HTTP {resp.status_code} (company_no={company_no}), 30s 重試"
        )
        await asyncio.sleep(30)
        try:
            resp = await client.get(url, headers={**_API_HEADERS, "Referer": referer})
        except (httpx.HTTPError, asyncio.TimeoutError) as e:
            print(f"  [Stage C] ❌ 重試仍失敗: {e}")
            return None

    if resp.status_code == 404:
        # 公司可能下架。寫 default 值,避免下次 SELECT 又撈到無限重試。
        print(f"  [Stage C] ⚠️ HTTP 404 (company_no={company_no}),寫入 default")
        return (_DEFAULT_CAPITAL, _DEFAULT_EMPLOYEES)

    if resp.status_code != 200:
        print(f"  [Stage C] ❌ HTTP {resp.status_code} (company_no={company_no})")
        return None

    try:
        payload = resp.json()
    except ValueError:
        print(f"  [Stage C] ❌ JSON 解析失敗 (company_no={company_no})")
        return None

    data = payload.get("data") or {}
    capital = data.get("capital") or _DEFAULT_CAPITAL
    employees = data.get("empNo") or _DEFAULT_EMPLOYEES
    return (capital, employees)


async def _worker(
    name: str,
    in_queue: asyncio.Queue,
    out_queue: asyncio.Queue,
    client: httpx.AsyncClient,
):
    """從 in_queue 拿公司,API 查完丟進 out_queue。"""
    while True:
        company = await in_queue.get()
        if company is _WORKER_DONE:
            in_queue.task_done()
            await out_queue.put(_WORKER_DONE)
            return

        c_name = company["company_name"]
        c_url = company["company_link"]
        company_no = _extract_company_no(c_url)

        if not company_no:
            print(f"  [Stage C] ⏭ 略過(抽不到 company_no): {c_name}")
            in_queue.task_done()
            continue

        result = await _fetch_company_info(client, company_no)
        if result is not None:
            capital, employees = result
            await out_queue.put(
                {
                    "company_link": c_url,
                    "company_name": c_name,
                    "capital": capital,
                    "employees": employees,
                }
            )

        await asyncio.sleep(STAGE_C_REQUEST_DELAY)
        in_queue.task_done()


async def _writer(
    out_queue: asyncio.Queue,
    cur,
    progress: dict | None,
    expected_done: int,
    total: int,
):
    """收 worker 結果寫 DB。expected_done 是 worker 數,用來判斷全結束。"""
    done_workers = 0
    processed = 0
    summary_every = max(10, total // 10)

    while True:
        item = await out_queue.get()
        if item is _WORKER_DONE:
            done_workers += 1
            out_queue.task_done()
            if done_workers >= expected_done:
                return
            continue

        try:
            await cur.execute(
                "UPDATE vacancies SET capital = %s, employee_count = %s "
                "WHERE company_link = %s",
                (item["capital"], item["employees"], item["company_link"]),
            )
            processed += 1
            if progress is not None:
                progress["current"] = processed
            print(
                f"[Stage C {processed:>3}/{total}] ✓ "
                f"資本={item['capital']:<10} 人數={item['employees']:<8} "
                f"{item['company_name']}"
            )
            if processed % summary_every == 0 and processed < total:
                pct = processed * 100 // total
                print(f"  ── 進度 {processed}/{total} ({pct}%)")
        except Exception as e:
            print(f"  [Stage C] ❌ DB 寫入失敗 ({item['company_name']}): {e}")
        finally:
            out_queue.task_done()


async def run_company_scraper(keyword=None, progress=None):
    """主排程:從 DB 找出缺少資料的公司,打 104 API 補全。

    progress(optional dict): 寫入 total=待補公司數, current=已處理數。
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
        print(f"[Stage C] ❌ 資料庫連線失敗: {e}")
        return

    try:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            sql_fetch = (
                "SELECT company_name, company_link FROM vacancies "
                "WHERE keyword = %s AND (capital = '0' OR employee_count = '') "
                "AND company_link != '' GROUP BY company_link"
            )
            await cur.execute(sql_fetch, (kw,))
            companies = await cur.fetchall()

            if not companies:
                print(f"[Stage C] 🎉 '{kw}' 相關公司的資料都已經補齊囉！")
                return

            total = len(companies)
            started = time.time()
            print("=" * 60)
            print("[Stage C] 🚀 公司資料補全啟動")
            print(f"          keyword: {kw}")
            print(f"          待補:    {total} 家")
            print(f"          workers: {STAGE_C_WORKERS}")
            print("=" * 60)
            if progress is not None:
                progress["total"] = total

            in_queue: asyncio.Queue = asyncio.Queue()
            out_queue: asyncio.Queue = asyncio.Queue()

            for c in companies:
                await in_queue.put(c)
            for _ in range(STAGE_C_WORKERS):
                await in_queue.put(_WORKER_DONE)

            limits = httpx.Limits(
                max_connections=STAGE_C_WORKERS,
                max_keepalive_connections=STAGE_C_WORKERS,
            )
            async with httpx.AsyncClient(
                timeout=STAGE_C_TIMEOUT, limits=limits
            ) as client:
                workers = [
                    asyncio.create_task(_worker(f"w{i}", in_queue, out_queue, client))
                    for i in range(STAGE_C_WORKERS)
                ]
                writer = asyncio.create_task(
                    _writer(
                        out_queue,
                        cur,
                        progress,
                        expected_done=STAGE_C_WORKERS,
                        total=total,
                    )
                )
                await asyncio.gather(*workers)
                await writer

            elapsed = time.time() - started
            print("=" * 60)
            print(
                f"[Stage C] ✅ 完成 — 耗時 {elapsed:.1f}s({total/max(elapsed, 0.1):.1f} 家/秒)"
            )
            print("=" * 60)
    finally:
        conn.close()


if __name__ == "__main__":
    asyncio.run(run_company_scraper())
