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

외부 비공식 API를 쓰는 기능이 두 개 있습니다 — 아래 두 절을 꼭 읽어 주세요:
[치지직 첫 방송일 수집](#치지직-첫-방송일-수집-channel-history) · [싱드컵 이벤트 수집](#싱드컵-이벤트-수집)

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

### 백그라운드 백필

`/stats`의 **신규 & 초기 분석** 탭이 "첫 방송 60일 이내"로 필터링하려면 채널마다
`first_live_date`가 있어야 한다. 요청 경로에서 모으면 첫 방문자가 수백 번의 외부 호출을
기다리게 되므로, 백엔드가 뜰 때 백필 루프(`start_history_backfill`)를 함께 띄운다.

- `rising_channel_stats`에서 **아직 `first_live_date`가 없는 채널**을 최근 본 순서로
  사이클당 `CHZZK_HISTORY_BACKFILL_BATCH`개씩 수집한다.
- 채널명을 함께 넘기므로 **채널당 외부 요청 1회**로 끝나고, 첫 방송일은 변하지 않으므로
  한 번 성공하면 그 채널은 다시 건드리지 않는다.
- 속도 제한·동시성·403 쿨다운은 단일 조회와 동일한 경로를 그대로 탄다.
- 백필이 아직 닿지 않은 채널은 대시보드에서 `rising_channel_stats.first_seen`
  (NexBot 최초 트랙킹 일자)으로 보완하고, 그런 행은 `first_stream_source: "TRACKED"`로
  표시해 프론트가 '추적 N일차'라고 구분해 보여 준다.

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
| `CHZZK_HISTORY_BACKFILL` | `1` | 백그라운드 백필 사용 (`0`이면 끔) |
| `CHZZK_HISTORY_BACKFILL_INTERVAL` | `300` | 백필 사이클 간격(초) |
| `CHZZK_HISTORY_BACKFILL_BATCH` | `60` | 사이클당 채널 수 |
| `CHZZK_HISTORY_BACKFILL_DELAY` | `90` | 부팅 후 첫 사이클까지 유예(초) |

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

---

## 싱드컵 이벤트 수집

치지직에서 진행하는 **싱드컵** 참가 게시글을 네이버 게임 '치지직 라운지' 자유게시판에서
모아 **버프 수 순위**를 보여 줍니다. `/stats` 좌측 메뉴의 `싱드컵 [EVENT]`에서 볼 수 있습니다.

### 사용하는 API

```
GET https://comm-api.game.naver.com/nng_main/v1/community/lounge/chzzk/feed
    ?offset=<페이지번호>&limit=30&order=NEW&boardId=4&buffFilteringYN=N
```

> ### ⚠️ 두 가지 함정
>
> - **`offset`은 글 개수가 아니라 페이지 번호입니다.** 다음 페이지는 `offset + 1`이지
>   `offset + 30`이 아닙니다. 30씩 더하면 게시글을 대량으로 건너뜁니다.
> - **`limit`은 30이 상한입니다.** 100 등을 넣으면 400이 납니다. 올리지 마세요.

이 엔드포인트도 [첫 방송일 수집](#치지직-첫-방송일-수집-channel-history)과 마찬가지로
**공식 Open API가 아닌 웹 내부 API**입니다. 예고 없이 형태가 바뀌거나 막힐 수 있고,
**자동화 수집이 네이버 이용약관에 부합하는지는 운영자가 별도로 검토해야 합니다.**
네이버 로그인 쿠키·세션은 쓰지 않으며(비로그인으로 조회됩니다), 프론트엔드가 네이버를
직접 부르지 않고 반드시 우리 백엔드를 거칩니다. CORS 우회 프록시나 브라우저 자동화는
사용하지 않습니다.

### 참가작 판별

제목을 **HTML entity 디코딩 → 유니코드 정규화(NFKC) → 앞뒤 공백 제거**한 뒤
`^\s*\[\s*싱드컵\s*\]` 에 맞는 글만 참가작으로 봅니다.
제목에 "싱드컵"이라는 단어만 있고 `[싱드컵]` 말머리가 없으면 제외합니다.

추가 조건: `board.boardId == 4` · 작성 시각이 이벤트 기간(KST) 안 · 클린봇 숨김 아님 ·
게시글 ID 정상.

`feed.createdDate`는 `YYYYMMDDHHmmss`(KST) 문자열이며 파싱해 epoch로 저장합니다.
날짜 문자열이 깨진 게시글은 **그 글만** 건너뛰고 경고 로그를 남깁니다.

### 클립 URL

본문(`feed.contents`, JSON 문자열)을 파싱해 구조를 **재귀 순회**하며
`https://chzzk.naver.com/clips/{id}` 형태를 찾습니다. `textNode.value` / `textNode.link.url` /
`oglink.link` 등 어디에 있든 잡히고, 배열 인덱스에 의존하지 않습니다. 중복은 제거하고
첫 번째를 대표 클립으로 씁니다. 본문 JSON 파싱에 실패해도 게시글은 저장하며,
원문 문자열에서라도 URL을 건집니다.

### 순위 규칙

정렬은 **버프 내림차순 → 조회수 내림차순 → 작성 시각 오름차순 → feedId 오름차순**입니다.
버프는 바깥쪽 `buff.buffCount`를 씁니다(`feed.buff`도 있지만 섞지 않습니다).

작성자 중복 제거는 **`user.userIdHash`** 기준입니다. 닉네임은 바뀌거나 겹칠 수 있어
쓰지 않습니다. 한 작성자가 여러 편을 올렸으면 합산하지 않고 **가장 잘 된 한 편만** 순위에
넣습니다. `userIdHash`가 없는 글은 닉네임으로 합치지 않고 `feed:{feedId}` 임시 키를 씁니다
(잘못된 병합이 누락보다 나쁩니다).

### 수집 흐름

1. `offset=0`부터 한 페이지씩 **순차** 호출(동시 요청 없음, 페이지 사이 짧은 간격).
2. 각 게시글을 정규화 → 참가작이면 `feedId` 기준 **upsert**.
3. `offset += 1`.
4. 종료 조건: 빈 페이지 / 페이지 전체가 이벤트 시작 이전(`order=NEW`라 최신순) /
   `SINGCUP_MAX_PAGES` 도달 / 동일 페이지 반복 감지.
5. **이벤트 구간을 끝까지 확인한 회차(full scan)에서만** 이번에 안 보인 글의
   `missing_scan_count`를 올리고, 연속 2회 누락일 때만 `active=0`으로 내립니다.
   원본 API의 일시적 누락으로 순위가 사라지지 않게 하기 위한 2단계 처리입니다.

**수집에 실패해도 DB의 기존 순위는 절대 지우지 않습니다.** 응답 스키마가 깨지면
'데이터 없음'이 아니라 **수집 실패**로 처리하고 마지막 정상 데이터를 계속 제공합니다.

### 상태 코드별 처리

| 상태 | 처리 |
| --- | --- |
| 400 | 파라미터/스펙 변경 신호 — **재시도하지 않음**, `SCHEMA_ERROR`로 기록 |
| 401 / 403 | 접근 거부 — 재시도하지 않고 `BLOCKED`로 표시(운영자 확인 필요) |
| 404 | 경로 변경 가능성 — 재시도하지 않고 `SCHEMA_ERROR` |
| 408 / 429 / 5xx / timeout | 최대 3회 재시도, 지수 백오프 + 지터, `Retry-After` 우선 |

숫자 필드는 안전하게 정수화합니다(null·변환 불가 → 0, 음수 → 0으로 보정하고 경고).
게시글 한 건의 오류가 전체 수집을 멈추지 않습니다.

### API

```bash
curl https://<backend>/api/singcup/rankings          # 순위(공개)
curl https://<backend>/api/singcup/status            # 수집기 진단(공개)

# 아래는 모두 SINGCUP_ADMIN_SECRET 헤더 필요
S='-H "X-Singcup-Secret: <secret>"'

# 정기 수집과 동일한 1회 실행
curl -X POST 'https://<backend>/api/singcup/collect' -H 'X-Singcup-Secret: <secret>'

# DB에 쓰지 않고 몇 건이 잡히는지만 확인
curl -X POST 'https://<backend>/api/singcup/collect?mode=dry-run' -H 'X-Singcup-Secret: <secret>'

# 과거 구간을 깊게 훑기(이벤트 시작일을 앞당긴 뒤 1회). upsert라 재실행해도 안전
curl -X POST 'https://<backend>/api/singcup/collect?mode=backfill' -H 'X-Singcup-Secret: <secret>'

# 이벤트 기간 밖 행 확인(기본 dry-run) → 실제 적용
curl -X POST 'https://<backend>/api/singcup/prune' -H 'X-Singcup-Secret: <secret>'
curl -X POST 'https://<backend>/api/singcup/prune?dry_run=false' -H 'X-Singcup-Secret: <secret>'
```

### 실행 모드

| mode | 동작 |
| --- | --- |
| `normal` | 정기 수집(기본). `offset=0`부터 이벤트 시작일 이전 페이지가 나올 때까지 순회 |
| `backfill` | 같은 순회지만 페이지 상한이 `SINGCUP_BACKFILL_MAX_PAGES`. **이벤트 시작일을 앞당긴 뒤 1회 실행** |
| `dry-run` | 순회·판별만 하고 **DB에 아무것도 쓰지 않음**(수집 이력도 남기지 않음) |

세 모드 모두 순회 로직은 같습니다 — 최적화를 이유로 페이지를 건너뛰지 않으므로
이벤트 기간 내 게시글의 버프/조회수 갱신이 누락되지 않습니다.

`POST /prune`은 이벤트 기간을 벗어난 행을 **삭제하지 않고 `active=0`으로만** 내리며,
기본값이 dry-run이라 대상을 먼저 확인한 뒤 적용하게 되어 있습니다. `singcup_feeds`의
해당 `event_id` 행만 건드리고 다른 테이블·이벤트는 수정하지 않습니다.

`secret`이 설정되지 않은 배포에서는 수동 수집이 **503으로 아예 막힙니다**(빈 값과 일치해
열리는 사고 방지). secret은 프론트엔드에 노출하지 않으며 로그에도 남기지 않습니다.

### 수집 주기와 중복 실행 방지

백엔드가 뜰 때 `start_singcup_collector()`가 함께 돌며, 이벤트 상태에 따라 스스로 조절합니다.

- 시작 전: 시작 시각까지 대기(최대 30분 간격으로 확인)
- 진행 중: `SINGCUP_COLLECT_INTERVAL_MINUTES`(기본 3분)
- 종료 후: `SINGCUP_POST_EVENT_HOURS`(기본 24시간) 동안만 1시간 간격으로 최종 검산, 이후 중단

**Railway replica가 여러 개여도 중복 수집되지 않습니다** — `singcup_collect_lock` 테이블에
조건부 UPDATE(rowcount로 획득 판정)로 분산 락을 겁니다. 락 TTL은 `SINGCUP_MAX_RUN_SECONDS`와
같아, 프로세스가 죽어도 그 시간 뒤 자동으로 풀립니다. 이전 수집이 끝나지 않았으면 새 작업은
`SKIPPED`로 건너뜁니다.

별도 Cron이 필요 없지만, Railway Cron Job으로 돌리고 싶다면 위 `POST /api/singcup/collect`를
호출하면 됩니다(락이 같이 걸리므로 내장 루프와 같이 써도 안전합니다).

### 환경변수

| 변수 | 기본값 | 설명 |
| --- | --- | --- |
| `SINGCUP_ENABLED` | `true` | `false`면 수집 루프를 띄우지 않음(조회 API는 그대로 동작) |
| `SINGCUP_EVENT_ID` | `singcup-2026` | 이벤트 식별자(DB 행 구분) |
| `SINGCUP_EVENT_NAME` | `싱드컵` | 화면 표기명 |
| `SINGCUP_START_AT` | `2026-07-20T00:00:00+09:00` | 시작(KST) |
| `SINGCUP_END_AT` | `2026-08-09T23:59:59+09:00` | 종료(KST) |
| `SINGCUP_COLLECT_INTERVAL_MINUTES` | `3` | 진행 중 수집 주기 |
| `SINGCUP_MAX_PAGES` | `100` | 정기 수집 최대 페이지 수 |
| `SINGCUP_BACKFILL_MAX_PAGES` | `300` | `mode=backfill` 최대 페이지 수 |
| `SINGCUP_REQUEST_TIMEOUT_MS` | `10000` | 요청 타임아웃 |
| `SINGCUP_MAX_RETRIES` | `3` | 재시도 가능한 오류의 총 시도 횟수 |
| `SINGCUP_MAX_RUN_SECONDS` | `300` | 한 회차 최대 실행 시간(= 락 TTL) |
| `SINGCUP_STALE_AFTER_MINUTES` | `20` | 이 시간을 넘기면 화면에 '집계 지연' 표시 |
| `SINGCUP_MISSING_SCANS` | `2` | 연속 몇 회 누락에서 비활성 처리할지 |
| `SINGCUP_POST_EVENT_HOURS` | `24` | 종료 후 최종 검산 기간 |
| `SINGCUP_ADMIN_SECRET` | (없음) | 수동 수집 인증. **비워두면 수동 수집이 막힙니다** |

#### 클립 수집(메인/랭킹) 전용

| 변수 | 기본값 | 설명 |
| --- | --- | --- |
| `SINGCUP_CLIP_INTERVAL_MINUTES` | `4` | 클립 수집 주기 |
| `SINGCUP_CLIP_MAX_PAGES` | `200` | 클립 목록 최대 페이지(07-20까지 실측 113페이지) |
| `SINGCUP_NEW_SCAN_PER_CYCLE` | `400` | 사이클당 **신규** 클립 카드 조회 상한 |
| `SINGCUP_REFRESH_PER_CYCLE` | `60` | 사이클당 기존 클립 수치 갱신 상한 |
| `SINGCUP_METRICS_TTL_MINUTES` | `20` | 하트/조회수 갱신 주기 |
| `SINGCUP_RESCAN_UNTAGGED_HOURS` | `24` | 태그 없던 클립 재확인 주기 |
| `SINGCUP_CARD_CONCURRENCY` | `4` | 카드 API 동시 요청 수 |
| `SINGCUP_CHANNEL_TTL_MINUTES` | `20` | 채널(팔로워) 캐시 |

**카드 API는 클립 1건당 1회**라 호출량 제어가 핵심입니다. 이벤트 시작을 07-20으로 두면
후보가 5,500건이 넘으므로, 첫 적재는 사이클당 400건씩 나눠 처리합니다(약 14사이클 ≈ 1시간).
못 훑은 클립은 scan 기록이 남지 않아 다음 사이클에 자연히 이어집니다. **backlog가 남아
있는 동안에는 클립 비활성화(missing 처리)를 하지 않습니다** — 전체를 확인한 상태가
아니어서 멀쩡한 클립이 사라질 수 있기 때문입니다.

이벤트 기간은 코드에 흩어 두지 않고 위 두 환경변수(또는 `singcup_collector`의 기본값)
한 곳에서만 관리합니다.

### 403/429가 늘어날 때

1. `GET /api/singcup/status`의 `recentRuns`에서 `status`를 확인합니다
   (`BLOCKED`면 IP/정책, `FAILED`면 일시 장애, `SCHEMA_ERROR`면 API 변경).
2. `SINGCUP_COLLECT_INTERVAL_MINUTES`를 늘려 요청량을 줄입니다.
3. **우회를 시도하지 마세요.** 순위 페이지는 마지막 정상 데이터로 계속 동작하며
   화면에 '집계 지연'이 표시됩니다.
4. Railway에서 네이버 접근이 되는지 확인하려면:
   `SINGCUP_LIVE_TESTS=1 pytest tests/integration -m integration`

### 테스트

```bash
pytest tests/test_singcup.py                                    # mock (외부 호출 없음)
SINGCUP_LIVE_TESTS=1 pytest tests/integration -m integration     # 실제 라운지 API
```

---

## 성능 · 보안 (2026-07-28)

### 측정 환경

프로덕션 부하 테스트는 하지 않는다. 로컬에 **운영 규모 스테이징 데이터셋**을 만들어 잰다
(채널 3,000 / 롤업 478,877행 / 스냅샷 315,619행 — 코드 주석의 QA 기준 "롤업 50만 행"에 맞춤).

관측 장치:

- `timing.py` — 모든 응답에 `Server-Timing: total;dur=..` 헤더. 느린 요청(`SLOW_REQUEST_MS`,
  기본 1000ms)은 `[slow]` 로그로 남는다.
- `GET /api/internal/timing` — 경로별 P50/P95/P99 (secret 필요).

### 측정으로 확인한 병목과 조치

| 구간 | 조치 전 | 조치 후 | 조치 |
| --- | --- | --- | --- |
| 빈집 타임 집계(7일) | 2,977ms | **392ms** | strftime→정수 연산 + 커버링 인덱스 |
| rank_daily 윈도우 함수(30일) | 1,266ms | **795ms** | 날짜 GROUP BY를 정수 일련번호로 |
| 시간대 히트맵(24h) | 442ms | 421ms | strftime→정수 연산 |
| `newcomers?group=small` (cold) | 3,866ms | **1,562ms** | 위 두 가지 |
| `ranking-period` (warm P95) | 3,156ms | **0ms** | 60초 TTL 캐시 신설 |
| `streamer` (warm P50) | 969ms | 739ms | 위 쿼리 개선 파급 |

**strftime → 정수 연산**: `strftime('%H', ts+32400,'unixepoch')`는 행마다 문자열 포맷팅을
하고 그 문자열로 GROUP BY 해시를 만든다. KST 시/일은 정수로 정확히 같은 값이 나온다.

```sql
시  = ((ts + 32400) / 3600) % 24      -- SQLite에서 / 는 정수 나눗셈
일  = (ts + 32400) / 86400
요일 = ((ts + 32400) / 86400 + 4) % 7  -- 1970-01-01 = 목요일
```

교체 전후가 동일함을 **794,496행 전체에 대해 검증**했다(불일치 0건).

**커버링 인덱스** `idx_rising_roll_cover(hour_ts, avg_viewers, snaps, sum_viewers, chzzk_channel_id)`:
시간 구간 집계가 인덱스만으로 끝나 테이블 접근이 사라진다. 채널별 집계는 기존
`idx_rising_roll_channel`을 계속 쓰므로 영향이 없다. 쓰기 비용은 수집 1회당 롤업 약 3천 행에
인덱스 1개가 추가되는 정도다.

### DB 구조에 대한 사실 정정

이 프로젝트의 DB는 **네트워크 DB가 아니라 같은 컨테이너의 SQLite 파일**(`aiosqlite`, WAL)이다.
따라서 다음 항목은 해당 사항이 없다.

- 백엔드↔DB 리전 차이로 인한 왕복 지연 — **없음**(같은 디스크)
- connection pool 크기/max overflow/replica×pool — **없음**(모듈 전역 단일 커넥션)
- materialized view — SQLite 미지원

대신 SQLite 고유의 제약이 있다: `database/db.py`가 **커넥션 하나를 공유**하므로 모든 쿼리가
aiosqlite의 단일 워커 스레드에서 직렬화된다. 무거운 쿼리 하나가 다른 요청을 막으므로,
위처럼 쿼리 자체를 줄이고 캐시로 실행 횟수를 줄이는 것이 유일하게 유효한 방향이다.

### 보안 조치

| 항목 | 상태 | 내용 |
| --- | --- | --- |
| API 문서 노출 | ✅ | 프로덕션에서 `/docs`·`/redoc`·`/openapi.json` 404 |
| CORS | ✅ | `*` → 정확한 Origin allowlist (부분 일치·null 차단) |
| 보안 헤더 | ✅ | `X-Content-Type-Options`, `Referrer-Policy`, `Permissions-Policy`, `X-Frame-Options: DENY`, HSTS |
| CSP | ⚠️ 부분 | **Report-Only로 시작**. AdSense 도메인만 allowlist. 보고 확인 후 `CSP_ENFORCE=1` |
| 인증 상태코드 | ✅ | 인증 누락이 422 → **401** |
| `?token=` 수용 | ✅ | 콜백에서 쿼리스트링 토큰 폴백 제거(프래그먼트만) |
| 방문자 조작 | ✅ | `POST /api/stats/visit` 분당 5회 제한(`RATE_LIMIT_VISIT`) |
| **localStorage 토큰** | ❌ **미완료** | 아래 참조 |

**localStorage → HttpOnly 쿠키 전환은 하지 않았다.** OAuth 콜백·JWT 발급·프론트 전 API 호출·
대시보드·관리자 페이지를 동시에 바꿔야 하고, 이 저장소에는 E2E 테스트가 없어 Discord 로그인
회귀를 검증할 수단이 없다. 검증 없이 인증을 바꾸면 로그인이 통째로 깨질 위험이 실제 이득보다
크다고 판단했다. 단계적 전환 계획은 `CHANGELOG.md` 참조.

### 추가 환경변수

| 변수 | 기본값 | 설명 |
| --- | --- | --- |
| `PRODUCTION` / `ENV` | (없음) | `1`/`production`이면 문서 비공개 + HSTS |
| `CORS_ALLOW_ORIGINS` | `FRONTEND_URL`, `https://nexbot.shop`, `https://www.nexbot.shop` | 쉼표 구분 allowlist |
| `CSP_ENFORCE` | `0` | `1`이면 CSP를 Report-Only가 아닌 강제로 |
| `RATE_LIMIT_VISIT` | `5` | 방문 집계 분당 상한 |
| `SERVER_TIMING` | `1` | `Server-Timing` 헤더 노출 |
| `SLOW_REQUEST_MS` | `1000` | 느린 요청 로그 임계값 |

### 롤백

- 인덱스: `DROP INDEX idx_rising_roll_cover;` (읽기만 느려지고 데이터는 그대로)
- 캐시: `_PERIOD_TTL = 0`
- 보안 헤더/CORS: `CORS_ALLOW_ORIGINS`에 필요한 Origin 추가, `SecurityHeadersMiddleware`
  `add_middleware` 한 줄 제거
- 쿼리 정수 연산: 되돌릴 필요가 없다(결과가 794,496행 전체에서 동일함을 검증)
