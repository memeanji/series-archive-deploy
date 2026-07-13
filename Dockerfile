# Series Archive — Playwright 브라우저 포함 이미지(크롤링 동작)
FROM mcr.microsoft.com/playwright/python:v1.47.0-jammy

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && playwright install chromium

COPY . .

# 데이터(SQLite)·시크릿은 볼륨/환경변수로 주입 권장
#   docker run -p 8530:8530 \
#     -v $PWD/data:/app/data \
#     -e YOUTUBE_API_KEY=... -e APIFY_TOKEN=... \
#     series-archive
EXPOSE 8530
CMD ["streamlit", "run", "app.py", \
     "--server.address", "0.0.0.0", "--server.port", "8530", "--server.headless", "true"]
