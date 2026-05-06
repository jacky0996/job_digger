# Sequence Diagrams

本文件用 UML Sequence Diagram + Activity Diagram 描述 Job Digger 的關鍵流程。融合了原 [SA_104_Scraper_Design.md](./legacy/SA_104_Scraper_Design.md) 的內容(已 supersede,保留在 legacy/)。

目標讀者:**SA、開發者、想理解爬蟲怎麼閃避反爬的 Reviewer**。

涵蓋四個流程:

1. Stage A — 清單採集(Producer-Consumer 並發)
2. Stage C — 內文深度過濾
3. Stage B — 公司資料補全(去重訪問)
4. (Roadmap)Admin 觸發 → 整體 pipeline

---

## 1. Stage A — 清單採集(Producer-Consumer)

「最複雜的階段:從輸入 keyword 到所有職缺寫進 DB」

```mermaid
flowchart TD
    Start([開始 Stage A]) --> ReceiveParams[接收參數: keyword, filter_tags]
    ReceiveParams --> LaunchBrowser[啟動 Playwright Chromium<br/>+ stealth plugin]

    subgraph init["Phase 1: 精準搜尋模擬"]
        LaunchBrowser --> NavigateHome[前往 104 首頁]
        NavigateHome --> WaitLoad{確認頁面 ready}
        WaitLoad --> InputKeyword[輸入 keyword]
        InputKeyword --> OpenCategory[點開職務類別 modal]
        OpenCategory --> SelectIT[選『資訊軟體系統類』]
        SelectIT --> ConfirmCategory[按確定]
        ConfirmCategory --> ExecuteSearch[按搜尋按鈕]
    end

    subgraph hack["Phase 2: 末頁探測 hack"]
        ExecuteSearch --> JumpInput[找跳頁欄位]
        JumpInput --> Type9999[輸入 9999 並 enter]
        Type9999 --> GetLastPage[讀目前頁數 = 真實末頁<br/>(104 自動修正回最大值)]
    end

    subgraph collect["Phase 3: 採集 + 並發 (Producer)"]
        GetLastPage --> ReturnPage1[回到第 1 頁]
        ReturnPage1 --> ScrapingLoop{Loop: page = 1 to LastPage}

        ScrapingLoop -- 執行中 --> ScrollDown[捲動到底觸發 lazy-load]
        ScrollDown --> JsExtract[page.evaluate JS 批次抽:<br/>title, company, job_link, salary]
        JsExtract --> AnchorTrace[錨點回溯:<br/>從 .info-job__text 找父 card]
        AnchorTrace --> FilterTitle{標題含<br/>filter_tags 任一?}
        FilterTitle -- 是 --> EnqueueData[放進 asyncio.Queue]
        FilterTitle -- 否 --> NextPage
        EnqueueData --> NextPage[點下一頁]
        NextPage --> ScrapingLoop
        ScrapingLoop -- done --> SignalEnd[Queue.put None  終止信號]
    end

    subgraph consume["Phase 4: Consumer (3 個並行)"]
        SignalEnd --> WorkerLoop{3 個 Worker 並行 Loop}
        WorkerLoop --> GetItem[Queue.get item]
        GetItem -- None --> EndWorker[結束]
        GetItem -- 有資料 --> Upsert[UPSERT vacancies<br/>ON DUPLICATE KEY UPDATE job_link]
        Upsert --> WorkerLoop
    end

    EndWorker --> Done([Stage A 結束])
```

**關鍵設計**

| 點 | 為什麼 |
|---|---|
| **Stealth plugin** | 改寫 `navigator.webdriver` 等 fingerprint,降低被當機器人擋的機率 |
| **末頁探測 hack(輸入 9999)** | 104 收到超範圍頁碼會自動修正回末頁,瞬間知道任務終點;比 brute-force 翻直到 404 快 100 倍 |
| **錨點回溯** | 從 `.info-job__text` 往上找最近的職缺卡片,而非 hardcode XPath。104 改版時容錯性高 |
| **第一道過濾在 Producer** | 標題不符的根本不進 queue,減少 60-80% 寫入量 |
| **None 作為終止信號** | Producer 跑完丟 N 個 None(N = worker 數),Worker 收到就結束。標準 producer-consumer pattern |

