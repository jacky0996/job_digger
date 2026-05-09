"""
手動測試用：開一個套了 stealth 的 Chromium,留給人類手動操作。

跑法(在專案根目錄):
    python tests/manual_stealth_probe.py

預期:
  - 開兩個分頁
    1) bot.sannysoft.com  → 看指紋偵測欄位是否全綠
    2) 104 搜尋頁          → 自由點擊職缺,觀察是否還跳機器人驗證
  - 關掉視窗或在終端按 Ctrl+C 結束。
"""

import asyncio
import random

from playwright.async_api import async_playwright
from playwright_stealth import Stealth

USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
]


async def main():
    async with Stealth().use_async(async_playwright()) as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(
            user_agent=random.choice(USER_AGENTS),
            viewport={
                "width": random.choice([1366, 1440, 1536, 1680]),
                "height": random.choice([800, 900, 1000]),
            },
            locale="zh-TW",
            timezone_id="Asia/Taipei",
        )

        # 分頁 1:指紋對照組
        probe = await context.new_page()
        await probe.goto("https://bot.sannysoft.com/")

        # 分頁 2:104 搜尋頁,你接手操作
        target = await context.new_page()
        await target.goto(
            "https://www.104.com.tw/jobs/search/?keyword=php",
            wait_until="domcontentloaded",
        )
        await target.bring_to_front()

        print("\n================ 手動測試模式 ================")
        print("分頁 1 (sannysoft):看指紋欄位有沒有全綠")
        print("分頁 2 (104):自由點職缺,觀察有沒有跳驗證")
        print("結束:關掉瀏覽器視窗,或在這裡 Ctrl+C")
        print("===============================================\n")

        # 等使用者關掉瀏覽器
        try:
            await browser.wait_for_event("disconnected", timeout=0)
        except Exception:
            pass


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 已退出")
