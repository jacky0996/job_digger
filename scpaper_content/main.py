"""Stage B — 透過 104 公開 API 比對職缺內文,寫入 check_type 標籤。

API 路徑:GET https://www.104.com.tw/api/jobs/{job_no}

判定優先序(first match wins,沿用 Playwright 版邏輯,確保 check_type 字串完全相容):
  1. data.jobDetail.jobDescription                 → 「工作內容有含關鍵字」
  2. data.condition.other                          → 「加分條件或必要條件內有含關鍵字」
  3. data.condition.specialty[].description (串接)  → 「僅有擅長工具含關鍵字,建議確認後再進行履歷投遞」
  4. 都沒中                                         → 「no_match」

比對規則:把文字 strip HTML + 解 entity + lowercase + 去空白後做 substring,
跟原本 Playwright JS 的 containsTag 邏輯一致(只是處理移到 Python 端)。

架構:Producer / Worker / Writer
  - 同 Stage C,只是工作內容不同。
"""

import asyncio
import html
import os
import re
import time
from datetime import datetime

import aiomysql
import httpx
from dotenv import load_dotenv

load_dotenv()

# 平行度與時間參數(env 可調)
STAGE_B_WORKERS = int(os.getenv("STAGE_B_WORKERS", 5))
STAGE_B_REQUEST_DELAY = float(os.getenv("STAGE_B_REQUEST_DELAY", 0.3))
STAGE_B_TIMEOUT = float(os.getenv("STAGE_B_TIMEOUT", 10))

# 從 /job/{no}?... 抽流水碼
_JOB_NO_RE = re.compile(r"/job/([^/?#]+)")

# HTML 處理 — block-level 標籤替換為換行,避免 substring 比對時把多個欄位連在一起
_BLOCK_TAGS_RE = re.compile(
    r"</?(?:br|p|div|li|ul|ol|h[1-6]|tr|td|th)\b[^>]*>", re.IGNORECASE
)
_ANY_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")

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


def _extract_job_no(url: str) -> str | None:
    if not url:
        return None
    m = _JOB_NO_RE.search(url)
    return m.group(1) if m else None


def _strip_html(s: str) -> str:
    """把 HTML 字串轉成可比對的純文字。block-level 標籤先換成空格防止文字粘連。"""
    if not s:
        return ""
    s = _BLOCK_TAGS_RE.sub(" ", s)
    s = _ANY_TAG_RE.sub("", s)
    return html.unescape(s)


def _normalize(s: str) -> str:
    """全部 lowercase + 去空白,讓 substring 比對忽略大小寫與排版。"""
    return _WHITESPACE_RE.sub("", s.lower())


def _contains_any(text: str, tags: list[str]) -> bool:
    if not text or not tags:
        return False
    norm = _normalize(text)
    return any(_normalize(tag) in norm for tag in tags if tag)


def _classify(api_data: dict, content_tags: list[str]) -> str:
    """依優先序檢查 API 三個欄位,回傳對應的 check_type 字串。"""
    # 1. 工作內容
    job_desc = (api_data.get("jobDetail") or {}).get("jobDescription") or ""
    if _contains_any(_strip_html(job_desc), content_tags):
        return "工作內容有含關鍵字"

    condition = api_data.get("condition") or {}

    # 2. 其他條件(加分條件 / 必要條件)
    other = condition.get("other") or ""
    if _contains_any(_strip_html(other), content_tags):
        return "加分條件或必要條件內有含關鍵字"

    # 3. 擅長工具:把 specialty 陣列的 description 串成字串
    specialty = condition.get("specialty") or []
    if isinstance(specialty, list):
        specialty_text = " ".join(
            (item.get("description") or "")
            for item in specialty
            if isinstance(item, dict)
        )
        if _contains_any(specialty_text, content_tags):
            return "僅有擅長工具含關鍵字,建議確認後再進行履歷投遞"

    return "no_match"


async def _fetch_job(client: httpx.AsyncClient, job_no: str) -> tuple[int, dict | None]:
    """打 API 拿 (status_code, data dict)。錯誤回傳 (status_code or 0, None)。"""
    url = f"https://www.104.com.tw/api/jobs/{job_no}"
    referer = f"https://www.104.com.tw/job/{job_no}"
    try:
        resp = await client.get(url, headers={**_API_HEADERS, "Referer": referer})
    except (httpx.HTTPError, asyncio.TimeoutError):
        return (0, None)

    if resp.status_code in (403, 429):
        await asyncio.sleep(30)
        try:
            resp = await client.get(url, headers={**_API_HEADERS, "Referer": referer})
        except (httpx.HTTPError, asyncio.TimeoutError):
            return (0, None)

    if resp.status_code != 200:
        return (resp.status_code, None)

    try:
        payload = resp.json()
    except ValueError:
        return (200, None)

    return (200, payload.get("data") or {})


async def _worker(
    in_queue: asyncio.Queue,
    out_queue: asyncio.Queue,
    client: httpx.AsyncClient,
    content_tags: list[str],
):
    while True:
        job = await in_queue.get()
        if job is _WORKER_DONE:
            in_queue.task_done()
            await out_queue.put(_WORKER_DONE)
            return

        job_no = _extract_job_no(job["job_link"])
        if not job_no:
            print(f"  [Stage B] ⏭ 略過(抽不到 job_no): id={job['id']}")
            in_queue.task_done()
            continue

        status, data = await _fetch_job(client, job_no)

        if status == 404:
            await out_queue.put(
                {"id": job["id"], "title": job["title"], "kind": "closed"}
            )
        elif data is not None:
            check_type = _classify(data, content_tags)
            await out_queue.put(
                {
                    "id": job["id"],
                    "title": job["title"],
                    "kind": "check",
                    "check_type": check_type,
                }
            )
        else:
            # 連線失敗 / 非 200 / JSON 壞:不寫 DB,下次再試
            print(f"  [Stage B] ⚠️ 跳過 (id={job['id']}, status={status})")

        await asyncio.sleep(STAGE_B_REQUEST_DELAY)
        in_queue.task_done()


