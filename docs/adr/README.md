# Architecture Decision Records (ADR)

本目錄收錄 Job Digger 的關鍵架構決策。

## 索引

| # | 標題 | 狀態 | 影響範圍 |
|---|---|---|---|
| [0001](./0001-fastapi-vs-django.md) | 採用 FastAPI 而非 Django | Accepted | 整體架構 / 開發體驗 |
| [0002](./0002-playwright-vs-requests.md) | 採用 Playwright 而非 requests + BeautifulSoup | Accepted | 爬蟲核心 / 反爬能力 |
| [0003](./0003-three-stage-pipeline.md) | 三階段 pipeline(A→C→B)而非單階段 | Accepted | 資料品質 / 爬蟲效率 |

> 模板與寫作公約見 [Middle Platform docs/adr/README.md](../../../Middle_Platform/docs/adr/README.md) — 跨 repo 共用同一個 ADR 模板。
