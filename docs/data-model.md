# Data Model

本文件描述 Job Digger 在 MariaDB 中的資料結構。本系統**擁有兩張共用業務表**(`vacancies` / `search_configs`),由本系統的 `init.sql` 啟動時建立,**[Job Digger Admin](../../job_digger_admin) 共用同一個 DB 但只是 client**。

目標讀者:**開發者、DBA、想理解資料怎麼流動的 Reviewer**。

---

## 1. ERD

```mermaid
erDiagram
    SEARCH_CONFIGS ||--o{ VACANCIES : "by keyword (no FK, snapshot)"

    SEARCH_CONFIGS {
        int      id PK
        string   keyword UK "104 搜尋關鍵字"
        text     filter_tags "comma-separated 過濾標籤"
        ts       created_at
    }

    VACANCIES {
        bigint   id PK
        string   title "職缺標題"
        string   company_name "公司名稱"
        text     company_link "公司 104 頁 URL (Stage B 用來去重)"
        string   job_link UK "職缺 URL"
        string   salary_text "原始薪資文字 (未解析)"
        string   capital "公司資本額,Stage B 補,default '0'"
        string   employee_count "員工數,Stage B 補,default ''"
        string   keyword "對應 search_configs.keyword (snapshot)"
        enum     status "active | closed"
        string   check_type "Stage C 寫,nullable"
        ts       created_at "首次抓取"
        ts       updated_at "最後更新 (UPSERT 觸發)"
        ts       deleted_at "soft delete,nullable"
    }
```

> **沒有真正的 FK** — `vacancies.keyword` 是 `search_configs.keyword` 的 snapshot 字串,不是 FK。理由見下節。

---

## 2. 表清單與擁有權

| 表 | 擁有者 | Producer(誰寫) | Consumer(誰讀) |
|---|---|---|---|
| `search_configs` | 本系統(schema)/ Admin(content) | Admin CRUD | 本系統:`start_scraping_task` 撈 keyword + filter_tags |
| `vacancies` | 本系統 | 本系統 三階段 pipeline | Admin 列表 / 過濾 |

> **「Schema 擁有者」vs「Content 擁有者」是不同的事**。`search_configs` 的 schema 在本系統的 `init.sql` 裡(因為跟 vacancies 在同個 DB,init.sql 一起建),但**內容**完全是 Admin 在管(Admin 那邊有 SearchConfig model 做 CRUD)。本系統只**讀**它。

---

## 3. `search_configs`

```sql
CREATE TABLE search_configs (
    id INT AUTO_INCREMENT PRIMARY KEY,
    keyword VARCHAR(50) NOT NULL UNIQUE,
    filter_tags TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

INSERT INTO search_configs (keyword, filter_tags)
VALUES ('php', 'php,PHP,軟體,資訊,後端')
ON DUPLICATE KEY UPDATE filter_tags = VALUES(filter_tags);
```

**欄位重點**

| 欄位 | 設計考量 |
|---|---|
| `keyword` | UNIQUE — 同個關鍵字只能有一筆設定 |
| `filter_tags` | comma-separated,**OR 邏輯**(標題含其一即通過 Stage A 過濾)|
| `created_at` | 沒 `updated_at`(Admin 改 keyword/filter_tags 不留軌跡 — 屬於設定不是事件) |

**本系統如何用它**:

```python
# app.py::start_scraping_task
await cur.execute(
    "SELECT keyword, filter_tags FROM search_configs WHERE id = %s",
    (config_id,),
)
config = await cur.fetchone()
keyword = config["keyword"]
filter_tags = [t.strip() for t in config["filter_tags"].split(",")]
```

---

## 4. `vacancies`(主要業務表)

```sql
CREATE TABLE vacancies (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    title VARCHAR(255),
    company_name VARCHAR(255),
    company_link TEXT,
    job_link VARCHAR(500),
    salary_text VARCHAR(100),
    capital VARCHAR(100) DEFAULT '0',
    employee_count VARCHAR(100) DEFAULT '',
    keyword VARCHAR(50),
    status ENUM('active', 'closed') DEFAULT 'active',
    check_type VARCHAR(255) DEFAULT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    deleted_at TIMESTAMP NULL DEFAULT NULL,
    UNIQUE KEY uk_job_link (job_link),
    INDEX idx_keyword (keyword),
    INDEX idx_status (status)
);
```

**欄位設計考量**