---

## 2. Stage C — 內文深度過濾

「Stage A 用標題過濾過了,為什麼還要 Stage C?」

因為標題常有「軟體工程師(Backend)」這種模糊命名,標題過了但內文要求其實是 .NET 不是 PHP。Stage C 打開內文做更精確的判斷。

```mermaid
sequenceDiagram
    autonumber
    participant Orch as Orchestrator
    participant DB as MariaDB (vacancies)
    participant Pw as Playwright
    participant Site as 104 內文頁

    Orch->>DB: SELECT * FROM vacancies<br/>WHERE keyword=? AND check_type IS NULL
    DB-->>Orch: List<Vacancy> (待檢查)

    loop 每筆 vacancy
        Orch->>Pw: page.goto(vacancy.job_link)
        Pw->>Site: GET 內文頁
        Site-->>Pw: HTML
        Pw->>Pw: page.content() 取整頁 HTML
        Orch->>Orch: 比對 filter_tags 是否在內文出現

        alt 通過
            Orch->>DB: UPDATE vacancies SET check_type='pass' WHERE id=?
        else 不通過(關鍵字 mismatch)
            Orch->>DB: UPDATE check_type='keyword_mismatch'
        else 年資/學歷不符(進階)
            Orch->>DB: UPDATE check_type='seniority_mismatch'
        end
    end

    Note over Orch: 完成,Stage B 只看 check_type='pass' 的
```

**重點**:**不刪除不通過的紀錄**,標 `check_type` 即可。理由:
- 保留 audit trail(萬一過濾規則漏判,仍可從 DB 撈回來重審)
- 重跑爬蟲時不會再對標過的職缺浪費 CPU(`WHERE check_type IS NULL` 直接跳過)

---

## 3. Stage B — 公司資料補全(去重訪問)

「補資本額 / 員工數,要去重避免同公司多次點擊」

```mermaid
sequenceDiagram
    autonumber
    participant Orch as Orchestrator
    participant DB as MariaDB
    participant Pw as Playwright
    participant Site as 104 公司頁

    Orch->>DB: SELECT DISTINCT company_link FROM vacancies<br/>WHERE check_type='pass'<br/>AND (capital='0' OR employee_count='')
    DB-->>Orch: List<company_link> (去重後,通常比 vacancies 少 5-10 倍)

    loop 每個 company_link
        Orch->>Pw: page.goto(company_link)
        Pw->>Site: GET 公司頁
        Site-->>Pw: HTML
        Pw->>Pw: page.evaluate:<br/>遍歷 .intro-table__head,<br/>找 "資本額" / "員工人數" label,<br/>取父節點下的 .t3.mb-0

        Orch->>DB: UPDATE vacancies SET capital=?, employee_count=?<br/>WHERE company_link=?
        Note over DB: 同公司的所有職缺一起更新
    end
```

**設計重點**

| 點 | 為什麼 |
|---|---|
| **去重 (DISTINCT company_link)** | 100 個職缺可能屬於 20 家公司,不去重會白白 5 倍時間 |
| **只補空的** (`capital='0' OR employee_count=''`) | 第二次跑爬蟲時,已補的不再重補,加速 |
| **批次 UPDATE WHERE company_link=?** | 同公司多個職缺一起更新,不是 N+1 |
| **父節點下抓** | 104 的 DOM 結構是 `<th>標籤名</th><td class="t3 mb-0">值</td>`,先定位 label 再相對找值 |

---

## 4. (Roadmap)Admin 觸發 → 整體 pipeline

「使用者按下 Admin 的『執行爬蟲』按鈕,完整跨系統時序」

