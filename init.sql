CREATE TABLE IF NOT EXISTS vacancies (
    id BIGINT AUTO_INCREMENT PRIMARY KEY COMMENT '系統流水號',
    title VARCHAR(255) COMMENT '職缺職稱',
    company_name VARCHAR(255) COMMENT '公司名稱',
    company_link TEXT COMMENT '公司 104 頁面連結',
    job_link VARCHAR(500) COMMENT '職缺 104 頁面連結',
    salary_text VARCHAR(100) COMMENT '原始薪資內容',
    capital VARCHAR(100) DEFAULT '0' COMMENT '公司資本額 (由 Stage C 填入)',
    employee_count VARCHAR(100) DEFAULT '' COMMENT '員工人數 (由 Stage C 填入)',
    keyword VARCHAR(50) COMMENT '搜尋關鍵字',
    status ENUM('active', 'closed') DEFAULT 'active' COMMENT '職缺狀態',
    check_type VARCHAR(255) DEFAULT NULL COMMENT '篩選結果分類',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '抓取時間',
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '最後更新時間',
    deleted_at TIMESTAMP NULL DEFAULT NULL COMMENT '軟刪除時間',
    UNIQUE KEY uk_job_link (job_link),
    INDEX idx_keyword (keyword),
    INDEX idx_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='職缺採集主表';

CREATE TABLE IF NOT EXISTS search_configs (
    id INT AUTO_INCREMENT PRIMARY KEY COMMENT '系統編號',
    keyword VARCHAR(50) NOT NULL UNIQUE COMMENT '104 搜尋關鍵字',
    title_tags TEXT COMMENT 'Stage A 用:職缺標題需含其中之一才寫入(逗號分隔)',
    content_tags TEXT COMMENT 'Stage B 用:工作內容/條件/擅長工具需含其中之一才標 pass(逗號分隔)',
    -- audit 欄位:由 admin (Laravel) 寫入,但 schema 統一在這裡管,
    -- 避免雙邊維護(Laravel 沒對應 migration)
    created_by_email VARCHAR(191) DEFAULT NULL COMMENT '建立者 email (admin Laravel 寫入)',
    updated_by_email VARCHAR(191) DEFAULT NULL COMMENT '最後更新者 email (admin Laravel 寫入)',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NULL DEFAULT NULL COMMENT 'admin 編輯時寫入,不走 ON UPDATE 自動更新',
    last_scraped_at TIMESTAMP NULL DEFAULT NULL COMMENT '最後一次成功完成爬蟲的時間'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='搜尋與過濾配置表';

INSERT INTO search_configs (keyword, title_tags, content_tags)
VALUES ('php', 'php,PHP,軟體,資訊,後端', 'php,PHP,laravel,Laravel')
ON DUPLICATE KEY UPDATE
    title_tags=VALUES(title_tags),
    content_tags=VALUES(content_tags);
