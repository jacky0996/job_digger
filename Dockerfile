# Job Digger FastAPI service (104 爬蟲 + 清洗)
FROM python:3.11-slim

# Playwright 跑 chromium 需要的系統 deps + 一般工具
RUN apt-get update && apt-get install -y \
    wget \
    gnupg \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 1. 先裝 Python 套件(這層 cache 友好,改 code 不必重裝)
COPY requirements.txt .
RUN pip install --upgrade pip && pip install --no-cache-dir -r requirements.txt

# 2. 裝 Playwright Chromium + 它的系統函式庫(~150MB)
RUN playwright install chromium && playwright install-deps chromium

# 3. 複製整個專案(app.py + 各 scraper 模組 + data_transform)
COPY app.py ./
COPY scpaper_company ./scpaper_company
COPY scpaper_content ./scpaper_content
COPY scraper_vacancies ./scraper_vacancies
COPY data_transform ./data_transform

EXPOSE 8000

# 啟動 FastAPI(注意:模組名是 app 不是 main)
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
