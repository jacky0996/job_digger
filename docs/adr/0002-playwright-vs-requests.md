# ADR-0002: 採用 Playwright 而非 requests + BeautifulSoup

- **狀態**: Partially Superseded by [ADR-0005](./0005-stage-bc-switch-to-104-api.md)(2026-05-11)
- **日期**: 2026-04-22
- **決策者**: Shane (SA / 開發者)

> **更新註記(2026-05-11)**:本決策當初涵蓋三個 stage。實際運作後發現 104 對 Stage B(內文)/ Stage C(公司)有可匿名訪問的公開 JSON API,改用 `httpx` 直打後速度提升 10-30 倍。**Stage B / C 已 supersede 改用 API 模式**(見 [ADR-0005](./0005-stage-bc-switch-to-104-api.md));**Stage A(清單)仍維持 Playwright**,因為翻頁與職務類別 modal 互動依賴 JS 渲染,且還沒找到對應的搜尋 API。下文敘述對 Stage A 仍完整適用。

## Context — 我們在解決什麼問題?

爬蟲的核心抉擇:**用 HTTP requests + HTML parser**,還是 **用 headless browser**。對 104 來說:

- 列表頁是 **SPA(Single Page App)** — 內容透過 JS 渲染,直接 `requests.get` 拿到的 HTML 沒職缺
- 有 **lazy loading** — 捲動才載入更多
- 有反爬機制 — 直接帶 `requests` 的 default User-Agent 會被擋
- 互動需求 — 點開「職務類別」modal 選「資訊軟體系統類」,必須模擬 click

選哪個影響:能不能爬到資料、被 ban 機率、效能、維護成本。

## Decision — 我們選了什麼?

**採 Playwright(Chromium) + playwright-stealth**:

- 用真實的 Chromium 瀏覽器跑(不是模擬,是真的瀏覽器)
- `page.evaluate` 跑客戶端 JS 批次抽資料(比 Python BeautifulSoup parse HTML 快很多)
- `playwright-stealth` 改寫 fingerprint,降低被偵測機率
- 模擬人類行為:輸入 → 點擊 → 等待

## Considered Options — 還評估過哪些?

### 選項 1 — requests + BeautifulSoup

- ✅ 輕量(無瀏覽器)、快、記憶體少
- ✅ 容易 retry / 並發(asyncio.gather 開 100 個)
- ❌ **104 是 SPA,直接 GET 拿不到內容** — 致命缺點
- ❌ 反爬偵測容易(headers 簡單)
- ❌ 不能模擬 click / scroll

> 對「靜態 HTML 的網站」這是最佳解,但 104 不是。

### 選項 2 — Selenium

- ✅ 也能跑真實瀏覽器
- ✅ 老牌,生態成熟
- ❌ Sync API,跟 FastAPI async 不搭
- ❌ Webdriver protocol 老舊,效能比 Playwright 差
- ❌ Stealth 支援沒 Playwright 好(undetected-chromedriver 是 hack)

### 選項 3 — Playwright【選中】

- ✅ Async API,跟 FastAPI / aiomysql 完整 async chain
- ✅ Microsoft 官方,持續更新
- ✅ Stealth 支援好(`playwright-stealth` 套件)
- ✅ `page.evaluate` 直接跑 JS,抽資料快又準
- ✅ 內建 lazy-load 處理(`page.scroll_into_view_if_needed`)
- ⚠ Image 大(~150MB Chromium)
- ⚠ 記憶體比 requests 大很多(每個 page ~100MB)

### 選項 4 — Splash / Pyppeteer

- ✅ 輕量
- ❌ Pyppeteer 已不維護,Playwright 就是它的繼承者
- ❌ Splash 是獨立服務,部署複雜

### 選項 5 — 直接打 104 內部 API

- ✅ 最快、最乾淨
- ❌ 104 不公開內部 API,逆向工程後可能因為改版隨時失效
- ❌ 違反 ToS 風險高

## Consequences — 這個決定帶來什麼?

### ✅ 正面

- **能爬 SPA**:104 的 JS 渲染、lazy loading、modal 互動都搞得定
- **反爬能力強**:Stealth + 真實瀏覽器,被當機器人擋的機率低
- **抽資料快**:`page.evaluate` 在瀏覽器端跑 JS 批次抽,比 BeautifulSoup parse 整頁 HTML 快
- **不必逆向 API**:直接從 UI 抽,104 改版只要 UI 還在就能爬
- **錨點回溯支援**:`page.locator('.info-job__text').locator('xpath=ancestor::...').first` 這種彈性 selector

### ⚠ 負面 / Trade-off

- **慢**(相對 requests):啟動瀏覽器 + 等 JS 渲染 + 模擬點擊,單頁 ~3-5 秒。緩解:
  - 用 producer-consumer 並發抓
  - 末頁探測 hack 避免無謂翻頁

- **吃資源**:單個 Playwright instance ~200MB,跑爬蟲時 host 記憶體壓力大。緩解:
  - 用完即關(`await browser.close()`)
  - 不開 multiple browsers,單瀏覽器多 page

- **Image 大**:Chromium ~150MB 進 Docker image。緩解:
  - 接受(這就是真實瀏覽器爬蟲的代價)
  - Docker layer cache 後 incremental build OK

- **104 改版可能要修**:UI 改 selector 就壞。緩解:
  - 錨點回溯(從穩定的 `.info-job__text` 往上找)
  - 加 integration test 早期發現

### 🔁 後續追蹤

- 監控被 ban 頻率,若提高評估加 IP rotation
- 評估 `playwright-stealth` 升級
- 若 104 真的封死(captcha 等),評估 anti-captcha service 或人工介入

## References

- Code:
  - `scraper_vacancies/main.py` — Stage A 用 Playwright(仍適用本 ADR)
  - `scpaper_content/main.py` — Stage B(改 API,見 ADR-0005)
  - `scpaper_company/main.py` — Stage C(改 API,見 ADR-0005)
- 文件:
  - [`docs/sequence-diagrams.md` 第 1 節](../sequence-diagrams.md#1-stage-a-清單採集-producer-consumer) — Stage A 詳細流程
  - [`docs/sequence-diagrams.md` 第 5 節](../sequence-diagrams.md#5-反爬策略總覽) — 反爬手段彙整
- 外部:
  - [Playwright 官方](https://playwright.dev/python/)
  - [playwright-stealth](https://github.com/AtuboDad/playwright_stealth)
  - [104 ToS](https://www.104.com.tw/area/legal/) — 爬蟲合規邊界
