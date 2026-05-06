# ADR-0001: 採用 FastAPI 而非 Django

- **狀態**: Accepted
- **日期**: 2026-04-22
- **決策者**: Shane (SA / 開發者)

## Context — 我們在解決什麼問題?

Job Digger 是個**純 service**:接 HTTP 觸發 → 跑背景爬蟲 → 寫 DB。沒有使用者 UI、沒有 admin 介面、沒有 ORM 複雜需求。Python 生態 web framework 的典型選擇:

- **Django**:全套(ORM / Admin / Auth / Template / Forms),適合 monolith
- **Flask**:輕量微框架,要什麼自己加
- **FastAPI**:async-first,內建 OpenAPI / Pydantic validation,適合 API service
- **Starlette**:FastAPI 底層,純 ASGI,適合自己刻

選哪個影響開發速度、async 支援、與後續維護成本。

## Decision — 我們選了什麼?

**採 FastAPI**:

- HTTP 層用 `@app.post("/api/scrape/{config_id}")` 這種 decorator + type hint
- 背景任務用內建 `BackgroundTasks`(輕量,不需 Celery)
- DB 操作用 `aiomysql` + 手寫 SQL(不用 ORM)
- 沒用 Pydantic model 定義 request body(本系統沒接 body,都是 path param)

## Considered Options — 還評估過哪些?

### 選項 1 — FastAPI【選中】

- ✅ **Async first**:Playwright async API + aiomysql + BackgroundTasks 完整 async 鏈,無 thread 切換成本
- ✅ **Swagger UI 免費**:`/docs` 自動產生,Admin 串 API 不必另寫文件
- ✅ **Pydantic validation 開箱**:本系統雖然沒用,但未來加 endpoint 用得到
- ✅ **輕量**:requirements.txt < 10 個套件
- ⚠ 沒有內建 Admin / ORM / Auth — 對 monolith 是缺點,對 service 是優點

### 選項 2 — Django + Django REST Framework

- ✅ 跟 [Middle Platform](../../../Middle_Platform) 同框架,知識複用
- ✅ Admin 開箱(可用來看 vacancies)
- ✅ ORM(Django models)減少 raw SQL
- ❌ **Sync first**:Django async 支援雖有但仍以 sync 為主,Playwright async 整合彆扭
- ❌ **重**:本系統不需要 Auth / Template / Forms / Migrations,Django 帶這些是 dead weight
- ❌ **Migration 管理**:本系統 schema 在 init.sql(跟 admin 共用),Django migration 反而衝突

### 選項 3 — Flask

- ✅ 輕量
- ❌ **Async 麻煩**:Flask 2.x 雖支援 async view,但生態(extensions)多半 sync,要自己 Celery
- ❌ 沒 OpenAPI 自動產生(要自己接 flask-restx)

### 選項 4 — Scrapy

- ✅ 專為爬蟲設計,有 pipeline / middleware / item 完整框架
- ❌ Spider 模型不適合「被 HTTP 觸發」這個場景(Scrapy 是 CLI / Crawler 為主)
- ❌ 整合 Playwright 要 scrapy-playwright,API 很彆扭
- ❌ 對 1 個 site / 簡單 keyword 場景過度工程

## Consequences — 這個決定帶來什麼?

### ✅ 正面

- **開發速度快**:50 行寫完 3 個 endpoint(見 [`app.py`](../../app.py))
- **Async 自然**:Playwright async + aiomysql + BackgroundTasks,從 HTTP 進入到 DB 寫出全 async
- **Swagger UI**:Admin 開發時可以直接 try-it-out
- **無框架包袱**:沒有 Django apps / migrations / settings 要學

### ⚠ 負面 / Trade-off

- **手寫 SQL**:沒 ORM,寫 raw SQL 容易拼錯。緩解:
  - 寫 SQL 時用 dict cursor (`aiomysql.DictCursor`),欄位錯立刻發現
  - SQL 集中在 scraper module 內,不散落
  - 規模大可導入 SQLAlchemy Core(不必上 ORM 全部)

- **沒 Admin UI**:本系統沒 Django admin,要看 DB 只能 DBeaver。緩解:Admin (Laravel) 提供 UI

- **BackgroundTasks 不適合 prod scale**:單 process 內跑,API 重啟會中斷;沒 retry。緩解:
  - 單機 / 作品集規模可接受
  - 規模大切到 RQ / Celery + Redis(Roadmap)

- **跟 Middle Platform 框架不同**:跨 repo 切換要 context switch (Django vs FastAPI)。緩解:
  - 兩者都是 Python,核心概念類似
  - SA 文件規範一致,易讀

### 🔁 後續追蹤

- 監控 BackgroundTasks 是否有「跑到一半 API 重啟」事件
- 若 vacancies 寫入量明顯增大(>10 萬筆/次),評估換 Celery
- 考慮加 SQLAlchemy Core 取代手寫 SQL

## References

- Code:
  - [`app.py`](../../app.py) — FastAPI app entry
  - [`requirements.txt`](../../requirements.txt) — 確實的依賴清單
- 文件:
  - [`docs/overview.md` 第 6 條設計原則](../overview.md#6-設計原則) — 「Boring tech」原則
  - [`docs/architecture.md` Level 2/3](../architecture.md) — Container / Component
- 外部:
  - [FastAPI 官方文件](https://fastapi.tiangolo.com/)
  - [Django vs FastAPI(社群討論)](https://www.reddit.com/r/django/) — 各種對照
