# NexBot

디스코드 봇 + 웹 대시보드 + 치지직 통계 서비스 모노레포.

| 경로 | 내용 | 실행 |
| --- | --- | --- |
| `main.py`, `cogs/` | 디스코드 봇 (discord.py) | `python main.py` |
| `web/backend/` | FastAPI 대시보드/통계 API | `cd web/backend && uvicorn main:app --reload` |
| `web/frontend/` | Next.js 대시보드 | `cd web/frontend && npm run dev` |
| `relay/relay.py` | 치지직 커뮤니티 폴링 릴레이(한국 IP 호스트 전용) | `python relay/relay.py` |

봇과 백엔드는 별도 프로세스지만 **같은 SQLite 파일(`bot.db`, WAL)** 을 공유한다.
스키마는 `database/db.py` 한 곳이 소유하며 마이그레이션 프레임워크는 쓰지 않는다
(변경분을 파일 끝 SQL 리스트에 append 하는 방식). 자세한 구조는 `CLAUDE.md` 참고.

테스트: `pip install -r requirements-dev.txt && pytest`

---

## 치지직 첫 방송일 수집 (channel history)

스트리머 상세 페이지의 "첫 방송"은 원래 **다시보기(VOD) 목록의 최고령 영상 날짜로 역산한
추정치**였다. 치지직 **공식 Open API에 개설일·첫방송일 필드가 없기 때문**인데, VOD를 지운
채널은 실제보다 한참 늦게 나오는 문제가 있었다.

지금은 치지직 채널 정보 화면이 실제로 쓰는 엔드포인트에서 **정확한 값**을 가져온다.

### 사용하는 API

```
GET https://api.chzzk.naver.com/service/v1/channels/{channelId}/data?fields=channelHistory
```

```json
{
  "code": 200,
  "content": {
    "channelHistory": { "firstLiveDate": "2025-01-14 22:19:58", "totalLiveHours": 4 }
  }
}
```

- `content.channelHistory.firstLiveDate` — 최초 방송 시각. `"YYYY-MM-DD HH:mm:ss"`, **KST**로 취급한다.
- `content.channelHistory.totalLiveHours` — 누적 방송 시간(정수 시간).
- `channelHistory`가 `null`이면 방송 기록이 없는 채널(`NO_HISTORY`).

채널 이름이 DB/요청 어디에도 없을 때만 추가로 한 번 더 부른다
(`GET /service/v1/channels/{channelId}` → `content.channelName`).
이름을 이미 알고 있으면 이 요청을 생략해 **채널당 외부 요청 1회**로 끝난다.

> ### ⚠️ 비문서화 엔드포인트 주의
>
> - 위 엔드포인트는 **치지직 공식 Open API가 아니라 웹 프론트가 쓰는 내부 API**다.
>   공개 규격이 없으므로 **예고 없이 응답 형태가 바뀌거나, 차단되거나, 사라질 수 있다.**
> - 응답 스키마가 바뀌면 이 코드는 `SchemaError`로 처리해 **기존 캐시를 지우지 않고**
>   경고 로그만 남긴다. 서비스는 자동으로 VOD 추정 방식으로 후퇴한다.
> - **프록시·IP 순환·CAPTCHA 우회 등 차단 회피 기능은 의도적으로 구현하지 않았다.**
>   403이 반복되면 수집을 멈추고 경고를 남기는 것이 정책이다.
> - **자동화 수집이 네이버/치지직의 이용약관·robots 정책에 부합하는지는 운영자가 별도로
>   검토해야 한다.** 이 문서는 기술적 동작만 설명하며 법적/약관상 적법성을 보증하지 않는다.

### 채널 ID 처리

다음 입력을 모두 받아 32자리 16진수 채널 ID(소문자)로 정규화한다.

- `4b8f70248caa6f086ceec07aad69a5cc`
- `https://chzzk.naver.com/4b8f70248caa6f086ceec07aad69a5cc`
- `https://chzzk.naver.com/4b8f70248caa6f086ceec07aad69a5cc/about`
- `https://chzzk.naver.com/live/4b8f70248caa6f086ceec07aad69a5cc`

URL은 호스트가 `chzzk.naver.com`인지 확인하고 경로 세그먼트가 정확히 32자 16진수인 것만
받는다. 형식이 틀리면 **외부 API를 호출하지 않고** 400을 돌려준다.

### API 사용 예제

**단일 채널**

