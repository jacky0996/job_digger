# 使用官方輕量 Python 映像檔
FROM python:3.11-slim

# 安裝 Playwright 啟動無頭瀏覽器所需的系統依賴
RUN apt-get update && apt-get install -y \
    wget \
    gnupg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 複製並安裝套件 (善用 Docker 快取)
COPY requirements.txt .
RUN pip install --upgrade pip
RUN pip install --no-cache-dir -r requirements.txt

# 安裝 Playwright Chromium 瀏覽器核心與系統函式庫
RUN playwright install chromium
RUN playwright install-deps chromium
RUN playwright install
# 複製專案程式碼
COPY main.py .

# 開放 FastAPI 的 8000 Port
EXPOSE 8000

# 啟動微服務
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
