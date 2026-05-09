# 104 Job Digger - 系統架構設計 (SD)

## 1. 系統概述
本系統旨在高效採集 104 人力銀行之職缺數據，並透過公司資本額、職稱關鍵字等維度，協助求職者精準篩選高品質職缺。

## 2. 技術棧 (Tech Stack)
- **語言**: Python 3.12+
- **核心框架**: `asyncio` (非同步協程)
- **爬蟲引擎**: `Playwright` (支持 JavaScript 渲染之自動化瀏覽器)
- **資料庫**: `MariaDB` (Docker 部署，Port 3308)
- **資料庫驅動**: `aiomysql` (非同步 MySQL 客戶端)
- **程式碼規範**: `Black`, `Flake8`, `isort`
- **CI/CD**: GitHub Actions + Pre-commit hooks

## 3. 核心架構：生產者-消費者模型 (Producer-Consumer)
系統採用解耦的非同步隊列架構，確保爬取與寫入互不干擾。

### 3.1 生產者 (Producer - Stage A)
- **職責**: 執行網頁跳轉、模擬捲動、分頁偵測與 HTML 數據提取。
- **擷取策略**:
    - 使用 `page.evaluate` 執行瀏覽器端 JavaScript 批次擷取。
    - **錨點回溯法**: 以 `.info-job__text` 為起點，動態向上尋找最近的職缺卡片容器。
    - **精準過濾**: 根據標題關鍵字（如：php, 後端）進行第一道數據清洗。

### 3.2 訊息隊列 (Async Queue)
- **媒介**: `asyncio.Queue`
- **功能**: 作為緩衝帶，平衡爬蟲抓取速度（受網速、反爬機制限制）與資料庫寫入速度。

### 3.3 消費者 (Consumer - Stage B/C)
- **職責**: 從隊列讀取數據並寫入 MariaDB。
- **並行設計**: 同時啟動 **3 個並行 Worker**，處理高併發寫入需求。
- **寫入邏輯**:
    - 採用 **UPSERT (ON DUPLICATE KEY UPDATE)** 語法。
    - 以 `job_link` 為唯一鍵，若職缺已存在則更新狀態位而不重複插入。

### 3.4 公司探查器 (Explorer - Stage C)
- **職責**: 針對已採集之職缺，補全其所屬公司的詳細商業數據（資本額、員工人數）。
- **數據補全策略**:
    - **採集去重**: 透過 SQL `GROUP BY company_link` 確保同一家公司僅點擊一次頁面。
    - **條件觸發**: 僅針對 `capital = '0'` 或 `employee_count = ''` 的資料行進行工作。
- **擷取邏輯**:
    - 前往公司詳情頁。
    - 遍歷所有 `.intro-table__head` 標籤，比對「資本額」與「員工人數」字樣。
    - 鎖定該標籤父節點下的 `.t3.mb-0` 內容。

## 4. 資料流程 (Data Flow)
### Phase 1: 職缺採集 (Stage A)
1. **Init**: 偵測關鍵字搜尋結果的總頁數。
2. **Loop**:
    - Producer 載入第 N 頁 -> 捲動觸發渲染 -> JS 批次抓取 -> 放入 Queue。
    - Consumers 競爭 Queue 中的數據 -> 格式化網址 -> 寫入資料庫 (UPSERT)。

### Phase 2: 公司探查 (Stage C)
1. **Fetch**: 從數據庫撈取待補全的公司清單。
2. **Loop**:
    - 前往公司頁面 -> JS 擷取商業資訊 (資本、人數) -> 批次更新回資料庫。

## 5. 資料結構 (Database Schema)
- `vacancies`: 存儲職缺主表。
    - `capital`: 儲存公司資本額 (VARCHAR，保留單位)。
    - `employee_count`: 儲存員工人數。
- `search_configs`: 存儲關鍵字搜尋配置與二次過濾標籤。

## 6. 後續擴展 (Roadmap)
- **Stage C (Company Deep Dive)**: 針對已採集職缺，進入公司頁面爬取資本額等商業資訊。
- **Redis Integration**: 導入 Redis 作為任務排程與即時狀態同步伺服器。
- **API Wrapper**: 使用 FastAPI 提供外部介面觸發同步任務。
