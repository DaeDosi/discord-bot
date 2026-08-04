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
