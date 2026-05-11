# Architecture Decision Records (ADR)

本目錄收錄 Job Digger 的關鍵架構決策。

## 索引

| # | 標題 | 狀態 | 影響範圍 |
|---|---|---|---|
| [0001](./0001-fastapi-vs-django.md) | 採用 FastAPI 而非 Django | Accepted | 整體架構 / 開發體驗 |
| [0002](./0002-playwright-vs-requests.md) | 採用 Playwright 而非 requests + BeautifulSoup | **Partially Superseded by 0005**(Stage A 仍適用)| 爬蟲核心 / 反爬能力 |
| [0003](./0003-three-stage-pipeline.md) | 三階段 pipeline(A→B→C)而非單階段 | Accepted | 資料品質 / 爬蟲效率 |
| [0004](./0004-split-title-and-content-tags.md) | 拆 title_tags 與 content_tags 兩欄 | Accepted | 過濾精度 |
| [0005](./0005-stage-bc-switch-to-104-api.md) | Stage B/C 改走 104 公開 API | Accepted | 爬蟲效率(10-30 倍) |

> 模板與寫作公約見 [Middle Platform docs/adr/README.md](../../../Middle_Platform/docs/adr/README.md) — 跨 repo 共用同一個 ADR 模板。
