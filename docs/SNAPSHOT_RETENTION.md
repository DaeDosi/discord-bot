# 싱드컵 스냅샷 보존정책 운영 문서

## ⚠️ 이 작업은 파괴적입니다

프루닝은 `singcup_snapshots`의 **원본 행을 실제로 삭제**합니다.

> **코드를 revert해도 이미 삭제된 원본은 복구되지 않습니다.**
> revert가 되돌리는 것은 '앞으로 더 지우지 않는 것'뿐입니다.
> 삭제된 구간에 대해 남는 것은 시간별 롤업(`singcup_snapshot_hourly`),
> 일별 롤업(`singcup_snapshot_daily`), 최종 성적(`singcup_final_standings`)입니다.

스키마 추가(신규 테이블·인덱스)는 되돌릴 수 있지만, 데이터 삭제는 되돌릴 수 없습니다.
이 둘을 묶어 "append-only 변경"이라고 부르면 안 됩니다.

## 왜 필요한가

| 항목 | 실측/계산 |
|---|---|
| 행 크기 | 198 bytes (인덱스 2개 포함, 로컬 20만 행 실측) |
| 증가 속도 | 참가자 800명 × 4분 주기 = 288,000행/일 = **55MB/일** |
| 21일 이벤트 | 6,048,000행 = **1,145MB** |
| Railway 볼륨 | 500MB |

`singcup_snapshots`에는 원래 프루닝이 전혀 없었습니다.

## 보존 기간의 근거

원본을 읽는 코드를 전부 확인한 결과, 가장 멀리 소급하는 것은
`singcup_clips._delta_maps()`의 24시간 비교(`collected_at <= now-86400`)입니다.
**그보다 오래된 원본을 읽는 코드는 없습니다.**

→ 원본 **26시간**(24h + 여유 2h), 그 이전은 시간당 1행으로 롤업.

보존 시간을 25시간 미만으로 낮추면 24시간 증감이 깨집니다
(`test_retention_covers_the_24h_lookback`이 이를 고정합니다).

## 환경변수

| 변수 | 기본값 | 설명 |
|---|---|---|
| `SINGCUP_SNAPSHOT_PRUNE_ENABLED` | `false` | **실제 삭제 스위치.** 배포만으로 켜지지 않습니다 |
| `SINGCUP_SNAPSHOT_PRUNE_DRY_RUN` | `true` | true면 아무것도 쓰지/지우지 않고 리포트만 |
| `SINGCUP_SNAPSHOT_RETENTION_HOURS` | `26` | 원본 보존 시간 |
| `SINGCUP_SNAPSHOT_PRUNE_BATCH` | `5000` | 배치당 삭제 행 수 |
| `SINGCUP_SNAPSHOT_PRUNE_SLEEP` | `0.2` | 배치 사이 양보 시간(초) |
| `SINGCUP_SNAPSHOT_PRUNE_MAX_ROWS` | `200000` | 1회 유지보수 삭제 상한 |
| `SINGCUP_HOURLY_RETENTION_DAYS` | `30` | 시간별 → 일별 압축 시점 |
| `SINGCUP_COMPACT_HOURLY_ENABLED` | `false` | 일별 압축 스위치 |
| `SINGCUP_RETENTION_INTERVAL_MINUTES` | `60` | 유지보수 주기 |
| `SINGCUP_RETENTION_WORKER_ENABLED` | `true` | 워커 자체 on/off |

**삭제가 실제로 일어나려면 `ENABLED=true` **와** `DRY_RUN=false`가 모두 필요합니다.**
둘 중 하나라도 아니면 리포트만 나옵니다.

## 활성화 절차 (순서를 지킬 것)

### 1. 백업 — 아직 안 되어 있으면 여기서 멈춥니다

WAL 사용 중이므로 **`.db` 파일만 복사하면 안 됩니다**(WAL에 있는 최신 트랜잭션이 빠집니다).

