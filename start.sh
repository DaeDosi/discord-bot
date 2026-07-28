#!/bin/sh
# 봇과 웹 API를 같은 컨테이너에서 실행 → 동일한 bot.db 공유
# DATABASE_URL=/data/bot.db (Railway Persistent Volume)

echo "=== NexBot unified startup ==="
# DATABASE_URL 전체를 찍지 않는다. 지금은 SQLite 파일 경로라 무해하지만, PostgreSQL로
# 옮기면 같은 줄이 비밀번호가 든 접속 문자열을 배포 로그에 그대로 남기게 된다.
# 진단에 필요한 건 '어떤 백엔드/어느 경로인가'뿐이라 스킴과 마지막 조각만 남긴다.
if [ -z "${DATABASE_URL}" ]; then
  echo "DATABASE_URL: not set (using ./bot.db)"
else
  echo "DATABASE_URL: $(printf '%s' "${DATABASE_URL}" | sed -E 's#^([a-z+]+://)?.*/#\1***/#')"
fi

# Discord 봇을 백그라운드에서 실행
python /app/main.py &
BOT_PID=$!
echo "Discord bot started (PID=$BOT_PID)"

# FastAPI 백엔드를 포어그라운드에서 실행 (Railway가 $PORT로 HTTP 라우팅)
cd /app/web/backend
echo "Starting web API on port ${PORT:-8000}..."
exec python -m uvicorn main:app --host 0.0.0.0 --port "${PORT:-8000}"
