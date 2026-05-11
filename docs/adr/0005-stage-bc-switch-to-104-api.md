# ADR-0005: Stage B / C 改走 104 公開 API,放棄 Playwright

- **狀態**: Accepted
- **日期**: 2026-05-11
- **決策者**: Shane (SA / 開發者)
- **相關 ADR**: [0002 — Playwright vs requests](./0002-playwright-vs-requests.md)(此 ADR 對 Stage B/C 部分 supersede)

## Context — 我們在解決什麼問題?

ADR-0002 當時的結論是「104 列表頁是 SPA 必須走 Playwright」,因此三個 stage 都用 Chromium。實際運作後遇到以下痛點:

- **Stage B(內文)/ Stage C(公司)變得極慢**:單筆要 5-30 秒(含 lazy-load 等待、CF 挑戰、selector polling)
- **單一 keyword 跑整夜跑不完**:一個 `python` 關鍵字 200+ 職缺,從睡前跑到起床還沒結束
- **CF 挑戰偶爾觸發**:Stage B 有專屬 `_pass_cf_challenge` handler 等 30 秒,期間使用者無感
- **Chromium 吃記憶體**:同時跑時容器 RAM 飆到 1-2GB
- **平行化困難**:多開 chromium context 會被 IP 流量集中觸發更多 CF

進一步勘查發現 **104 對 SPA 渲染所需的後端 API 是公開可訪問的**:

- `GET https://www.104.com.tw/api/jobs/{job_no}` — 職缺內文 JSON
- `GET https://www.104.com.tw/api/companies/{company_no}/content` — 公司資料 JSON

兩個 endpoint 都:
- **匿名可打**(不需登入 / 不需 cookie / 不需 CSRF token)
- **單純 IP 流量管制**(門檻比 browser 寬很多)
- **回傳的欄位跟頁面顯示完全一致**(因為頁面就是吃這 API 渲染的)

## Decision — 我們選了什麼?

**Stage B 與 Stage C 全面改用 `httpx` 打 104 公開 API,完全脫離 Playwright。**

- Stage A(清單)**維持** Playwright — 還沒找到對應的搜尋 API,且翻頁與職務類別 modal 互動依賴 JS
- Stage B / C 重寫成 **Producer / Worker / Writer** 模式:
  - Producer:單一 `SELECT` 撈待處理項目進入 in-queue
  - N 個 Worker(預設 5,env `STAGE_B_WORKERS` / `STAGE_C_WORKERS`):平行打 API,每 request 後 `sleep 0.3s` 避開尖峰
  - 單一 Writer:從 out-queue 收結果寫 DB(避開 aiomysql connection 不可併發的限制)
- DB schema 不動、寫入欄位 / `check_type` 字串完全相容,**admin UI 與舊資料 100% 沿用**

## Considered Options — 還評估過哪些?

### 選項 1 — 繼續用 Playwright,加平行 worker

- ✅ 不必改 selector 邏輯
- ❌ 多 chromium context 撐爆記憶體
- ❌ 同 IP 流量集中 → CF 反而更頻繁
- ❌ 治標不治本(根因是 browser 本身就慢)

### 選項 2 — 接 2captcha 自動解 CF

- ✅ 解掉 CF 阻塞
- ❌ 要付費(雖然便宜),要對接 API
- ❌ 還是沒解決 browser 渲染慢的根本問題
- ❌ 多一個外部依賴

### 選項 3 — Hybrid:Playwright 取 cookie,後續用 httpx

- ✅ 若 API 需 cookie 才能打,這是 fallback 方案
- ❌ 實測 API **不需 cookie**,Hybrid 是過度設計

### 選項 4 — 全部改 API【選中(B/C)】

- ✅ 速度提升 10-30 倍(實測 200 筆從整夜降到 5 分鐘內)
- ✅ CF 觸發接近 0
- ✅ RAM 大幅降低(沒 chromium)
- ✅ 可放心平行化(5 workers 不會被 IP rate-limit)
- ⚠ 對 104 API 有依賴 — 若 104 改 API path 或加 auth,會壞
- ⚠ Stage A 暫時不適用(沒找到對應 API)

## Consequences — 這個決定帶來什麼?

### ✅ 正面

