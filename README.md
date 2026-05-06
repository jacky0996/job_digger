# Job Digger

FastAPI + Playwright 打造的 **104 職缺爬蟲服務**,負責三階段抓取與清洗:**清單採集 → 內文過濾 → 公司資料補全**。提供 HTTP API 給 [Job Digger Admin](../job_digger_admin) 觸發爬蟲,寫入共用 MariaDB。

> ▸ **想直接跑起來** → 看 [docs/deployment.md](./docs/deployment.md)(`docker compose up -d --build`,host port 85,**第一次 build 含 Playwright Chromium 5-10 分鐘**)
> ▸ **想懂為什麼做** → 看 [docs/overview.md](./docs/overview.md)(系統定位、Scope)
> ▸ **想看爬蟲流程圖** → 看 [docs/sequence-diagrams.md](./docs/sequence-diagrams.md)(三階段 pipeline + 生產者-消費者)
> ▸ **想串 API** → 看 [docs/api-spec.md](./docs/api-spec.md)(3 個 endpoint)

技術棧:Python 3.11 / FastAPI / asyncio / Playwright(Chromium)/ aiomysql / MariaDB / Docker / Pre-commit。

---

## SA 文件索引

本專案以 SA 視角整理文件,給三類讀者使用:

- **串接方(Job Digger Admin)** — 想知道 API 怎麼呼叫
- **開發者 / SA / Architect** — 想知道爬蟲架構與三階段 pipeline
- **未來維護者** — 想知道某個技術決策當初的取捨

文件採 Markdown + Mermaid 撰寫,GitHub 直接渲染,不需要任何工具即可閱讀。

---

## 推薦閱讀順序

| # | 文件 | 給誰看 | 對應 UML 圖 |
|---|---|---|---|
| 1 | [docs/overview.md](./docs/overview.md) | 所有人 | — (純文字:定位 / Scope / Stakeholders) |
| 2 | [docs/architecture.md](./docs/architecture.md) | 開發者 / Architect | **Component Diagram**(三階段 pipeline + Producer-Consumer 模型) |
| 3 | [docs/data-model.md](./docs/data-model.md) | 開發者 / DBA | **ERD**(vacancies + search_configs) |
| 4 | [docs/api-spec.md](./docs/api-spec.md) | 串接方(admin) | — (HTTP contract,3 個 endpoint) |
| 5 | [docs/sequence-diagrams.md](./docs/sequence-diagrams.md) | SA / 開發者 | **Sequence Diagram**(三階段爬蟲 + 反爬處理) |
| 6 | [docs/deployment.md](./docs/deployment.md) | Ops / Architect | **Deployment Diagram**(Docker + Playwright Chromium) |
| 7 | [docs/adr/](./docs/adr/) | 後續維護者 | — (Architecture Decision Records) |

---

## 不同角色的入口建議

| 你是誰 | 從這裡開始 |
|---|---|
| **第一次來** | `docs/overview.md` → `docs/architecture.md` → `docs/sequence-diagrams.md` |
| **要 review 設計** | `docs/architecture.md` → `docs/sequence-diagrams.md` → `docs/adr/` |
| **要呼叫 API** | `docs/api-spec.md` → `http://localhost:85/docs`(FastAPI 自動產生 Swagger UI) |
| **要部署 / 維運** | `docs/deployment.md` |
| **要查 DB schema** | `docs/data-model.md` |
| **要看 104 爬蟲怎麼閃避反爬** | `docs/sequence-diagrams.md` 第 2 節 + `adr/0002-playwright-vs-requests.md` |

---

## 既有文件整併

本目錄原本有兩份 ad-hoc 的 SA 文件:

| 原檔 | 新位置 |
|---|---|
| `docs/SA_104_Scraper_Design.md` | 已整併進 [`docs/sequence-diagrams.md`](./docs/sequence-diagrams.md);舊檔搬到 [`docs/legacy/`](./docs/legacy/) |
| `docs/SD_Crawler_Architecture.md` | 已整併進 [`docs/architecture.md`](./docs/architecture.md);舊檔搬到 [`docs/legacy/`](./docs/legacy/) |

> 兩個原檔已搬進 `docs/legacy/` 保留歷史。確認沒人引用舊位置後可整個刪除 legacy/。

---

## 文件公約

- **圖優於文字**:盡量用 Mermaid 畫圖,文字補關鍵說明
- **每份文件 < 5 分鐘看完**:超過就拆檔
- **變更 code 時同步更新**:文件與 code 同 repo,改 scraper 就改 `sequence-diagrams.md`,改 schema 就改 `data-model.md`
- **決策必留 ADR**
- **與生態系對齊**:本系統是 [Middle Platform](../Middle_Platform) + [Job Digger Admin](../job_digger_admin) 生態的爬蟲層,SA 文件結構刻意跨 repo 一致
