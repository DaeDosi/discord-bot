#!/bin/sh
# 봇과 웹 API를 같은 컨테이너에서 실행 → 동일한 bot.db 공유
# DATABASE_URL=/data/bot.db (Railway Persistent Volume)

echo "=== NexBot unified startup ==="
echo "DATABASE_URL: ${DATABASE_URL:-not set, using ./bot.db}"

# Discord 봇을 백그라운드에서 실행
python /app/main.py &
BOT_PID=$!
echo "Discord bot started (PID=$BOT_PID)"

# FastAPI 백엔드를 포어그라운드에서 실행 (Railway가 $PORT로 HTTP 라우팅)
cd /app/web/backend
echo "Starting web API on port ${PORT:-8000}..."
exec python -m uvicorn main:app --host 0.0.0.0 --port "${PORT:-8000}"