- **速度**:Stage B 從「整夜」降到「分鐘」,Stage C 從「8-25 分鐘」降到「30-60 秒」
- **資源**:單次 task 容器 RAM 從 ~1.5GB 降到 ~300MB(只剩 Stage A 還跑 chromium)
- **CF 不再是瓶頸**:API 端不會跳挑戰(SPA fingerprint 機制不對 JSON 端套用)
- **架構統一**:Stage B 與 Stage C 用同一套 Producer/Worker/Writer pattern,程式碼可讀性提升
- **依賴更乾淨**:Stage B 不再需要 `playwright-stealth`、`CONTEXT_ROTATE_EVERY` 等反偵測複雜度
- **使用者體驗**:admin 觸發後 5 分鐘內就能看到分類結果,以前要等隔天

### ⚠ 負面 / Trade-off

- **耦合 104 API 內部介面**:這是「非公開但事實上公開」的 endpoint,沒文件、沒版本承諾
  - 緩解:若 104 改 path / 加 auth,Stage B/C 大量 fail 會被 admin 看到,**容易發現**;改回 Playwright 模式只需要 git revert 兩個檔案
- **三階段反爬一致性破壞**:Stage A 仍用 Playwright(走 browser),Stage B/C 走 API。**反爬偵測機制如果 cross-reference 行為,可能會發現異常**
  - 目前實測沒問題,但保留風險
- **失敗時寫 default 值的小問題**(沿用 Playwright 時代的舊邏輯)
  - Stage C 公司被下架時寫 `capital='0'`,下次 SELECT 又會撈到無限重試
  - 對小規模可忽略,大規模要加 `last_company_fetch_at` 欄位區分「未取得」與「公司未填」
- **Stage A 仍是慢的瓶頸**:整體 pipeline 還是受 Stage A 拖慢
  - 後續若找到 Stage A 對應 API(`/jobs/search/list?...`),可以再砍掉 Playwright,連 Docker image 都能瘦身(省 ~150MB chromium)

### 🔁 後續追蹤

- 監控 Stage B/C 是否突然開始大量 fail → 可能是 104 改 API
- 探勘 Stage A 對應的 list API,若找到則撰寫 ADR-0006,完全移除 Chromium 依賴
- 觀察 IP rate limit 行為 — 目前 `STAGE_X_REQUEST_DELAY=0.3` 沒被擋,但若擴大規模可能要調整

## 實作細節

### 新模組結構

```
scpaper_content/main.py   (Stage B)
scpaper_company/main.py   (Stage C)
  ├── _extract_{job,company}_no()     # 從連結 regex 抽流水碼
  ├── _fetch_*()                      # 單筆 API call + 退避重試
  ├── _classify() (Stage B only)      # 比對 content_tags,決定 check_type
  ├── _worker(in_q, out_q, client)    # N 個並行,每筆 sleep 0.3s
  ├── _writer(out_q, cur, progress)   # 單線 DB 寫入 + 進度計數
  └── run_{content,company}_scraper() # 公開介面(簽章不變)
```

### 新環境變數

| 變數 | 預設 | 說明 |
|---|---|---|
| `STAGE_B_WORKERS` / `STAGE_C_WORKERS` | 5 | 並行 worker 數 |
| `STAGE_B_REQUEST_DELAY` / `STAGE_C_REQUEST_DELAY` | 0.3 | 每 worker request 後 sleep 秒數 |
| `STAGE_B_TIMEOUT` / `STAGE_C_TIMEOUT` | 10 | 單次 API timeout |

### 比對邏輯保留

Stage B 的 `_classify` 在 Python 端重現原本 JS `containsTag` 的邏輯:
- HTML strip(處理 `<br>` `<p>` 等 block-level 標籤 → 空格,其他標籤直接移除,`html.unescape` 解 entity)
- `lowercase + 去空白`
- substring 比對

**寫入 `check_type` 的字串 100% 相容舊版**,所以 admin UI 的 badge 渲染與 SQL 查詢完全不用改。

## References

- Code:
  - `scpaper_content/main.py` — Stage B(內文)新版
  - `scpaper_company/main.py` — Stage C(公司)新版
  - `requirements.txt` — 加 `httpx`
- 文件:
  - [`docs/architecture.md`](../architecture.md) — Level 3/4 已更新反映 API 模式
  - [`docs/sequence-diagrams.md`](../sequence-diagrams.md) — Stage B/C 流程已重畫
  - [ADR-0002](./0002-playwright-vs-requests.md) — 此 ADR 對 Stage B/C 部分 supersede,Stage A 仍適用
- 外部:
  - `https://www.104.com.tw/api/jobs/{job_no}`
  - `https://www.104.com.tw/api/companies/{company_no}/content`
  - [httpx](https://www.python-httpx.org/) — async HTTP client
