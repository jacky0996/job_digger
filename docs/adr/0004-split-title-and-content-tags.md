# ADR-0004:把 `filter_tags` 拆成 `title_tags` + `content_tags`

| 項 | 值 |
|---|---|
| 狀態 | Accepted |
| 日期 | 2026-05-08 |
| 影響範圍 | `search_configs` schema、Stage A/B 簽名、admin 表單 |

## Context

原本 `search_configs.filter_tags` 一個欄位被 Stage A(標題層過濾)跟 Stage B(內文層匹配)共用,兩個階段對同一份標籤有完全不同的解讀:

- **Stage A** 在 104 搜尋結果上掃職缺,**標題**包含任一 tag 才寫進 vacancies(收斂用)
- **Stage B** 進職缺頁,**內文 / 條件 / 擅長工具**含任一 tag 才標 `工作內容有含關鍵字` 等(精準用)

這個設計在「PHP 工程師」「後端工程師」這類 keyword 上工作得不錯 — 因為 PHP 工程師的職缺標題本來就會出現「PHP」「後端」等字,Stage A 留下來、Stage B 也容易命中。

但**碰到「跨領域組合」就破功**。例如使用者想找「需要 PHP 技能的 SA 職缺」:

- keyword = `SA`(在 104 搜尋 SA 相關職缺)
- filter_tags = `PHP,Laravel`(我要會 PHP/Laravel 的)
- Stage A 用 `PHP/Laravel` 掃 SA 職缺**標題** → 0 筆通過(SA 職缺標題不會寫 PHP)
- Stage B 連跑都跑不到

實際線上發現:`scrape:all-pending` 對 SA keyword 跑了 120 頁,**每頁都印「符合 0 筆」**,完全沒寫進 DB。

## Decision

把 `filter_tags` 一欄拆成兩欄:

| 欄位 | 用途 | 對應階段 |
|---|---|---|
| `title_tags` | **標題層過濾**:104 搜尋結果中標題含其中之一才寫入 vacancies | Stage A |
| `content_tags` | **內文層匹配**:工作內容/條件/擅長工具含其中之一才標 pass | Stage B |

對「PHP 的 SA」例子的設定:

```
keyword       = "SA"
title_tags    = "SA,系統分析,Architect"      ← 收斂職缺類別
content_tags  = "PHP,php,Laravel,laravel"   ← 精準匹配技能
```

Stage A 把標題像 SA 的職缺都收進來,Stage B 再用 PHP/Laravel 在內文裡精準篩 — 完美的 intersection 語意。

## Consequences

### 好處

- ✅ **跨領域組合可行**:「SA + PHP 技能」「PM + Python 技能」這種需求變得可表達
- ✅ **語意清楚**:每個欄位職責單一,不用記住「同一份 tags 在不同 stage 怎麼用」
- ✅ **Admin UI 更易理解**:兩個輸入框各自有 help text 解釋,新使用者更容易上手

### 成本

- ⚠ **Schema migration**:`ALTER TABLE search_configs ADD COLUMN ... ; UPDATE ... SET title_tags=filter_tags, content_tags=filter_tags ; DROP COLUMN filter_tags`
- ⚠ **多處程式碼動到**:Stage A/B 函式簽名、Laravel model fillable、Controller validation、Blade form/list、相關 docs

### 替代方案考慮過

| 方案 | 為何沒選 |
|---|---|
| **不改,讓使用者自己手動避開衝突** | 每次寫 SA-like keyword 都要 0 結果,UX 糟、debug 容易誤以為是程式碼 bug |
| **Stage A 不過濾,Stage B 全收當 no_match** | Stage A 寫超多無關職缺(SA 一個 keyword 可能 1000+ 筆),Stage B 要逐筆開瀏覽器跑 1000+ 次,12 小時起跳;DB 一堆 no_match 也難清 |
| **只多一個 content_tags,filter_tags 沿用為標題** | 名字 `filter_tags` 跟 `content_tags` 不對稱,語意不清。一致命名 `title_tags`/`content_tags` 對讀者更友善 |

## 實作位置

- Schema:[`init.sql`](../../init.sql)
- Stage A:[`scraper_vacancies/main.py::run_list_scraper`](../../scraper_vacancies/main.py)
- Stage B:[`scpaper_content/main.py::run_content_scraper`](../../scpaper_content/main.py)
- Orchestrator:[`app.py::start_scraping_task`](../../app.py)
- Admin model:[`SearchConfig`](../../../job_digger_admin/app/Models/SearchConfig.php)
- Admin form:[`_form.blade.php`](../../../job_digger_admin/resources/views/search_configs/_form.blade.php)