```bash
curl -X POST https://<backend>/api/chzzk/channel-history \
  -H 'Content-Type: application/json' \
  -d '{"channel": "https://chzzk.naver.com/4b8f70248caa6f086ceec07aad69a5cc/about",
       "refresh": false}'
```

```json
{
  "channelId": "4b8f70248caa6f086ceec07aad69a5cc",
  "channelName": "피라냠",
  "firstLiveDate": "2025-01-14 22:19:58",
  "firstLiveDateIso": "2025-01-14T22:19:58+09:00",
  "totalLiveHours": 4,
  "source": "CHZZK_CHANNEL_HISTORY",
  "cached": false,
  "collectedAt": "2026-07-27T14:03:11+09:00",
  "status": "OK",
  "stale": false
}
```

**여러 채널 (배치)**

```bash
curl -X POST https://<backend>/api/chzzk/channel-history/batch \
  -H 'Content-Type: application/json' \
  -d '{"channels": ["4b8f70248caa6f086ceec07aad69a5cc",
                    "https://chzzk.naver.com/live/<other-id>"],
       "refresh": false}'
```

같은 채널 ID는 중복 제거하고, 동시성·초당 요청수 제한을 통과한 만큼만 외부로 나간다
(한 번에 최대 `CHZZK_MAX_BATCH_SIZE`개, 기본 100).

**운영 지표**

```bash
curl https://<backend>/api/chzzk/channel-history/metrics
```

성공 횟수 / 캐시 적중률 / 404·403·429 횟수 / 외부 API 평균 응답시간 / 재시도 횟수 /
대기 중인 배치 작업 수(`batch_pending`) / 차단 쿨다운 종료 시각을 돌려준다.

### `status` 값

| status | 의미 |
| --- | --- |
| `OK` | 첫 방송일 확보 |
| `NO_HISTORY` | `channelHistory=null` — 방송 기록 없음 (24시간 후 재확인) |
| `NOT_FOUND` | 404 |
| `BLOCKED` | 403 — 즉시 재시도하지 않고 쿨다운 |
| `ERROR` | 5xx / timeout / 스키마 오류 |
| `INVALID` | 채널 ID 형식 오류 (배치 응답에서만, 외부 호출 없음) |

응답의 `stale: true`는 **외부 호출이 실패해 이전에 저장해 둔 값을 돌려줬다**는 뜻이다.

### 캐시 및 속도 제한 정책

캐시는 `chzzk_channel_history` 테이블(SQLite)에 저장한다.

1. DB에 `first_live_date`가 있으면 **외부를 부르지 않고 즉시 반환**한다(첫 방송일은 변하지 않는다).
2. `refresh=true`일 때만 강제 재조회.
3. `NO_HISTORY`는 24시간(`CHZZK_NO_HISTORY_TTL_HOURS`) 후 재확인한다.
4. `totalLiveHours`는 **하루 최대 1회**만 갱신한다(`CHZZK_TOTAL_HOURS_TTL_HOURS`).
   공개 스트리머 페이지는 응답 지연을 만들지 않으려고 이 갱신도 건너뛴다
   (`refresh_stale_total=False`) — 갱신은 위 수집 API를 부를 때 일어난다.
5. 같은 채널에 동시에 여러 요청이 들어오면 **single-flight**로 외부 호출은 1회만 나간다.
6. **캐시된 정상 `firstLiveDate`는 외부 API 장애가 나도 삭제하지 않는다.** 실패는
   `last_error`/`last_attempt_at`에만 기록하고 값과 `status=OK`는 보존한다.

외부 요청 정책:

- `User-Agent: NexBot-CHZZKCollector/1.0`, `Accept: application/json` (브라우저 위장 안 함)
- timeout 10초, 총 시도 3회, 동시 3개, 초당 2회
- **429/5xx/timeout만 재시도** — 400/401/403/404는 재시도하지 않는다
- 재시도 간격은 지수 백오프 + 랜덤 지터, `Retry-After` 헤더가 있으면 그 값을 우선 사용
- 403이 연속 3회면 30분간 수집을 멈추고 경고 로그를 남긴다

로그는 구조화 JSON 한 줄(`[chzzk_history] {...}`)로 남기며 `channel_id`, `cache_hit`,
`http_status`, `duration_ms`, `retries`, `error_kind`, `job_id`만 기록한다 —
**API 응답 본문 전체는 저장하거나 로깅하지 않는다.**

### Railway 환경변수

전부 선택 사항이며, 없으면 아래 기본값으로 동작한다.

