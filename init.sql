-- Job Digger schema (PostgreSQL 16)
-- 對照舊版 MariaDB init.sql:
--   * AUTO_INCREMENT  → GENERATED ALWAYS AS IDENTITY
--   * ENUM            → VARCHAR + CHECK constraint(PG 的 CREATE TYPE 不好擴充)
--   * ON UPDATE CURRENT_TIMESTAMP → trigger(PG 沒有 column-level on update)
--   * COMMENT 'xxx'   → COMMENT ON COLUMN 獨立宣告
--   * INSERT ... ON DUPLICATE KEY UPDATE → INSERT ... ON CONFLICT DO UPDATE

-- =====================================================================
-- vacancies
-- =====================================================================
CREATE TABLE IF NOT EXISTS vacancies (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    title           VARCHAR(255),
    company_name    VARCHAR(255),
    company_link    TEXT,
    job_link        VARCHAR(500),
    salary_text     VARCHAR(100),
    capital         VARCHAR(100) DEFAULT '0',
    employee_count  VARCHAR(100) DEFAULT '',
    keyword         VARCHAR(50),
    status          VARCHAR(20)  DEFAULT 'active' CHECK (status IN ('active', 'closed')),
    check_type      VARCHAR(255),
    created_at      TIMESTAMP    DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP    DEFAULT CURRENT_TIMESTAMP,
    deleted_at      TIMESTAMP    DEFAULT NULL,
    CONSTRAINT uk_job_link UNIQUE (job_link)
);

CREATE INDEX IF NOT EXISTS idx_vacancies_keyword ON vacancies (keyword);
CREATE INDEX IF NOT EXISTS idx_vacancies_status  ON vacancies (status);

COMMENT ON TABLE  vacancies                 IS '職缺採集主表';
COMMENT ON COLUMN vacancies.id              IS '系統流水號';
COMMENT ON COLUMN vacancies.title           IS '職缺職稱';
COMMENT ON COLUMN vacancies.company_name    IS '公司名稱';
COMMENT ON COLUMN vacancies.company_link    IS '公司 104 頁面連結';
COMMENT ON COLUMN vacancies.job_link        IS '職缺 104 頁面連結';
COMMENT ON COLUMN vacancies.salary_text     IS '原始薪資內容';
COMMENT ON COLUMN vacancies.capital         IS '公司資本額 (由 Stage C 填入)';
COMMENT ON COLUMN vacancies.employee_count  IS '員工人數 (由 Stage C 填入)';
COMMENT ON COLUMN vacancies.keyword         IS '搜尋關鍵字';
COMMENT ON COLUMN vacancies.status          IS '職缺狀態';
COMMENT ON COLUMN vacancies.check_type      IS '篩選結果分類';
COMMENT ON COLUMN vacancies.created_at      IS '抓取時間';
COMMENT ON COLUMN vacancies.updated_at      IS '最後更新時間';
COMMENT ON COLUMN vacancies.deleted_at      IS '軟刪除時間';

-- PG 沒有 ON UPDATE CURRENT_TIMESTAMP,用 trigger 模擬
CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_vacancies_updated_at ON vacancies;
CREATE TRIGGER trg_vacancies_updated_at
BEFORE UPDATE ON vacancies
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- =====================================================================
-- search_configs
-- =====================================================================
CREATE TABLE IF NOT EXISTS search_configs (
    id                INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    keyword           VARCHAR(50) NOT NULL UNIQUE,
    title_tags        TEXT,
    content_tags      TEXT,
    created_by_email  VARCHAR(191) DEFAULT NULL,
    updated_by_email  VARCHAR(191) DEFAULT NULL,
    created_at        TIMESTAMP    DEFAULT CURRENT_TIMESTAMP,
    updated_at        TIMESTAMP    DEFAULT NULL,
    last_scraped_at   TIMESTAMP    DEFAULT NULL
);

COMMENT ON TABLE  search_configs                  IS '搜尋與過濾配置表';
COMMENT ON COLUMN search_configs.id               IS '系統編號';
COMMENT ON COLUMN search_configs.keyword          IS '104 搜尋關鍵字';
COMMENT ON COLUMN search_configs.title_tags       IS 'Stage A 用:職缺標題需含其中之一才寫入(逗號分隔)';
COMMENT ON COLUMN search_configs.content_tags     IS 'Stage B 用:工作內容/條件/擅長工具需含其中之一才標 pass(逗號分隔)';
COMMENT ON COLUMN search_configs.created_by_email IS '建立者 email (admin Laravel 寫入)';
COMMENT ON COLUMN search_configs.updated_by_email IS '最後更新者 email (admin Laravel 寫入)';
COMMENT ON COLUMN search_configs.updated_at       IS 'admin 編輯時寫入,不走 ON UPDATE 自動更新';
COMMENT ON COLUMN search_configs.last_scraped_at  IS '最後一次成功完成爬蟲的時間';

-- seed:確保 php 配置一定存在;若已存在,更新 tag 內容
INSERT INTO search_configs (keyword, title_tags, content_tags)
VALUES ('php', 'php,PHP,軟體,資訊,後端', 'php,PHP,laravel,Laravel')
ON CONFLICT (keyword) DO UPDATE
SET title_tags   = EXCLUDED.title_tags,
    content_tags = EXCLUDED.content_tags;
