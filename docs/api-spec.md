# API Specification

本文件列出 Job Digger 對外提供的 HTTP endpoints。目標讀者:**串接方([Job Digger Admin](../../job_digger_admin))、想直接用 curl 觸發爬蟲的開發者**。

> FastAPI 自動產生的 Swagger UI 在 http://localhost:85/docs(可試打,有 try-it-out 介面)。本文件聚焦於設計層級的契約。

---

## 1. 端點全覽

| 方法 | 路徑 | 用途 | 認證 |
|---|---|---|---|
| POST | `/api/scrape/{config_id}` | 觸發爬蟲(背景執行) | 無(內網,靠 CORS 白名單) |
| GET | `/api/scrape/status/{config_id}` | 查爬蟲是否在跑 | 無 |
| GET | `/health` | 健康檢查 | 無 |
| GET | `/docs` | Swagger UI | 無 |
| GET | `/openapi.json` | OpenAPI spec | 無 |

> 本系統**目前沒有身分驗證**(SSO 不適用 — 它是 service-to-service,不是給人用)。安全靠:
> 1. CORS 白名單(只允許 admin origin,本機 localhost:8084)
> 2. 內網部署(host port 85 在生產應該完全不對外 expose)

---

## 2. 通用約定

### 2.1 Content Type

- Request:不需 body(都是 path parameter)
- Response:`application/json`

### 2.2 CORS

由 `ALLOWED_ORIGINS` env var 控制(逗號分隔)。預設:

```
http://localhost:84,http://127.0.0.1:84
```

> ⚠ port 84 是歷史值,實際 admin 在 host port **8084**,正式部署要把 8084 加進白名單。

### 2.3 HTTP Status Code

| Status | 用法 |
|---|---|
| 200 | 成功 |
| 400 | 已有同 config_id 的爬蟲在跑(防重複觸發)|
| 404 | config_id 不存在於 search_configs |
| 422 | path parameter 型別錯(FastAPI 自動處理)|
| 500 | 未捕捉的 server error |

---

## 3. 各端點詳述

### 3.1 `POST /api/scrape/{config_id}`

觸發爬蟲。**非同步**,立即回 200,實際爬蟲在 `BackgroundTasks` 跑。

**Path Parameter**

| 名稱 | 型別 | 說明 |
|---|---|---|
| `config_id` | int | `search_configs.id`,**必須先存在** |

**Response 200**

```json
{
  "status": "accepted",
  "message": "Scraping task for config 1 has been started.",
  "config_id": 1
}
```

**Errors**

```json
// 404 — config_id 不存在
{
  "detail": "Config ID not found"
}

// 400 — 已在跑
{
  "detail": "此關鍵字的抓取任務已在執行中"
}
```

**設計重點**

- **冪等性**:同 config_id 重複呼叫會擋 → 400(由 `active_tasks` set 控制)
- **無回傳結果**:呼叫後實際爬蟲跑 5-30 分鐘,要查狀態用 `/api/scrape/status/{id}`
- **不接 query / body**:刻意保持簡單,所有設定都從 `search_configs` 讀
- **背景任務**:用 FastAPI 的 `BackgroundTasks.add_task`,**不適合單機重啟敏感場景**(API 重啟會中斷)— Roadmap 改 Celery / RQ

**範例 cURL**

```bash
curl -X POST http://localhost:85/api/scrape/1
```

### 3.2 `GET /api/scrape/status/{config_id}`

查特定 config_id 是否有爬蟲正在跑。

**Path Parameter**

| 名稱 | 型別 | 說明 |
|---|---|---|
| `config_id` | int | search_configs.id |

**Response 200**

```json
{
  "config_id": 1,
  "is_running": true
}
```

> **不檢查 config_id 是否存在** — 不存在的 id 也會回 `is_running: false`。理由:這是「is something running」查詢,不是「does this config exist」。

**設計限制**

- **只看 in-process state**:來源是 `app.py::active_tasks` set,API 重啟就清空(即使爬蟲 fail 了也不知道是「已結束」還是「重啟前在跑」)
- **不知道進度**:只回 `true/false`,不能回「跑到第幾頁」(Roadmap)

**範例 cURL**

```bash
curl http://localhost:85/api/scrape/status/1
# {"config_id":1,"is_running":false}
```

### 3.3 `GET /health`

健康檢查。給 LB / k8s probe / 監控用。

**Response 200**

```json
{
  "status": "ok",
  "port": 83
}
```

> `port: 83` 是歷史值(預設 dev port),實際對外是 8000(容器內)→ 85(host)。這個欄位**沒有實際意義**,可以忽略。Roadmap 會清掉。

**範例 cURL**

```bash
curl http://localhost:85/health
```

### 3.4 `/docs` 與 `/openapi.json`

FastAPI 自動產生的:

- `/docs` — Swagger UI(瀏覽器互動)
- `/openapi.json` — OpenAPI 3 spec JSON

> 不必手寫,跟 code 永遠同步。改 endpoint 簽章後 Swagger UI 自動更新。

---

## 4. 串接 Walkthrough(Admin 視角)

完整時序見 [`sequence-diagrams.md` 第 3 節](./sequence-diagrams.md#3-admin-觸發爬蟲流程integration-roadmap)。簡要版:

```bash
# 1. 觸發
curl -X POST http://localhost:85/api/scrape/1
# → {"status": "accepted", ...}

# 2. 輪詢狀態
while true; do
  curl -s http://localhost:85/api/scrape/status/1 | jq
  sleep 30
done
# 看到 is_running 從 true 變 false 就是跑完了

# 3. 看結果(直接打 DB,不打本系統 API)
docker exec -it job_digger_db mariadb -udeveloper -p job_digger \
  -e "SELECT title, company_name, salary_text FROM vacancies WHERE keyword='php' ORDER BY created_at DESC LIMIT 10"
```

---

## 5. 速率限制 / Retry

| 機制 | 現況 | Roadmap |
|---|---|---|
| 防重複觸發 | `active_tasks` set 擋同 config_id 同時跑兩次 | OK |
| Per-IP rate limit | **無** | 加 nginx `limit_req` 或 `slowapi` |
| 失敗重試 | **無**(內部任務拋例外只記 log) | 加 retry decorator(指數 backoff)|
| Webhook 通知完成 | **無**(Admin 只能 polling status)| 加 `POST callback_url` 機制 |

---

## 6. 安全考量

| 風險 | 現況 | 建議 |
|---|---|---|
| 任何人都能觸發爬蟲 | CORS + 內網部署 | 加 API key(env var)、middleware 驗證 |
| 任何人能查 config 是否在跑 | 同上 | 同上 |
| Swagger UI / OpenAPI 公開 | dev OK,prod 應關閉 | `FastAPI(docs_url=None)` 在 production |
| `/health` 露 port 資訊 | 無實質風險 | Roadmap 清掉 |

---

## 7. 沒做的事(刻意)

- **沒 endpoint 改 search_configs** — Admin 直接寫 DB,本系統不該重複實作
- **沒 endpoint 列 vacancies** — Admin 直接讀 DB
- **沒 endpoint 取消執行中的爬蟲** — `active_tasks` 只追蹤狀態,沒有 cancel hook(Roadmap)
- **沒 batch endpoint** — 一次一個 config_id,Admin 想 batch 自己 loop

對應原則:**本系統是 worker,不是 CRUD service**。資料介面靠 DB(共用 MariaDB),HTTP 介面只給「觸發」用。
