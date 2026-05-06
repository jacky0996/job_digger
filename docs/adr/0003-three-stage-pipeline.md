# ADR-0003: 三階段 pipeline(A→C→B)而非單階段

- **狀態**: Accepted
- **日期**: 2026-04-22
- **決策者**: Shane (SA / 開發者)

## Context — 我們在解決什麼問題?

爬完 104 列表後,還要做兩件事:**內文確認(怕標題誤判)** + **公司資料補全(資本額/員工數)**。怎麼組合這些步驟?

直覺:**逐筆完整爬一筆寫一筆** — 對每個職缺:抓清單 → 進內文 → 進公司頁 → 寫 DB → 下一筆。

但這個直覺**錯了**,效率差很多。

## Decision — 我們選了什麼?

**三階段 pipeline,順序刻意是 A → C → B**:

```
Stage A — 清單採集
  ↓ 寫 vacancies(只填 title/company/job_link/salary)
Stage C — 內文深度過濾
  ↓ 對每筆打開內文,標 check_type='pass' / 'mismatch'
Stage B — 公司資料補全
  ↓ 只對 check_type='pass' 的公司頁訪問,DISTINCT company_link 去重
```

每階段獨立 module,獨立可重跑,寫不同欄位。

## Considered Options — 還評估過哪些?

### 選項 1 — 逐筆完整爬(直覺解)

```
for job in list:
    job.detail = scrape_content(job.url)
    job.company_info = scrape_company(job.company_url)
    db.upsert(job)
```

- ✅ 邏輯直覺,單迴圈
- ❌ **同公司多次點擊**:100 個職缺可能屬於 20 家公司,你會重複點同公司 5 次。被 ban 機率高 + 浪費時間
- ❌ **內文不通過的也浪費時間補公司資料**:Stage C 過濾掉 60% 的話,你白白多做 60% 的 Stage B
- ❌ **Stage 不能重跑**:某筆職缺的內文 fail,整個逐筆迴圈都得重來

### 選項 2 — A → B → C(先補公司,再過濾)

- ✅ 同 A → C → B,但 B 在中間
- ❌ **浪費資源**:對最後會被 C 過濾掉的職缺也補了公司資料

### 選項 3 — A → C → B (現選)

- ✅ **去重**:Stage B 的 DISTINCT company_link 大幅省訪問次數
- ✅ **省 Stage B 時間**:只對 C 通過的職缺補公司資料,通常省 50-80%
- ✅ **每階段獨立可重跑**:Stage A 跑完寫 vacancies,即使 Stage C/B fail 資料還在 DB,下次重跑可以從中間階段開始
- ✅ **每階段語意清楚**:A 是 Producer-Consumer 並發、C 是純過濾、B 是去重訪問,職責不混
- ⚠ **三次掃 DB**:每階段 SELECT 一次 vacancies,有 overhead(但比逐筆爬省太多)

### 選項 4 — 用 message queue (Kafka/RabbitMQ) 串起三階段

- ✅ 完整 event-driven,各階段可獨立 scale
- ❌ 對單機規模過度工程,作品集不需要

## Consequences — 這個決定帶來什麼?

### ✅ 正面

- **效率高**:A 抓 1000 筆,C 過濾剩 400,B 對 80 家公司頁訪問。對比逐筆方案的 1000 + 1000 + 1000 = 3000 次頁面訪問,本方案是 1000 + 400 + 80 = 1480 次,**快 2 倍**
- **Resilient**:Stage A 寫進 DB 後,即使 C/B fail,資料不丟。下次跑 C 跑 B 時 SELECT 出未處理的繼續做
- **Stage 解耦**:三個 module 在 `scraper_vacancies/` `scpaper_content/` `scpaper_company/`,可獨立改、獨立測試
- **冪等**:每個 stage 都用 UPSERT 或 UPDATE WHERE status,重跑不會重複寫

### ⚠ 負面 / Trade-off

- **多次 SELECT vacancies**:每階段都查一次。緩解:
  - 加 index(`idx_keyword`、`idx_status`)
  - 對 N < 10k 規模沒影響
  - 真的大量可加 ETL pipeline 框架(Airflow / Prefect)

- **複雜度高**:三個 module、三次 entry,新人上手慢。緩解:
  - 每個 module `main.py::run_xxx_scraper` 是統一入口
  - SA 文件畫清楚([sequence-diagrams.md](../sequence-diagrams.md))

- **Stage 之間的契約**:A 寫的欄位 / C 標的 status / B 補的欄位,規約靠 convention 而非 schema。緩解:
  - data-model.md 寫清楚每階段寫什麼欄位
  - 加 integration test 驗整個 pipeline

- **Sub-stage 失敗判定不清**:Stage A 寫了 1000 筆但其中 50 筆 fail 沒有特別標記;Stage B 完成標記沒有(只看 capital != '0' 推測)。緩解:
  - 加 `crawl_logs` 表記每階段執行 stats(Roadmap)
  - 加 `stage_status` 欄位 enum 'pending'/'a_done'/'c_done'/'b_done'

### 🔁 後續追蹤

- 監控每階段執行時間 vs 職缺數,若 C 過濾率太低(<20%)代表 keyword/filter_tags 太寬
- 若加新階段(例如 Stage D 評分),確認順序
- 若公司頁變慢,Stage B 可獨立 scale(拆成獨立 worker container)

## References

- Code:
  - `app.py::start_scraping_task` — 三階段順序的 orchestrator
  - `scraper_vacancies/main.py::run_list_scraper` — Stage A
  - `scpaper_content/main.py::run_content_scraper` — Stage C
  - `scpaper_company/main.py::run_company_scraper` — Stage B
- 文件:
  - [`docs/sequence-diagrams.md` 第 1-3 節](../sequence-diagrams.md) — 三階段詳細時序
  - [`docs/architecture.md` Level 5](../architecture.md#level-5--三階段-pipeline-詳述) — 三階段職責劃分
  - [`docs/data-model.md` 第 6 節](../data-model.md#6-資料生命週期) — 一個職缺如何被三階段加工
- 業界對照:
  - ETL pipeline 設計原則(Extract → Transform → Load):本系統其實是 EXFL(Extract → Filter → Load + Enrich),只是名字不一樣