```mermaid
sequenceDiagram
    autonumber
    actor U as 使用者
    participant Adm as Admin (Laravel)
    participant API as Job Digger API :85
    participant Bg as BackgroundTasks
    participant SA as Stage A scraper
    participant SC as Stage C scraper
    participant SB as Stage B scraper
    participant DB as MariaDB

    U->>Adm: 點「執行爬蟲」(config_id=1)
    Adm->>API: POST /api/scrape/1
    API->>DB: SELECT id FROM search_configs WHERE id=1
    DB-->>API: ok
    API->>API: config_id in active_tasks?<br/>→ 不在,加進去
    API->>Bg: add_task(start_scraping_task, 1)
    API-->>Adm: HTTP 200 {"status":"accepted",...}
    Adm-->>U: 顯示「已啟動」

    Note over Bg,DB: --- 以下背景非同步進行 ---

    Bg->>DB: SELECT keyword, filter_tags FROM search_configs WHERE id=1
    DB-->>Bg: keyword="php", filter_tags=["php","後端"]

    Bg->>SA: run_list_scraper("php", ["php","後端"])
    Note over SA: 詳見第 1 節 Stage A 流程
    SA->>DB: UPSERT vacancies (含初步過濾)

    Bg->>SC: run_content_scraper("php", ["php","後端"])
    Note over SC: 詳見第 2 節 Stage C 流程
    SC->>DB: UPDATE check_type

    Bg->>SB: run_company_scraper()
    Note over SB: 詳見第 3 節 Stage B 流程
    SB->>DB: UPDATE capital + employee_count

    Bg->>API: active_tasks.discard(1)

    Note over U: --- 使用者另外開分頁 ---
    U->>Adm: 進「職缺搜尋」頁
    Adm->>DB: SELECT * FROM vacancies WHERE keyword='php'
    DB-->>Adm: 結果(隨時間越來越完整)
    Adm-->>U: 顯示新職缺

    Note over U: 也可輪詢狀態
    U->>Adm: 自動每 30 秒
    Adm->>API: GET /api/scrape/status/1
    API-->>Adm: {"is_running": true/false}
```

---

## 5. 反爬策略總覽

把分散在各 Stage 的反爬手段整合在一起:

| 手段 | 在哪實作 | 對抗什麼 |
|---|---|---|
| Playwright Stealth | Stage A/B/C 共用 | navigator.webdriver / plugins / languages 等 fingerprint 偵測 |
| 模擬人類點擊(input → click → wait) | Stage A 搜尋部分 | 偵測「直接 navigate 帶 query string」這種 bot 行為 |
| 末頁探測 hack(避免 brute-force 翻頁)| Stage A | 短時間內大量翻頁的 rate-based 偵測 |
| 公司頁去重(DISTINCT) | Stage B | 同 IP 短時間重複訪問同 URL |
| 第一道過濾在 Producer | Stage A | 不寫入「假興趣」職缺,間接降低 DB 壓力 |
| 適度 sleep(目前未統一)| 各 stage scraper 內 | rate limit |

**沒做但建議的**(對應 [adr/0002-playwright-vs-requests.md](./adr/0002-playwright-vs-requests.md) 的 Roadmap):
- IP rotation(proxy pool)
- User-Agent 輪換
- 隨機延遲(目前是固定的 page wait)
- Captcha 處理(目前 104 沒主動跳,但可能會)

---

## 6. 失敗處理

```mermaid
flowchart LR
    start([Stage A/B/C 任一執行])
    do[執行...]
    ok{成功?}
    success([下一階段])

    catch[try/except 捕捉]
    log[print 到 docker log]
    discard[active_tasks.discard]
    end_task([任務終止,但不影響其他]）

    start --> do --> ok
    ok -- 是 --> success
    ok -- 否 --> catch --> log --> discard --> end_task
```

**目前的限制**:
- **沒重試**:Stage 中斷就放棄整個 task(對應 [adr/0001 Roadmap](./adr/0001-fastapi-vs-django.md))
- **沒部分成功**:即使 Stage A 跑完 90%,Stage B/C fail 一樣算整個 task fail(但 A 寫的 vacancies 還在 DB)
- **錯誤回報只在 docker log**:Admin 不會收到通知,要自己看 log

對「我自己用」是 OK 的(出問題我會看 log),要對外開放才需要補 retry / observability。