```sql
-- 온라인 일관 백업. 다른 읽기/쓰기를 막지 않습니다.
VACUUM INTO '/data/backup-YYYYMMDD.db';
```

백업 파일에는 **스트리머 OAuth 토큰과 서버 설정이 들어 있습니다.**
- 권한: `chmod 600`
- 보관 위치: 볼륨 내부(외부 전송 금지)
- 보관 기간: 전환 검증 후 7~14일, 이후 삭제

백업 후 검증:
```
GET /api/admin/db/integrity?full=true    → {"ok": true}
```

### 2. dry-run 리포트 확인

```
GET /api/admin/db/retention/report
```

확인할 값:
- `rollup.rollup_rows` — 생성 예정 롤업 행 수
- `prune.would_delete` — 삭제 예정 원본 행 수
- `prune.oldest_at` / `newest_at` — 삭제 대상 시각 범위
- `prune.per_event` — 이벤트별 삭제 예정 행 수
- `prune.not_rolled_up_rows` — **0이어야 합니다**
- `prune.estimated_reclaim_bytes` — 예상 회수 용량
- `prune.estimated_batches` / `estimated_seconds` — 처리 예상

### 3. 롤업 검증 (실제 쓰기 필요)

`DRY_RUN=false` + `ENABLED=false`로 두면 롤업은 쓰되 **삭제는 하지 않습니다.**
이 상태에서 한 주기 돌린 뒤:

- `verify.mismatched_hours == 0`
- 롤업 값과 원본 마지막 값이 스트리머별로 일치
- 1시간/24시간 증감이 이전과 동일

### 4. 활성화

위가 전부 통과한 뒤에만:
```
SINGCUP_SNAPSHOT_PRUNE_ENABLED=true
SINGCUP_SNAPSHOT_PRUNE_DRY_RUN=false
```

### 5. 활성화 후 관찰

- `[singcup_retention] {"event":"prune_batch", ...}` — 배치별 소요시간·삭제 건수·
  `lock_retries`·`wal_bytes`
- `database is locked` 오류가 늘지 않는지
- API p95가 악화되지 않는지
- `GET /api/admin/db/diagnostics` → `files.db_bytes`, `growth.days_until_full`

## 안전장치 (코드로 강제됨)

1. **기본 비활성** — 배포만으로는 절대 삭제되지 않음
2. **이중 관문** — `ENABLED`와 `DRY_RUN` 둘 다 넘어야 삭제
3. **롤업 검증** — 원본의 스트리머가 롤업에 하나라도 빠지면 그 시간대 삭제 금지
4. **결정적 대표값** — 같은 초에 두 행이 있어도 `id` 최댓값으로 확정
5. **UPSERT** — 재실행해도 중복이 생기지 않음(멱등)
6. **배치 커밋** — 배치마다 COMMIT + sleep, 전체를 한 트랜잭션으로 묶지 않음
7. **삭제 상한** — 1회 `PRUNE_MAX_ROWS`까지만
8. **최종 성적 영구 보존** — 어떤 압축 단계에서도 건드리지 않음

## 롤백

| 상황 | 조치 |
|---|---|
| 삭제를 멈추고 싶다 | `SINGCUP_SNAPSHOT_PRUNE_ENABLED=false` (즉시 반영) |
| 워커 자체를 끄고 싶다 | `SINGCUP_RETENTION_WORKER_ENABLED=false` |
| **이미 삭제된 원본이 필요하다** | **백업에서 복원하는 방법뿐** |

## 아직 실행하지 않은 위험 작업

- **VACUUM** — DELETE 후에도 파일은 줄지 않습니다. `diagnostics`의
  `pragmas.reclaimable_bytes`로 회수 가능량을 먼저 확인하고, 파일 크기만큼
  임시 공간 + 전체 배타적 잠금이 필요하므로 유지보수 시간에 별도 수행합니다.
  순서: 백업 → 봇 정지 → `VACUUM` → `integrity_check?full=true` → 재기동
- **PostgreSQL 이전** — 별도 작업
