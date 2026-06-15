FROM python:3.12-slim

WORKDIR /app

# 의존성 설치 (bot + web backend 동일)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# cache-bust: 20260616-12
COPY . .

RUN chmod +x /app/start.sh

# 봇 + 웹 API 통합 실행
CMD ["/app/start.sh"]
