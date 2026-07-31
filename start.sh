#!/bin/sh
# 봇과 웹 API를 같은 컨테이너에서 실행 → 동일한 bot.db 공유
# DATABASE_URL=/data/bot.db (Railway Persistent Volume)
#
# 예전에는 `python main.py &` 뒤에 `exec uvicorn`으로 셸을 대체했다. 그러면 PID 1이
# uvicorn이 되고 **봇에는 SIGTERM이 전달되지 않는다.** 배포로 컨테이너를 내릴 때
# 웹만 정상 종료하고 봇은 강제 종료될 때까지 같은 SQLite 파일에 계속 썼다 —
# 구 프로세스와 새 프로세스가 겹치면 쓰기 잠금이 오래 남는다.
# 이제 셸이 PID 1로 남아 두 자식에게 신호를 전달하고 둘 다 기다린다.

echo "=== NexBot unified startup ==="
# DATABASE_URL 전체를 찍지 않는다. 지금은 SQLite 파일 경로라 무해하지만, PostgreSQL로
# 옮기면 같은 줄이 비밀번호가 든 접속 문자열을 배포 로그에 그대로 남기게 된다.
# 진단에 필요한 건 '어떤 백엔드/어느 경로인가'뿐이라 스킴과 마지막 조각만 남긴다.
if [ -z "${DATABASE_URL}" ]; then
  echo "DATABASE_URL: not set (using ./bot.db)"
else
  echo "DATABASE_URL: $(printf '%s' "${DATABASE_URL}" | sed -E 's#^([a-z+]+://)?.*/#\1***/#')"
fi

GRACE="${SHUTDOWN_GRACE_SECONDS:-15}"

# Discord 봇
DB_PROCESS_ROLE=bot python /app/main.py &
BOT_PID=$!
echo "Discord bot started (PID=$BOT_PID)"

# FastAPI 백엔드 (Railway가 $PORT로 HTTP 라우팅)
cd /app/web/backend || exit 1
echo "Starting web API on port ${PORT:-8000}..."
DB_PROCESS_ROLE=web python -m uvicorn main:app --host 0.0.0.0 --port "${PORT:-8000}" &
WEB_PID=$!
echo "Web API started (PID=$WEB_PID)"

shutting_down=0

terminate() {
  [ "$shutting_down" = "1" ] && return 0
  shutting_down=1
  echo "shutdown: SIGTERM -> bot=$BOT_PID web=$WEB_PID (grace ${GRACE}s)"
  kill -TERM "$BOT_PID" "$WEB_PID" 2>/dev/null
  i=0
  while [ "$i" -lt "$GRACE" ]; do
    if ! kill -0 "$BOT_PID" 2>/dev/null && ! kill -0 "$WEB_PID" 2>/dev/null; then
      echo "shutdown: both exited cleanly"
      return 0
    fi
    i=$((i + 1))
    sleep 1
  done
  echo "shutdown: grace expired -> SIGKILL"
  kill -KILL "$BOT_PID" "$WEB_PID" 2>/dev/null
  return 0
}

trap terminate TERM INT

# 어느 한쪽이 먼저 끝나면 나머지도 내린다 — 봇만 죽은 채 웹이 살아 있으면
# 헬스체크는 통과하는데 수집이 멈춘 상태가 조용히 유지된다.
while :; do
  if ! kill -0 "$BOT_PID" 2>/dev/null; then
    wait "$BOT_PID"; RC=$?
    echo "bot exited rc=$RC"
    terminate
    wait "$WEB_PID" 2>/dev/null
    exit "$RC"
  fi
  if ! kill -0 "$WEB_PID" 2>/dev/null; then
    wait "$WEB_PID"; RC=$?
    echo "web exited rc=$RC"
    terminate
    wait "$BOT_PID" 2>/dev/null
    exit "$RC"
  fi
  sleep 1
done