_STATS_LABEL_SHORT = {
    "工作內容有含關鍵字": "工作內容",
    "加分條件或必要條件內有含關鍵字": "加分條件",
    "僅有擅長工具含關鍵字,建議確認後再進行履歷投遞": "擅長工具",
    "no_match": "no_match",
}


async def _writer(
    out_queue: asyncio.Queue,
    cur,
    progress: dict | None,
    expected_done: int,
    total: int,
):
    done_workers = 0
    processed = 0
    counts: dict[str, int] = {v: 0 for v in _STATS_LABEL_SHORT.values()}
    counts["closed"] = 0

    # 每 N 筆印一次進度小結;太小很吵、太大看不到進展
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
            if item["kind"] == "closed":
                now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                await cur.execute(
                    "UPDATE vacancies SET status = 'closed', deleted_at = %s "
                    "WHERE id = %s",
                    (now, item["id"]),
                )
                processed += 1
                counts["closed"] += 1
                print(
                    f"[Stage B {processed:>3}/{total}] 🚪 已下架  "
                    f"id={item['id']} ({item['title']})"
                )
            else:
                await cur.execute(
                    "UPDATE vacancies SET check_type = %s WHERE id = %s",
                    (item["check_type"], item["id"]),
                )
                processed += 1
                short = _STATS_LABEL_SHORT.get(item["check_type"], item["check_type"])
                counts[short] = counts.get(short, 0) + 1
                marker = "✓" if short != "no_match" else "·"
                print(
                    f"[Stage B {processed:>3}/{total}] {marker} {short:<8} "
                    f"id={item['id']} ({item['title']})"
                )

            if progress is not None:
                progress["current"] = processed

            if processed % summary_every == 0 and processed < total:
                pct = processed * 100 // total
                print(
                    f"  ── 進度 {processed}/{total} ({pct}%) | "
                    f"工作內容={counts['工作內容']} 加分條件={counts['加分條件']} "
                    f"擅長工具={counts['擅長工具']} no_match={counts['no_match']} "
                    f"已下架={counts['closed']}"
                )
        except Exception as e:
            print(f"  [Stage B] ❌ DB 寫入失敗 (id={item['id']}): {e}")
        finally:
            out_queue.task_done()


async def run_content_scraper(keyword, content_tags, progress=None):
    """主排程:撈出待檢查職缺,打 104 API 比對 content_tags,寫入 check_type。

    progress(optional dict): 寫入 total=待檢查職缺數, current=已處理數。
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
    except Exception as e:
        print(f"[Stage B] ❌ 資料庫連線失敗: {e}")
        return

    try:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            # 增量爬取:沒處理過,或上次偵測逾時的(沿用舊邏輯)
            sql = (
                "SELECT id, job_link, title FROM vacancies "
                "WHERE status = 'active' AND keyword = %s "
                "AND (check_type IS NULL OR check_type = '偵測逾時，建議手動確認')"
            )
            await cur.execute(sql, (kw,))
            jobs = await cur.fetchall()

            if not jobs:
                print(f"[Stage B] 🎉 '{kw}' 內文資料都已處理完畢")
                return

            total = len(jobs)
            started = time.time()
            print("=" * 60)
            print("[Stage B] 🚀 內文比對啟動")
            print(f"          keyword: {kw}")
            print(f"          tags:    {content_tags}")
            print(f"          待檢查:  {total} 筆")
            print(f"          workers: {STAGE_B_WORKERS}")
            print("=" * 60)
            if progress is not None:
                progress["total"] = total

            in_queue: asyncio.Queue = asyncio.Queue()
            out_queue: asyncio.Queue = asyncio.Queue()

            for j in jobs:
                await in_queue.put(j)
            for _ in range(STAGE_B_WORKERS):
                await in_queue.put(_WORKER_DONE)

            limits = httpx.Limits(
                max_connections=STAGE_B_WORKERS,
                max_keepalive_connections=STAGE_B_WORKERS,
            )
            async with httpx.AsyncClient(
                timeout=STAGE_B_TIMEOUT, limits=limits
            ) as client:
                workers = [
                    asyncio.create_task(
                        _worker(in_queue, out_queue, client, content_tags)
                    )
                    for _ in range(STAGE_B_WORKERS)
                ]
                writer = asyncio.create_task(
                    _writer(
                        out_queue,
                        cur,
                        progress,
                        expected_done=STAGE_B_WORKERS,
                        total=total,
                    )
                )
                await asyncio.gather(*workers)
                await writer

            elapsed = time.time() - started
            print("=" * 60)
            print(
                f"[Stage B] ✅ 完成 — 耗時 {elapsed:.1f}s({total/max(elapsed, 0.1):.1f} 筆/秒)"
            )
            print("=" * 60)
    finally:
        conn.close()


if __name__ == "__main__":
    asyncio.run(
        run_content_scraper(
            keyword=os.getenv("DEFAULT_KEYWORD", "php"), content_tags=["php", "laravel"]
        )
    )
