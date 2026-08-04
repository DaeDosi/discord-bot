# 싱드컵 한국 조회수 복구 poller (AWS 서울)

## 왜 있나

하트는 치지직 카드 응답의 `interaction.emotion.reactions`에 있고 조회수는
`content.vod.count`에 있다. `krOnlyViewing=true`(한국 전용 재생) 클립을 Railway
해외 리전에서 부르면 **HTTP는 200인데 `content.vod` 블록이 통째로 빠진다**.
하트 블록은 남으므로 하트만 갱신되고 조회수는 계속 못 읽는다.

**조회수가 0으로 응답된 것이 아니라 조회수 컨테이너가 응답에서 누락된 것**이다.
그래서 상태는 `observed_zero`(정상 관측된 진짜 0)가 아니라 `unknown`이고,
0으로 저장해서는 안 된다. 한국에서 같은 API를 불러 `content.vod.count`를
받아야만 복구된다. 그것이 이 프로세스가 존재하는 유일한 이유다.

## 하지 않는 것

- 인바운드 포트를 열지 않는다 — 서버가 아니다. outbound HTTPS만 쓴다.
- 운영 DB에 접근하지 않는다. DB 경로도 접속 문자열도 SQL도 갖고 있지 않다.
- 값을 판정하지 않는다. 검증과 저장은 전부 Railway가 한다.
- 무한 재시도를 하지 않는다. 남은 일은 lease 만료 후 다음 회차가 가져간다.
- 표준 라이브러리만 쓴다 — EC2에 패키지를 설치하지 않기 위해서다.

## 배포 (아직 실행하지 않았다)

```bash
# 1) 전용 계정과 디렉터리
sudo useradd --system --no-create-home --shell /usr/sbin/nologin krpoller
sudo install -d -o root -g krpoller -m 0750 /opt/krpoller /etc/krpoller

# 2) 코드
sudo install -o root -g krpoller -m 0640 kr_poller.py /opt/krpoller/kr_poller.py

# 3) 환경 파일 — secret은 여기에만 둔다(명령행 인자로 넘기지 않는다)
sudo install -o root -g krpoller -m 0640 /dev/null /etc/krpoller/env
sudo tee /etc/krpoller/env >/dev/null <<'EOF'
KRP_API_BASE=https://<railway-backend-host>
SINGCUP_KR_POLLER_SECRET=<사용자가 생성한 값>
KRP_BATCH=25
KRP_RATE_PER_SECOND=1.0
KRP_MAX_RETRIES=2
EOF

# 4) systemd
sudo install -m 0644 singcup-kr-poller.service /etc/systemd/system/
sudo install -m 0644 singcup-kr-poller.timer   /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now singcup-kr-poller.timer

# 5) 확인
systemctl list-timers singcup-kr-poller.timer
journalctl -u singcup-kr-poller -n 50 --no-pager
```

## 제한 시간 (상대별로 다르다)

하나의 `KRP_TIMEOUT_SECONDS`로 묶여 있었고 그래서 첫 운영 회차가 죽었다.
치지직 조회 25건은 전부 200으로 성공했는데 마지막 `POST /results` 응답을
기다리다 10초를 넘겨 `TimeoutError`가 났다. 결과 endpoint는 25건을 순차 검증·
반영한 뒤 `recompute_ranking()`까지 돌리므로 조회 한 건과 시간 규모가 다르다.

**전부 늘리는 것은 오답이다** — 치지직 제한을 키우면 응답 없는 상류 하나가
회차를 잡아먹고 그 시간이 `_get_bounded`의 재시도와 곱해진다.

| 변수 | 기본값 | 범위 | 대상 |
|---|---|---|---|
| `KRP_CHZZK_TIMEOUT_SECONDS` | 10 | 1–60 | 치지직 detail/card 조회 |
| `KRP_CONTROL_TIMEOUT_SECONDS` | 10 | 1–60 | Railway `POST /tasks` |
| `KRP_RESULTS_TIMEOUT_SECONDS` | 60 | 5–180 | Railway `POST /results` |
| `KRP_OBSERVE_BUDGET_SECONDS` | 180 | 30–600 | 관측 단계 전체 예산 |