| 欄位 | 設計考量 | 寫入階段 |
|---|---|---|
| `job_link` UNIQUE | 重跑爬蟲不重複插入(UPSERT 條件)| Stage A |
| `title` / `company_name` / `salary_text` | 從 104 抓的原始內容,**不解析**(salary 字串如「月薪 50,000~80,000」直接存)| Stage A |
| `company_link` | Stage B 去重用(`SELECT DISTINCT company_link`)| Stage A |
| `capital` / `employee_count` | default `'0'` / `''`,Stage B 補 | Stage B |
| `keyword` | snapshot,不依賴 search_configs(萬一 Admin 刪了 search_config 還能查歷史) | Stage A |
| `status` | enum,目前都是 `active`(`closed` 是 Roadmap:定期回 104 看職缺還在不在)| (預留) |
| `check_type` | Stage C 寫,值如 `pass` / `keyword_mismatch` / `seniority_mismatch` | Stage C |
| `created_at` / `updated_at` | UPSERT 時 updated_at 自動更新 | 自動 |
| `deleted_at` | soft delete,目前**沒人寫**(Admin 不該寫,本系統也不想寫過時職缺直接刪) | (預留) |

**索引設計**

| Index | 用途 |
|---|---|
| `uk_job_link` (UNIQUE) | UPSERT 條件、Stage C 內文過濾時 SELECT WHERE job_link |
| `idx_keyword` | Admin 列表頁過濾(WHERE keyword = ?)|
| `idx_status` | 排除 closed 職缺(`WHERE status = 'active'`) |

**沒加但建議加的索引**(Roadmap):
- `(keyword, status, deleted_at)` 複合索引 — Admin 列表頁的最常組合過濾
- `(check_type)` — 統計頁(各 check_type 的職缺數)

---

## 5. UPSERT 邏輯詳述

`scraper_vacancies` 寫入時:

```sql
INSERT INTO vacancies
    (title, company_name, company_link, job_link, salary_text, keyword, status, created_at)
VALUES
    (?, ?, ?, ?, ?, ?, 'active', NOW())
ON DUPLICATE KEY UPDATE
    title = VALUES(title),
    salary_text = VALUES(salary_text),
    -- 注意:不更新 company_link / capital / employee_count
    -- 因為 Stage B 已經補過,別覆蓋掉
    updated_at = NOW();
```

**為何 UPSERT 不全部覆蓋**:
- `capital` / `employee_count` 是 Stage B 的成果,Stage A 重跑時 default 是空值,覆蓋會把 B 的成果清掉
- `check_type` 是 Stage C 的成果,同理

實作上有兩個選項:
1. UPSERT 但用 `IFNULL` 保留:`capital = IFNULL(capital, VALUES(capital))`(複雜)
2. UPSERT 時只更新 Stage A 寫的欄位(現在做法)

選 2 比較直觀。

---

## 6. 資料生命週期

### 6.1 一個職缺的生命線

```
[Stage A] INSERT vacancies (title, company, job_link, ...)
   capital='0', employee_count='', check_type=NULL, status='active'
   ↓
[Stage C] UPDATE vacancies SET check_type = 'pass'/'keyword_mismatch'/...
   ↓
[Stage B] UPDATE vacancies SET capital = '5億', employee_count = '500人'
   (但只更新 check_type='pass' 的職缺)
   ↓
(Roadmap) 排程定期 UPDATE vacancies SET status = 'closed'
   WHERE 104 已經不存在
   ↓
(Roadmap) Admin 手動 UPDATE vacancies SET deleted_at = NOW() (軟刪)
```

### 6.2 一個 search_config 的生命線

```
Admin INSERT search_configs (keyword='php', filter_tags='php,後端')
   ↓
Admin (任意次) UPDATE filter_tags = 'php,後端,軟體,資訊'
   ↓
本系統 SELECT WHERE id = X(每次跑爬蟲)
   ↓
Admin 可能 DELETE
   (但歷史的 vacancies.keyword='php' 仍保留 — snapshot 設計)
```

---

## 7. 機敏資料考量

| 欄位 | 機敏性 | 處理 |
|---|---|---|
| `vacancies.salary_text` | 低 — 公開 104 資料 | 明文 |
| `vacancies.company_name` | 低 — 公開 | 明文 |
| `vacancies.capital` / `employee_count` | 低 — 公開 | 明文 |
| `vacancies.job_link` | 低 — 公開 URL | 明文 |
| `search_configs.keyword` | 內部 — 我設的搜尋字 | 明文 |

> 整個系統不存 PII(沒有使用者個資、沒有薪資協商紀錄等),機敏性低。

---

## 8. Roadmap

| 項目 | 計畫 |
|---|---|
| `closed` 狀態自動標記 | 排程跑「重訪」,職缺已下架時 `UPDATE status='closed'` |
| `salary_min` / `salary_max` 數值欄 | 從 `salary_text` 解析(如「月薪 50,000~80,000」→ 50000 / 80000),加索引以便範圍查詢 |
| `crawl_logs` 新表 | 記每次爬蟲的執行時間 / 成功職缺數 / 失敗原因,給 Admin 統計頁用 |
| `companies` 抽出新表 | 目前公司資訊重複存在每筆 vacancy,正規化成 `companies` 表 + `vacancies.company_id` FK,省空間 + 一致性 |
| Partition by year | 若 vacancies 破百萬,按 `created_at` 年份 partition |