| 변수 | 기본값 | 설명 |
| --- | --- | --- |
| `CHZZK_REQUESTS_PER_SECOND` | `2` | 전역 초당 요청수 상한 (0이면 제한 없음) |
| `CHZZK_MAX_CONCURRENCY` | `3` | 동시 외부 요청 수 |
| `CHZZK_REQUEST_TIMEOUT_SECONDS` | `10` | 요청 타임아웃 |
| `CHZZK_MAX_RETRIES` | `3` | 한 요청의 총 시도 횟수(첫 시도 포함) |
| `CHZZK_NO_HISTORY_TTL_HOURS` | `24` | `NO_HISTORY` 재확인 주기 |
| `CHZZK_TOTAL_HOURS_TTL_HOURS` | `24` | 누적 방송시간 갱신 주기 |
| `CHZZK_NOT_FOUND_TTL_HOURS` | `24` | `NOT_FOUND` 재확인 주기 |
| `CHZZK_ERROR_RETRY_MINUTES` | `10` | 일반 오류 재시도 간격 |
| `CHZZK_BLOCKED_THRESHOLD` | `3` | 연속 403 몇 회에서 수집을 멈출지 |
| `CHZZK_BLOCKED_COOLDOWN_MINUTES` | `30` | 차단 쿨다운 |
| `CHZZK_BACKOFF_BASE_SECONDS` | `0.5` | 지수 백오프 기준값 |
| `CHZZK_BACKOFF_MAX_SECONDS` | `30` | 백오프 상한 |
| `CHZZK_MAX_BATCH_SIZE` | `100` | 배치 1회 최대 채널 수 |
| `CHZZK_USER_AGENT` | `NexBot-CHZZKCollector/1.0` | 요청 UA |

배포 관련:

- 서버 측 호출이므로 **CORS 우회 코드는 없다.** 브라우저에서 직접 치지직을 부르지 않는다.
- 치지직 API는 한국 외 IP에서 응답이 달라지거나 막힐 수 있다. Railway 서비스는
  **Singapore 리전**이 지리적으로 가장 가깝다 (Railway 대시보드 → Service → Settings →
  Regions, 또는 `railway.toml`의 배포 설정). 그래도 막히면 `relay/relay.py`처럼
  한국 IP 호스트에서 도는 별도 프로세스로 옮기는 것이 정공법이다.
- **Railway의 송신 IP는 공유되거나 바뀔 수 있으므로 IP에 의존하는 설계를 하지 않는다.**
  Pro 플랜의 고정 송신 IP는 선택 사항이며, **차단 우회 목적으로 쓰지 않는다.**
- 캐시는 DB에 있으므로 **재시작해도 수집 결과는 유실되지 않는다.** 다만 single-flight는
  프로세스 내부 락이라 **replica를 2개 이상으로 늘리면** 같은 채널을 동시에 부를 수 있다
  (`channel_id` PK가 중복 행은 막지만 중복 요청은 막지 못한다). 그 경우 분산 락 도입 전까지는
  replica 1개를 유지하는 편이 안전하다.

### 403/429가 늘어날 때 운영자가 할 일

1. `GET /api/chzzk/channel-history/metrics`에서 `forbidden` / `rate_limited` 추이와
   `blocked_until`을 확인한다.
2. **먼저 요청량을 줄인다** — `CHZZK_REQUESTS_PER_SECOND`를 1 이하로,
   `CHZZK_MAX_CONCURRENCY`를 1~2로 내리고 재배포한다.
3. 배치 수집을 잠시 멈추고, 캐시 적중률(`cache_hit_rate`)이 낮다면 불필요한 `refresh=true`
   호출이 있는지 확인한다.
4. 403이 계속되면 수집은 자동으로 멈춘다. **우회를 시도하지 말고**, 스트리머 페이지가
   VOD 추정으로 후퇴해 정상 동작하는지만 확인한 뒤 상황을 지켜본다.
5. 응답 구조가 바뀐 경우(`schema_errors` 증가) 위 엔드포인트 응답을 직접 한 번 확인하고
   `parse_channel_history()`를 수정한다.

### 테스트

```bash
pip install -r requirements-dev.txt

pytest                                        # mock 테스트만 (외부 호출 없음)
CHZZK_LIVE_TESTS=1 pytest tests/integration -m integration   # 실제 치지직 API 호출
```

통합 테스트 채널은 `4b8f70248caa6f086ceec07aad69a5cc`이며 `firstLiveDate`는
`2025-01-14 22:19:58`로 고정 검증한다. `totalLiveHours`는 방송할수록 늘어나므로
고정값으로 검증하지 않는다.