전부 **미설정이어도 위 기본값으로 동작한다.** 잘못된 값(문자·0·음수·NaN·
Infinity·범위 밖)은 `krp_bad_env`로 **이름만** 남기고 기본값을 쓴다 — 원문은
찍지 않는다(환경변수에 다른 비밀이 잘못 들어갔을 때 그것이 새는 통로가 된다).

구 이름 `KRP_TIMEOUT_SECONDS`도 아직 읽는다. 설정돼 있으면 치지직·tasks의
기본값으로만 쓰이고 **결과 제출에는 영향을 주지 않는다.**

관측 예산이 있는 이유는 상류가 느릴 때 관측만으로 systemd 상한을 다 써
**결과를 제출하지 못한 채 죽는 것**을 막기 위해서다. 예산이 끝나면 그때까지
모은 결과를 제출하고 정상 종료하며, 남은 후보는 lease 만료 후 다음 회차가
가져간다.

**하드 예산이다.** deadline이 `observe()` → `_get_bounded()` → `_get()`까지
내려가고, 각 요청 timeout은 `min(CHZZK_TIMEOUT, 남은 시간)`으로, backoff와 rate
sleep도 남은 시간으로 잘린다. 남은 시간이 없으면 새 요청을 시작하지 않는다.
시작 전에만 확인하는 **소프트 제한이면 상한이 아니다** — 마지막 한 건이
detail 3회 + card 3회 + backoff를 통째로 더 돌아 예산을 넘긴다. `videoId`가
없는 클립이 두 경로를 모두 거치므로 초과분이 가장 크다(기본값 기준 +66초).
예산 계산에는 `time.monotonic()`을 쓴다 — NTP 보정이나 시각 변경이 상한을
거짓말로 만들지 않기 위해서다.

예산 종료는 **외부 실패도, 조회수 0도 아니다.** `BudgetExhausted`로 빠져나오고
그 클립은 결과에 담지 않는다. 담아 버리면 관측하지 못한 것이 `partial` 관측으로
굳는다.

### 조합까지 안전해야 한다

개별 범위만 검사하면 허용된 조합이 상한을 넘긴다(control 60 + results 180 +
budget 600 = 840초 > 300초). 그래서 관측 예산은 환경변수를 그대로 쓰지 않고
남는 시간에서 **역산해 잘라 낸다**. 잘릴 때는 `krp_budget_clamped`를 남긴다.

```
OBSERVE_BUDGET = max(5, min(요청값, 300 − CONTROL − RESULTS − 20))
```

불변식: `CONTROL + OBSERVE_BUDGET + RESULTS + 여유 20초 ≤ TimeoutStartSec 300초
< 서버 lease 600초`. 모든 허용 조합에서 성립한다.

| 조합 | CONTROL | OBSERVE | RESULTS | 합계(+20) |
|---|---|---|---|---|
| 기본값 | 10 | **180** | 60 | 270 ≤ 300 |
| 최대치 요청 | 60 | **40** (600→clamp) | 180 | 300 ≤ 300 |
| 최소치 | 1 | 30 | 5 | 56 ≤ 300 |

## 만들지 않는 AWS 리소스

Elastic IP · NAT Gateway · Load Balancer · RDS · Secrets Manager ·
CloudWatch Logs 유료 구성 · 80/443 인바운드 · 0.0.0.0/0 SSH.
기존 서울 EC2 1대와 기존 EBS만 쓴다. 신규 과금 리소스는 0건이다.

## 로그

`clipUid` · 상태 · 소요시간(ms) · 시도 횟수만 남긴다.
URL 쿼리 · 서명 · nonce · secret · 토큰 · 이메일 · IP · 전체 응답 본문은
남기지 않는다(journald는 평문이고 이 값들은 한 번 새면 회수할 수 없다).

## 중지 / 롤백

```bash
sudo systemctl disable --now singcup-kr-poller.timer
```

Railway 쪽은 `SINGCUP_KRP_ENABLED=false` 한 줄이면 endpoint가 503만 돌려주므로
코드를 되돌리지 않아도 즉시 이전 동작이 된다.
