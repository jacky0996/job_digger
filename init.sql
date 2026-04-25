CREATE TABLE IF NOT EXISTS vacancies (
    id BIGINT AUTO_INCREMENT PRIMARY KEY COMMENT '系統流水號',
    title VARCHAR(255) COMMENT '職缺職稱',
    company_name VARCHAR(255) COMMENT '公司名稱',
    company_link TEXT COMMENT '公司 104 頁面連結',
    job_link VARCHAR(500) COMMENT '職缺 104 頁面連結',
    salary_text VARCHAR(100) COMMENT '原始薪資內容',
    capital VARCHAR(100) DEFAULT '0' COMMENT '公司資本額 (由 Stage B 填入)',
    employee_count VARCHAR(100) DEFAULT '' COMMENT '員工人數 (由 Stage B 填入)',
    keyword VARCHAR(50) COMMENT '搜尋關鍵字',
    status ENUM('active', 'closed') DEFAULT 'active' COMMENT '職缺狀態',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '抓取時間',
    UNIQUE KEY uk_job_link (job_link),
    INDEX idx_keyword (keyword),
    INDEX idx_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='職缺採集主表';

CREATE TABLE IF NOT EXISTS search_configs (
    id INT AUTO_INCREMENT PRIMARY KEY COMMENT '系統編號',
    keyword VARCHAR(50) NOT NULL UNIQUE COMMENT '104 搜尋關鍵字',
    filter_tags TEXT COMMENT '二次過濾標籤 (逗號分隔，標題需包含其一)',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='搜尋與過濾配置表';

INSERT INTO search_configs (keyword, filter_tags)
VALUES ('php', 'php,PHP,軟體,資訊,後端')
ON DUPLICATE KEY UPDATE filter_tags=VALUES(filter_tags);
