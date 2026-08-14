# COMMUNITY-0 — NexBot 커뮤니티 플랫폼 설계서

| 항목 | 값 |
|---|---|
| 작성 | 2026-08-14 |
| 기준 커밋 | `origin/main` = `68482b6` |
| 상태 | **설계만. 구현 0줄.** |
| 제품 결정 A~F | **§26에서 전부 확정됨 (2026-08-14)** — **B-1·B-2 포함.** 남은 것은 스키마에 영향 없는 잔여 2건뿐(§26-B 말미) |
| worktree | `discord-workspace-community0` / `design/community0-plan` |
| 원 요구 | `해야할일 08.06 마무리.txt` |

> 이 문서의 모든 "기존 구조" 서술은 `origin/main` 코드를 실제로 읽고 적었다.
> 추정한 부분은 그 자리에 **[미확인]**으로 표시했다.

---

## §0. 먼저 — 코드 감사에서 나온 3가지 (설계의 전제)

### ① 로컬 `User` 테이블이 **없다.** 새로 만들지 않는다

`web/backend/auth.py` + `deps.py`를 읽은 결과, 이 서비스의 신원은 전부 **Discord OAuth
→ 자체 JWT**다. `create_jwt()`가 `sub`(Discord user id)·`username`·`avatar`·
`global_name`·`access_token`을 담고 7일 만료(`JWT_EXPIRE_HOURS = 24*7`)로 서명한다.
DB에 유저 행이 없다 — **JWT 자체가 유저 레코드**다.

**따라서 커뮤니티도 새 인증 체계나 새 User 테이블을 만들지 않는다.**
필요한 것은 "이 Discord user id의 표시 이름/아바타를 게시글에 어떻게 남길 것인가"뿐이다.

> ⚠️ **작성자 표시 정책 결정 필요(§13-A).** JWT의 아바타 URL을 게시글에 그대로 저장하면
> 사용자가 아바타를 바꿔도 옛 글에 옛 이미지가 남는다. 반대로 매번 Discord를 조회하면
> 목록 하나에 N번 외부 호출이 된다. → **작성 시점 스냅샷 저장**(`author_name`,
> `author_avatar`)을 권장하되, 그게 곧 개인정보 보존이므로 §14에서 다시 다룬다.

### ② "스트리머 인증"은 **이미 있다.** 다시 만들지 않는다

루트 `CLAUDE.md`가 못 박은 두 흐름이 그대로 쓰인다.

| 흐름 | 위치 | 커뮤니티에서의 의미 |
|---|---|---|
| **1. 스트리머 OAuth** | `chzzk_auth_router.py`, `chzzk_subscriptions.streamer_access_token` | "이 치지직 채널의 주인"임을 치지직이 보증 → **게시판 소유권의 근거** |
| **2. 시청자 인증** | `verify_router.py`, `chzzk_verifications` | Discord user ↔ chzzk 채널 매핑 |

**게시판 개설 권한을 흐름 1에 건다.** 그러면 "아무나 남의 이름으로 게시판을 만든다"가
구조적으로 불가능해진다. 별도 인증 배지 시스템을 만들 이유가 없다.

> ⚠️ 흐름 1은 **길드 단위**다(`chzzk_subscriptions`는 `guild_id` 스코프이고, 한 채널은
> 한 길드에만 등록된다). 커뮤니티 게시판은 길드와 무관한 **사이트 전역** 개념이라
> 스코프가 어긋난다. → **§26-B에서 해소됨**: 길드 등록과 독립된 사이트 전역 OAuth를
> 별도로 두고, 길드 등록 사실만으로 전역 소유권을 인정하지 않는다.

### ③ `community_listing` 테이블이 **휴면 상태로 남아 있다**

2026-07-25에 제거된 길드 홍보 디렉터리의 잔재다(`db.py:458`, append-only라 drop 안 함).
**재사용하지 않는다** — 축이 다르다(길드 홍보 ↔ 스트리머 게시판). 다만 루트 `CLAUDE.md`가
"`/community` 라우트와 `api.community`를 되살리지 말 것"이라고 명시했으므로,
새 커뮤니티는 **`/boards` 계열 경로**를 쓴다. 이름 충돌을 피하는 것이 목적이다.

---

## §1. 서비스 기획서

### 문제

네이버 카페는 **대형 스트리머만** 굴러간다. 카페 개설·운영 비용(개설 심사, 메뉴 설계,
등업 정책, 스팸 관리)이 시청자 수십 명 규모의 방송인에게는 과하다. 결과적으로 하꼬
스트리머의 팬은 "모일 곳이 없어서" 채팅창이 꺼지면 흩어진다.

### 해결

**개설 비용을 0에 수렴시킨다.** 치지직 계정을 연결하면 게시판이 즉시 생기고, 기본
카테고리가 이미 채워져 있으며, 방송 데이터(현재 방송 중 여부·최근 통계)가 자동으로
붙는다. 스트리머가 설정할 것은 사실상 없다.

### NexBot 안에 두는 이유 (이게 차별화의 근거다)

이미 있는 것들이 그대로 자산이 된다 — 치지직 실시간 수집(`rising_collector`, 10분 주기),
채널 통계(`/stats`), 스트리머 인증(OAuth), Discord 연동(봇), 그리고 TAG-1의 소속 태그.
**커뮤니티는 새 서비스가 아니라 기존 데이터에 대화를 붙이는 것이다.**

---

## §2. MVP와 후속 범위

> **§26-F에서 3분할로 확정됐다** — COMMUNITY-1 Core / COMMUNITY-2 Recruitment /
> COMMUNITY-3 이후. 아래 표의 "MVP" 열은 **Core + Recruitment를 합친 것**이므로,
> 실제 착수 단위는 §26-F 표를 기준으로 삼는다.

| 범위 | MVP(COMMUNITY-1) | 후속 |
|---|---|---|
| 인증 | 기존 Discord JWT 재사용 | — |
| 스트리머 게시판 | 개설·조회·기본 카테고리 6종 | 커스텀 카테고리, 게시판 스킨 |
| 게시글 | 작성·조회·수정·삭제·공지 고정 | 임시저장, 예약 발행 |
| 댓글 | 1단계(대댓글 없음) | 대댓글, 멘션 |
| 자유게시판 | 사이트 전역 1개 | 태그별 분리 |
| 시참 모집 | 작성·신청·취소·마감 | 자동 추첨, 대기열 |
| 콘텐츠 모집 | 작성·제출·마감 | 제출물 심사 워크플로 |
| 홈 "현재 모집 중" | O | 개인화 추천 |
| 검색 | 제목+본문 LIKE, 게시판명 | FTS5 전문검색 |
| 신고 | 신고 접수 + 운영자 숨김 | 자동 필터, 신고 사유 통계 |
| 알림 | **DB 적재만**(읽기 UI 포함, fan-out 없음) | 실시간·이메일·Discord DM |
| 모바일 | O | — |
| **제외(명시적)** | 파일 업로드 저장소 · 포인트/레벨/배지 · 추천 알고리즘 · WebSocket · 대규모 알림 fan-out · AI · 외부 이미지 프록시 · 익명 제보 영구 저장 | 각각 별도 승인 |

### 이미지·첨부 — MVP에서 하지 않는다

저장 위치·용량 상한·악성 파일 검사·저작권·삭제 정책이 **하나도 확정되지 않았다.**
Railway 컨테이너 파일시스템은 재배포 시 날아가므로 자체 저장소를 만들려면 외부
오브젝트 스토리지가 필요하고, 그건 비용·약관·삭제요청 처리까지 딸려 온다.

**MVP의 이미지 정책:** 본문에 이미지 URL을 쓰면 **링크로만** 렌더한다(자동 임베드 없음).
이렇게 하면 저장소도, 프록시도, 악성 파일 검사도 필요 없다.

---

## §3. 사용자 여정

### A. 시청자 — 홈에서 글쓰기까지 (요구된 핵심 흐름)

```
홈  →  [현재 모집 중] 카드 또는 상단 내비 '스트리머 게시판'
    →  게시판 탐색(/boards): 인기·최근 활동·검색
    →  게시판 입장(/boards/[slug]): 공지 고정 + 카테고리 탭 + 글 목록
    →  글 읽기(/boards/[slug]/posts/[id])
    →  [글쓰기] 클릭
         ├─ 비로그인 → 로그인 유도 모달 → Discord OAuth → **원래 자리로 복귀**
         └─ 로그인   → 작성 폼(카테고리 선택 · 제목 · 본문)
    →  작성 완료 → 글 상세로 이동
```

**핵심 UX 결정:** 로그인 유도는 **글쓰기 버튼을 누른 시점**에만 한다. 게시판 진입
자체를 막으면 검색 유입이 전부 튕긴다(SEO도 죽는다). 읽기는 항상 비회원 허용.

### B. 스트리머 — 게시판 개설

```
/boards → [내 게시판 만들기]
   → 치지직 계정 연결 여부 확인
        ├─ 미연결 → 기존 치지직 OAuth 흐름으로(§0-②)
        └─ 연결됨 → 채널명·슬러그·소개 확인 → 개설
   → 기본 카테고리 6종 자동 생성 → 첫 공지 작성 유도
```

### C. 스트리머 — 시참 모집

```
게시판 → [시참 모집 열기] → 게임 태그 · 최대 인원 · 시작 시각 · 참여 조건 · 디스코드 여부
   → 발행 → 홈 '현재 모집 중'과 /recruits에 동시 노출
   → 신청자 목록 확인 → (MVP는 선착순 자동 확정) → [모집 마감]
```

---

## §4. 페이지별 와이어프레임

공통 레이아웃은 **기존 `/stats`의 2열 구조**를 그대로 따른다
(`grid md:grid-cols-[240px_1fr]`) — 새 사이트처럼 보이지 않게 하는 가장 값싼 방법이다.

### 4.1 `/boards` — 게시판 탐색 메인

```
┌ Header(기존 재사용) ────────────────────────────────────────────┐
├──────────────┬──────────────────────────────────────────────────┤
│ Sidebar 240px│ Main                                             │
│              │ ┌ 검색 바 ────────────────────────────────────┐  │
│ [내 게시판]   │ │ 🔍 스트리머명·치지직 닉네임·카테고리·장르     │  │
│ ─────────    │ └────────────────────────────────────────────┘  │
│ 정렬          │ ┌ 인기 게시판 (가로 스크롤 카드 6) ───────────┐  │
│ · 인기        │ │ [아바타] 채널명 [소속태그] · 글 1.2k · 🔴LIVE│  │
│ · 최근 활동   │ └────────────────────────────────────────────┘  │
│ · 최근 생성   │ ┌ 최근 활동 많은 게시판 (리스트) ─────────────┐  │
│ · 추천        │ │ 행: 아바타 · 이름 · 태그 · 최근글 · 24h 글수 │  │
│ ─────────    │ └────────────────────────────────────────────┘  │
│ 장르 필터     │ ┌ 최근 생성 게시판 ──────────────────────────┐  │
│ (카테고리)    │ └────────────────────────────────────────────┘  │
└──────────────┴──────────────────────────────────────────────────┘
```

- **Right Panel 없음.** 1280px에서 3열은 각 열이 좁아져 한글 제목이 전부 잘린다.
  요구서의 "Right Panel"은 여기서 **Sidebar 하단 블록**으로 흡수한다.
- 게시판 카드에는 **TAG-1의 소속 태그**와 `/stats`가 이미 아는 **LIVE 여부**를 붙인다.

### 4.2 `/boards/[slug]` — 스트리머 게시판

```
┌ 게시판 헤더 ────────────────────────────────────────────────────┐
│ [아바타64] 채널명 [소속태그] [🔴LIVE]        [팔로우] [글쓰기]   │
│ 소개 한 줄 · 구독자 N · 게시글 N · 개설 2026-08-14              │
│ [통계 보기 →]  ← 기존 /stats/streamer/{id}로 연결(신규 개발 0)   │
├──────────────┬──────────────────────────────────────────────────┤
│ 카테고리      │ 📌 공지 (고정, 최대 3)                           │
│ · 전체        │ ───────────────────────────────────────────────  │
│ · 공지        │ 목록 행:                                         │
│ · 자유        │  [카테고리] 제목            작성자 · 시각 · 👁 · 💬│
│ · 팬아트      │  ...                                             │
│ · 클립        │ ───────────────────────────────────────────────  │
│ · 질문        │ [더 보기]  ← cursor 기반. 페이지 번호 없음        │
│ · 방송 피드백 │                                                  │
└──────────────┴──────────────────────────────────────────────────┘
```

### 4.3 `/boards/[slug]/posts/[id]` — 글 상세

제목 → 메타(작성자·시각·조회수) → 본문 → 반응 버튼 → 댓글 목록 → 댓글 입력.
우측 열 없음(읽기 집중). 하단에 같은 게시판의 이전/다음 글.

### 4.4 `/recruits` — 시참 모집

```
필터: [모집중 ▾] [게임 태그 ▾] [디스코드 필요 ▾]
카드 그리드(모바일 1열 / 태블릿 2열 / 데스크톱 3열):
┌───────────────────────────┐
│ [모집중]  롤          D-0 │  ← 상태를 색 + **글자**로 표시
│ ○○ 스트리머 [소속태그]     │
│ 롤 듀오 모집               │
│ ▓▓▓▓▓░░░░░  2 / 5명        │
│ 21:00 시작 · 디스코드 필요  │
│              [참가 신청]   │
└───────────────────────────┘
```

### 4.5 `/contents` — 콘텐츠 모집

같은 카드 그리드. 인원 대신 **D-day**와 마감일, 제출 링크 버튼, 익명 제출 배지.

### 4.6 `/free` — 사이트 자유게시판

`/boards/[slug]`와 **같은 컴포넌트**를 쓰고 헤더만 바꾼다(일상·질문·정보 공유·잡담).

### 4.7 홈 `/` — "현재 모집 중" 섹션 위치

현재 홈은 **Hero → (그 아래 섹션들)** 구조다(`app/page.tsx`). UI-1에서 Hero의 격자를
걷어낸 그 영역 바로 아래에 억지로 끼우지 않는다.

**결정: Hero 다음의 첫 콘텐츠 섹션 자리**에 둔다. 이유 — 이 섹션이 커뮤니티로 들어가는
유일한 홈 진입점이고, 스크롤 하단이면 존재 자체를 모른다.

**데이터가 없을 때는 섹션을 통째로 렌더하지 않는다.** 빈 카드 자리를 크게 남기면
"서비스가 죽었나"로 읽힌다. 정확히는 `recruits.length + contents.length === 0`이면
`return null`.

---

## §5. 컴포넌트 구조

```
web/frontend/
├─ app/
│  ├─ boards/
│  │  ├─ page.tsx                    탐색 메인
│  │  ├─ [slug]/page.tsx             게시판
│  │  ├─ [slug]/posts/[id]/page.tsx  글 상세
│  │  ├─ [slug]/write/page.tsx       작성/수정
│  │  └─ new/page.tsx                게시판 개설
│  ├─ free/page.tsx
│  ├─ recruits/{page.tsx,[id]/page.tsx,new/page.tsx}
│  └─ contents/{page.tsx,[id]/page.tsx,new/page.tsx}
└─ components/community/
   ├─ BoardCard.tsx        BoardHeader.tsx    CategoryTabs.tsx
   ├─ PostList.tsx         PostRow.tsx        PostBody.tsx
   ├─ CommentList.tsx      CommentForm.tsx
   ├─ RecruitCard.tsx      RecruitProgress.tsx
   ├─ ContentCard.tsx      DDayBadge.tsx
   ├─ ReportButton.tsx     ModerationBar.tsx
   ├─ LoginGate.tsx        ← 비로그인 시 '돌아올 자리'를 기억하는 유일한 지점
   └─ states/{Loading,Empty,Error,Forbidden,Deleted}.tsx
```

**재사용 원칙:** `components/StreamerTag.tsx`(TAG-1) · `components/Switch.tsx` ·
`app/stats/singcupShared.tsx`의 배지 관례를 그대로 쓴다. 새 pill/toggle을 만들지 않는다.

**상태관리:** 전역 스토어를 **도입하지 않는다.** 현재 저장소는 `useState` + `lib/api.ts`의
`request()`로 충분히 돌아가고 있고, 커뮤니티도 페이지 단위 로컬 상태로 끝난다.
목록 캐시가 필요해지면 기존 `sharedGet`(모듈 캐시 + in-flight 합류)을 재사용한다.

---

## §6. Route 구조

| 경로 | 렌더 | 인증 | SEO |
|---|---|---|---|
| `/boards` | SSR | 불필요 | index |
| `/boards/[slug]` | SSR | 불필요 | index |
| `/boards/[slug]/posts/[id]` | SSR | 불필요 | index |
| `/boards/[slug]/write` | CSR | **필요** | noindex |
| `/boards/new` | CSR | **필요(스트리머)** | noindex |
| `/free` | SSR | 불필요 | index |
| `/recruits`, `/recruits/[id]` | SSR | 불필요 | index |
| `/recruits/new` | CSR | **필요(스트리머)** | noindex |
| `/contents`, `/contents/[id]` | SSR | 불필요 | index |
| `/notices` | SSR | 불필요 | index |

> `app/robots.ts`에 `/boards/*/write`, `/boards/new`, `/recruits/new`, `/contents/new`를
> **Disallow에 추가해야 한다.** 로그인 게이트 뒤라 크롤러에게는 빈 껍데기이고,
> 그게 정확히 AdSense가 지난번에 지적한 "콘텐츠 없는 화면"이다.

### 내비게이션

기존 헤더는 이미 `NexBot / 치지직 통계 / 로그인`으로 차 있다. 여기에 6개를 더 넣으면
1280px에서 줄바꿈한다.

**결정:** 헤더에는 **`커뮤니티` 하나**만 추가하고, 그 아래 2차 내비(게시판 탐색 · 자유게시판 ·
시참 모집 · 콘텐츠 모집 · 공지)를 커뮤니티 섹션 공통 서브바로 둔다.
모바일(<md)에서는 서브바를 **가로 스크롤**로 흘린다(줄바꿈 금지 — `nexadmin` 탭이
390px에서 "서/버/목/록"으로 쪼개진 실측 사례가 있다).

---

## §7. API 계약

전부 `/api/community/*`. 기존 `request()`(Authorization 헤더)를 그대로 쓴다.

| 메서드 | 경로 | 권한 | 비고 |
|---|---|---|---|
| GET | `/boards?sort=popular\|recent\|active&cursor=&q=` | 공개 | cursor 페이지네이션 |
| POST | `/boards` | 스트리머 | 치지직 소유권 검증 |
| GET | `/boards/{slug}` | 공개 | 게시판 메타 + 카테고리 |
| PATCH | `/boards/{slug}` | 게시판 관리자 | 소개·카테고리 |
| GET | `/boards/{slug}/posts?category=&cursor=` | 공개 | 공지 고정 분리 반환 |
| POST | `/boards/{slug}/posts` | 로그인 | rate limit |
| GET | `/posts/{id}` | 공개 | 조회수 +1(§17) |
| PATCH/DELETE | `/posts/{id}` | 작성자·게시판 관리자·OWNER | soft delete |
| POST | `/posts/{id}/pin` | 게시판 관리자 | 최대 3 |
| GET/POST | `/posts/{id}/comments` | 공개 / 로그인 | |
| DELETE | `/comments/{id}` | 작성자·관리자 | soft delete |
| POST | `/posts/{id}/reactions` | 로그인 | 멱등(토글) |
| GET | `/recruits?status=&game=&cursor=` | 공개 | |
| POST | `/recruits` | 스트리머 | |
| POST | `/recruits/{id}/join` \| `/leave` | 로그인 | 정원 초과 409 |
| POST | `/recruits/{id}/close` | 작성자 | |
| GET/POST | `/contents`, `/contents/{id}/submit` | 공개 / 로그인 | |
| GET | `/home/active` | 공개 | 홈 '현재 모집 중' 전용 **경량** 응답 |
| GET | `/search?q=&type=&cursor=` | 공개 | q 최소 2자 + rate limit |
| POST | `/reports` | 로그인 | 대상 타입+id |
| GET | `/moderation/reports` | OWNER | |
| POST | `/moderation/hide` | OWNER | |
| GET | `/notifications?cursor=` | 로그인 | 본인 것만 |

**`/home/active`를 따로 두는 이유:** 홈은 전 방문자가 여는 페이지다. 목록 API를 그대로
쓰면 홈 1회 로드가 모집글 전체 필드를 끌고 온다 — 싱드컵 `/main`에서 정확히 그 실수로
월 $29.98 전송비가 예측된 전례가 있다. 홈 전용 응답은 **카드에 그리는 필드만** 담고
ETag + `s-maxage`를 붙인다.

---

## §8. SQLite ERD

```
(Discord JWT: sub = author_id)          ← User 테이블 없음. 만들지 않는다
        │
        │ author_id (TEXT, Discord snowflake)
        ▼
  community_boards ─┬─< community_categories ─┬─< community_posts ─┬─< community_comments
   id, slug,        │   id, board_id, name,   │   id, board_id,    │   id, post_id,
   owner_user_id,   │   sort_order, active    │   category_id,     │   author_id, body,
   chzzk_channel_id │                         │   author_id,       │   status, created_at
   title, intro,    ├─< community_board_roles │   title, body,     │
   status,          │   board_id, user_id,    │   is_pinned,       └─< community_reactions
   post_count,      │   role                  │   view_count,          target_type,
   last_post_at     │                         │   comment_count,       target_id,
                    │                         │   status,              user_id, kind
                    │                         │   created_at,
                    │                         │   updated_at
  community_recruits ──< community_participations
   id, board_id, author_id, game_tag,      recruit_id, user_id,
   max_people, current_people,             status, created_at
   start_at, condition, needs_discord,
   status, created_at

  community_contents ──< community_submissions
   id, board_id, author_id, kind,           content_id, user_id(nullable),
   deadline_at, submit_url,                 payload, is_anonymous, created_at
   allow_anonymous, status

  community_reports ──> community_moderation_actions
   id, target_type, target_id,              id, target_type, target_id,
   reporter_id, reason, status              actor_id, action, reason, created_at

  community_notifications
   id, user_id, kind, target_type, target_id, read_at, created_at
```

### 설계 규칙 (이 저장소의 관례를 따른다)

1. **FK 제약을 걸지 않는다.** 기존 테이블이 전부 그렇고(`rising_*`, `singcup_*`,
   TAG-1 포함), append-only 마이그레이션에서 FK는 실패 지점만 늘린다.
2. **삭제는 전부 soft delete** — `status ∈ (visible, hidden, deleted)`.
   `deleted`는 작성자 삭제, `hidden`은 운영자 조치. 두 개를 하나로 합치면
   "내가 지운 글"과 "잘린 글"을 사용자가 구분하지 못한다.
3. **`Attachment` 테이블은 MVP에 만들지 않는다**(§2). ERD 후보에는 있었지만,
   저장 정책이 없는 상태에서 스키마만 만들면 나중에 반드시 다시 바꾼다.
4. **카운터(`post_count`, `comment_count`, `current_people`)는 비정규화한다.**
   목록마다 `COUNT(*)`를 돌리면 그게 N+1이다. 갱신은 같은 짧은 트랜잭션 안에서.
5. 인덱스는 **화면이 읽는 모양 그대로**:
   `(board_id, status, is_pinned DESC, created_at DESC)`,
   `(post_id, status, created_at)`, `(status, start_at)`, `(user_id, read_at)`.

---

## §9. 권한표

| 기능 | 비회원 | 일반 | 스트리머 | 게시판 관리자 | OWNER |
|---|:--:|:--:|:--:|:--:|:--:|
| 게시판·글·댓글 조회 | ✅ | ✅ | ✅ | ✅ | ✅ |
| 게시판 개설 | ❌ | ❌ | ✅(본인 채널) | — | ✅ |
| 게시판 설정·카테고리 관리 | ❌ | ❌ | ✅(본인) | ✅ | ✅ |
| 글 작성 | ❌ | ✅ | ✅ | ✅ | ✅ |
| 글 수정 | ❌ | 본인만 | 본인만 | 본인만 | ✅ |
| 글 삭제 | ❌ | 본인만 | 본인만 | ✅(게시판 내) | ✅ |
| 공지 고정 | ❌ | ❌ | ✅(본인) | ✅ | ✅ |
| 댓글 작성 | ❌ | ✅ | ✅ | ✅ | ✅ |
| 댓글 삭제 | ❌ | 본인만 | 본인만 | ✅ | ✅ |
| 시참·콘텐츠 모집 등록 | ❌ | ❌ | ✅ | ✅ | ✅ |
| 참가 신청·취소 | ❌ | ✅ | ✅ | ✅ | ✅ |
| 참가 승인·마감 | ❌ | ❌ | ✅(본인 글) | ✅ | ✅ |
| 신고 | ❌ | ✅ | ✅ | ✅ | ✅ |
| 신고 처리·숨김·복구 | ❌ | ❌ | ❌ | ❌ | ✅ |

- **미인증 401, 권한 없음 403** — 기존 `deps.get_current_user`(401)와
  `_require_owner`(403)의 계약을 그대로 쓴다.
- "게시판 관리자"는 `community_board_roles`로 스트리머가 위임한다(MVP는 위임 UI 없이
  스키마만 두고 소유자 = 관리자).

---

## §10. 검색·정렬·추천

### 검색 (MVP)

- 대상: 게시판명·채널명 / 글 제목·본문 / 게임 태그
- 방식: `LIKE ? ESCAPE '\'`로 **와일드카드 이스케이프**(TAG-1 `_like_escape`와 동일 패턴)
- **최소 2자** + **IP·유저 단위 rate limit**(기존 `rate_limit.py` 재사용)
- **무제한 SELECT 금지** — 항상 `LIMIT` + cursor
- 후속: SQLite **FTS5** 가상 테이블. MVP에서 넣지 않는 이유는 인덱스 동기화가
  트리거를 요구하고, 그게 쓰기 잠금 시간을 늘리기 때문(§17)

### 정렬

| 키 | 정의 |
|---|---|
| 최근 | `created_at DESC` |
| 인기 | `(view_count * 1 + comment_count * 5 + reaction_count * 3)` 내림차순, 최근 7일 |
| 최근 활동 | `last_post_at DESC` |
| 추천 | 아래 |

### 추천 (MVP는 **알고리즘이 아니다**)

정직하게 규칙 기반으로 간다.
`점수 = 24시간 글 수 × 2 + 24시간 댓글 수 × 3 + (현재 방송 중이면 +10)`
— "현재 방송 중"은 `rising_live_snapshots` 최신 사이클에서 이미 알 수 있는 값이다(추가 수집 0).

**협업 필터링·임베딩은 MVP 제외.** 데이터가 없는 상태의 추천은 무작위와 구별되지 않는다.

---

## §11. 알림 구조

**MVP는 DB 적재 + 인앱 읽기까지만.** 실시간 push·Discord DM·이메일 전부 제외.

| 이벤트 | 대상 |
|---|---|
| 내 글에 댓글 | 글 작성자 |
| 내 시참 모집에 신청 | 모집 작성자 |
| 내 신청이 마감/취소됨 | 신청자 |
| 내 글이 운영자에 의해 숨겨짐 | 작성자 |

**fan-out을 하지 않는다.** "게시판 구독자 전원에게 새 글 알림"은 구독자 1만 명이면
쓰기 1만 행이고, 그게 SQLite 쓰기 잠금을 길게 잡는다(§17). 후속으로 미룬다.

---

## §12. 신고·숨김·삭제·복구

| 상태 | 누가 | 보이는가 | 되돌리기 |
|---|---|---|---|
| `visible` | — | 전원 | — |
| `hidden` | OWNER | 작성자에게만 "숨겨짐" 안내 | ✅ `visible`로 |
| `deleted` | 작성자 | 아무도 | ✅ 30일 내 OWNER 복구 |

- **하드 삭제는 즉시 하지 않는다.** 30일 뒤 배치로 지운다 — 오삭제 복구와 신고 조사에
  둘 다 필요하다.
- 신고는 **같은 사용자가 같은 대상에 1회**(`UNIQUE(target_type, target_id, reporter_id)`).
- 임계값 자동 숨김을 **MVP에서 넣지 않는다.** 조직적 신고로 정상 글이 사라진다.

---

## §13. 보안

| 위협 | 대책 |
|---|---|
| **XSS** | 본문은 **Markdown 부분집합만** 허용(굵게·기울임·링크·인용·코드·목록). **원시 HTML 금지.** 서버에서 파싱해 화이트리스트 노드만 남기고, 프론트는 React 텍스트 렌더 기본값을 쓴다. `dangerouslySetInnerHTML`을 **절대 쓰지 않는다** |
| 링크 | `http(s)`만. `javascript:`·`data:` 거부. 외부 링크는 `rel="noopener noreferrer nofollow"` |
| **CSRF** | 인증이 **Authorization 헤더**(쿠키 아님)라 구조적으로 해당 없음 — 루트 `CLAUDE.md`가 CORS `*`를 허용하는 근거와 같다. **쿠키 인증으로 바꾸지 말 것** |
| **IDOR** | 모든 수정·삭제에서 `author_id == jwt.sub` 또는 역할 검증. **경로의 id를 신뢰하지 않는다** |
| **SQL 주입** | 파라미터 바인딩 전용. 문자열 포맷은 `IN (?,?,…)`의 물음표 생성에만 |
| 스팸·도배 | 글 작성 분당 상한, 동일 본문 연속 차단, 계정 생성일 기준 신규 계정 제한. 기존 `rate_limit.py` 재사용 |
| **파일 업로드** | **없다**(§2). 공격면 자체를 만들지 않는다 |
| 열거 | slug는 채널명 기반 + 충돌 시 suffix. 순번 id를 slug로 쓰지 않는다 |
| 로그 | 기존 `log_redaction` 관례에 맞춰 토큰·이메일을 남기지 않는다 |

---

## §14. 개인정보·보존

| 데이터 | 보존 | 근거 |
|---|---|---|
| `author_id`(Discord id) | 글과 동일 | 작성자 식별에 필수 |
| `author_name`/`author_avatar` 스냅샷 | 글과 동일 | ⚠️ **§13-A 결정 필요** — 편의 vs 최소 수집 |
| 삭제된 글 | 30일 뒤 하드 삭제 | §12 |
| 신고 이력 | 1년 | 반복 신고 판단 |
| 알림 | 90일 | 무기한 누적 방지 |
| 익명 제출물 | **MVP 미구현** | 정책 미확정(요구서 명시) |

> ⚠️ **V2 `hourly_visitors`에서 prune 미구현으로 무기한 누적된 전례가 있다**
> (인계 문서 §3). 커뮤니티는 **보존 정책과 prune 코드를 같은 커밋에** 넣는다.
> "나중에"는 실제로 오지 않았다.

---

## §15. SEO·메타데이터

- `/boards`, `/boards/[slug]`, 글 상세는 **SSR + `generateMetadata`**.
  기존 `rising_router`의 `/streamer/{id}/meta` 패턴(경량 응답 + ETag)을 그대로 복제한다 —
  **전체 대시보드를 SSR에서 부르지 않는다**(그 실수로 크롤러가 429를 맞고
  `robots: index=false` 폴백이 붙은 실측 사례가 있다).
- 글 상세: `title`, `description`(본문 앞 120자, 마크다운 제거), `og:image`는
  스트리머 채널 이미지, `article:published_time`.
- `sitemap.xml`에 게시판 목록 추가(기존 `streamers-sitemap.xml`과 같은 방식).
- 작성/개설 폼은 `robots.ts` Disallow(§6).

---

## §16. 캐시·ETag·페이지네이션

- **cursor 페이지네이션만.** `OFFSET`은 뒤로 갈수록 느려지고, 새 글이 들어오면
  같은 글이 두 번 보인다. 커서는 `(created_at, id)` 복합.
- 목록 응답: `ETag`(본문 해시) + `Cache-Control: public, max-age=30, s-maxage=60`.
  **ETag는 본문 전체 해시로 만든다** — 일부 필드로 만들면 내용이 바뀌어도 304가 나간다
  (`_meta_etag` 주석이 같은 교훈을 적고 있다).
- 홈 `/home/active`: `s-maxage=60`, 모집 상태 변경 시 세대 카운터로 무효화
  (TAG-1의 `streamer_tags.version()`과 같은 패턴).
- **`sharedGet` 재사용** — 같은 목록을 여러 컴포넌트가 부를 때 in-flight 합류.

---

## §17. 성능과 SQLite 쓰기 잠금 — **가장 큰 리스크**

이 저장소는 봇과 백엔드가 **같은 `bot.db` 파일**을 공유한다(WAL). 이미
`db_locked_giveup` 경합이 관측되고 있다. 커뮤니티는 **처음으로 사용자 트래픽이 직접
쓰기를 유발하는** 기능이라, 아래를 설계 제약으로 못 박는다.

| 규칙 | 이유 |
|---|---|
| **조회수 +1을 매 요청 UPDATE 하지 않는다** | 인기 글 하나가 초당 수십 번 쓰기 잠금을 잡는다. → **메모리 버퍼 후 N초마다 일괄 반영** |
| 트랜잭션 안에서 **외부 HTTP·sleep 금지** | 이미 스윕에서 25~30초 잠금으로 봇이 죽은 실측이 있다(`singcup_sweep.py` 주석) |
| 쓰기는 **단문 + 즉시 commit** | 장기 트랜잭션 금지 |
| 카운터 갱신은 **본 쓰기와 같은 트랜잭션** | 별도 트랜잭션이면 잠금 획득이 2회 |
| 목록 쿼리는 **인덱스만으로 끝나야** | `EXPLAIN QUERY PLAN`에 `SCAN` 없으면 통과 |
| N+1 금지 | 작성자·태그·카운트는 **한 번에** 모아 붙인다(TAG-1 `tags_for_channels` 패턴) |
| 알림 fan-out 금지 | §11 |

> **배포 창 제약도 그대로다.** `web/backend/**`·`database/**`는 `watchPatterns` 매칭이라
> Railway 재배포를 유발한다. 커뮤니티 백엔드 배포는 기존 게이트(직전 회차 완주 +
> 새 회차 ≤5%)를 따른다.

---

## §18. 운영자 moderation UX

**`/nexadmin`에 `커뮤니티` 탭을 추가한다** — 별도 관리 사이트를 만들지 않는다.
TAG-1이 `스트리머 태그` 탭을 붙인 것과 같은 자리·같은 방식(`_require_owner`).

- 신고 큐: 대상 미리보기 · 신고 사유 · 신고 수 · [숨김] [기각] [복구]
- 숨김 시 **사유 필수** — 작성자에게 그 사유가 보인다
- 조치 이력(`community_moderation_actions`)은 지우지 않는다

---

## §19. 모바일 UX

- **모바일 우선.** 390×844에서 먼저 그린다
- **가로 스크롤 0** — 서브바만 의도적으로 가로 스크롤(§6)
- 목록 행은 2줄 허용(제목 1줄 + 메타 1줄), 제목은 `truncate`
- 글쓰기 버튼은 게시판 화면에서 **하단 고정**(FAB)
- 하단 고정 요소는 기존 대시보드 모바일 바(`h-14`)와 겹치지 않게

---

## §20. 접근성

- 모든 대화형 요소에 `focus-visible` 링(기존 토큰 재사용)
- **상태를 색만으로 말하지 않는다** — [모집중]/[마감]은 글자 배지
- 목록은 `<ul>/<li>`, 제목 계층은 `h1 → h2 → h3` 건너뛰지 않기
- 폼 입력마다 `<label>` 연결 — `components/Switch.tsx`는 **`<label>`로 감싸야 동작한다**
  (루트 `CLAUDE.md` 명시)
- 로딩은 `aria-busy`, 오류는 `role="alert"`
- `prefers-reduced-motion` 준수, **skeleton 남용 금지**(3개 이상 겹치면 텍스트 안내로)

---

## §21. 테스트 계획

**백엔드(pytest)**
- 마이그레이션 재실행 안전성
- 권한 매트릭스 전수: 5역할 × 주요 동작 → 401/403/200
- IDOR: 남의 글 수정·삭제 시도
- Markdown 파서: 원시 HTML·`javascript:`·`data:` 차단
- cursor 페이지네이션: 중복·누락 없음, 새 글 삽입 중에도
- 정원 초과 참가 신청 → 409, 동시 신청 경쟁
- soft delete 후 목록·검색·SEO에서 사라지는지
- 조회수 버퍼: N초 후 정확히 반영
- rate limit 발동
- ETag: 내용 변경 시 반드시 변한다
- **`EXPLAIN QUERY PLAN`에 전체 스캔이 없는지**

**프론트**
- `tsc --noEmit` 0 · ESLint 신규 0 · production build 성공
- 브라우저 QA: 390 / 1280 / 1920, 다크·라이트, Console 오류 0, 가로 스크롤 0
- 로그인 게이트 후 원래 자리 복귀
- 긴 스트리머명·긴 제목·태그 많은 행에서 레이아웃 유지

---

## §22. 배포·rollback

- **DB 마이그레이션은 append-only**라 앞으로만 간다. 롤백은 **코드만** 되돌린다
  (테이블은 dormant로 남긴다 — `community_listing`의 선례).
- 단계별 배포: ① 스키마+읽기 API → ② 쓰기 API → ③ 프론트.
  각 단계가 독립적으로 롤백 가능해야 한다.
- **기능 플래그 `COMMUNITY_ENABLED`(기본 false)** 로 감싼다. 문제가 생기면
  재배포 없이 끌 수 있다 — 재배포는 스윕 회차를 끊는다.
- 자동 롤백 금지(상시 규칙). 제안만.

---

## §23. 예상 변경 파일

| 파일 | 성격 | 재배포 |
|---|---|---|
| `database/db.py` | 테이블 12 + 인덱스, append-only | **Railway** |
| `web/backend/community/*.py` (신규 6~8) | 도메인 로직 | **Railway** |
| `web/backend/routers/community_router.py` (신규) | API | **Railway** |
| `web/backend/routers/admin_router.py` | moderation 엔드포인트 append | **Railway** |
| `web/backend/main.py` | 라우터 등록 | **Railway** |
| `web/frontend/app/{boards,free,recruits,contents}/**` (신규) | 페이지 | Vercel |
| `web/frontend/components/community/**` (신규) | 컴포넌트 | Vercel |
| `web/frontend/lib/{api,types}.ts` | 클라이언트·타입 | Vercel |
| `web/frontend/app/page.tsx` | 홈 '현재 모집 중' | Vercel |
| `web/frontend/app/robots.ts` | 작성 폼 Disallow | Vercel |
| `web/frontend/app/sitemap.ts` | 게시판 추가 | Vercel |
| `tests/test_community_*.py` (신규 4~6) | 테스트 | 없음 |

---

## §24. 단계별 일정 (구현 승인 시)

| 단계 | 내용 | 규모 |
|---|---|---|
| C1 | 스키마 + 도메인 모듈 + 권한 골격 + 테스트 | 중 |
| C2 | 게시판 개설·조회 + 카테고리 + 글 CRUD + 공지 고정 | 대 |
| C3 | 댓글 + 반응 + 자유게시판 | 중 |
| C4 | 시참 모집 + 참가 | 중 |
| C5 | 콘텐츠 모집 + 제출 | 소 |
| C6 | 홈 '현재 모집 중' + 내비 + 검색 | 중 |
| C7 | 신고 + 운영자 moderation + 보존 prune | 중 |
| C8 | SEO·sitemap·robots·접근성·모바일 QA | 소 |

**각 단계는 독립 worktree·독립 커밋.** C1~C8을 한 커밋에 담지 않는다.

---

## §25. 차별화 요소 (10개 이상)

1. **개설 비용 0** — 치지직 계정 연결만으로 게시판이 생기고 기본 카테고리가 채워진다.
   네이버 카페의 개설·등업·메뉴 설계 과정이 통째로 없다.
2. **하꼬 우선 노출** — 추천 점수에 절대 규모가 아니라 *24시간 활동량*과 *현재 방송 중*을
   쓴다. 대형 채널이 목록을 영구 점유하지 않는다.
3. **방송 데이터가 이미 붙어 있다** — 게시판에 `/stats`의 시청자·팔로워·첫 방송일이
   그대로 연결된다. 커뮤니티와 통계가 한 서비스다. 이건 카페가 못 한다.
4. **"지금 방송 중" 신호** — 10분 주기 수집으로 게시판 카드에 LIVE가 붙는다.
   팬이 들어올 이유가 상시 생긴다.
5. **시참 모집이 1급 기능** — 치지직 문화의 핵심인데 카페에서는 자유게시판 글 하나다.
   인원·시작 시각·조건·디스코드 여부가 구조화돼 있어 필터링된다.
6. **콘텐츠 모집(사연·밸런스게임·제보)** — 스트리머의 실제 반복 업무를 폼으로 만든다.
7. **소속 태그**(TAG-1) — 이세돌·스텔라이브 같은 그룹이 시각적으로 묶인다.
   그룹 팬덤이 개별 게시판을 넘나든다.
8. **홈 '현재 모집 중'** — 커뮤니티가 홈의 살아 있는 콘텐츠가 된다. 카페는 방문해야만
   안에 뭐가 있는지 안다.
9. **Discord 로그인** — 스트리머 시청자층이 이미 쓰는 계정. 별도 가입 절차가 없다.
10. **검색 유입 설계** — SSR + 구조화된 메타데이터. 카페 글은 검색에서 잘 안 잡힌다.
11. **광고·등업·눈팅제한이 없다** — 읽기는 언제나 비회원 허용.
12. **모바일 우선** — 방송 시청은 대부분 모바일 동시 사용이다.
13. **운영 부담을 서비스가 진다** — 스팸 제한·신고 큐가 기본 제공. 하꼬 스트리머가
    관리자를 뽑을 필요가 없다.
14. **하나의 신원으로 Discord 봇까지 이어진다** — 커뮤니티 → 서버 참여 → 채팅 포인트가
    같은 계정으로 연결된다.

---

## §26. 제품 결정 — ✅ **확정 (2026-08-14)**

A~F가 모두 확정됐다. 아래는 결정 사항과, 그 결정이 실제로 성립하려면 무엇이 더
정해져야 하는지다. **B만 아직 미해결 항목을 남긴다(§26-B).**

| # | 항목 | 확정 |
|---|---|---|
| **A** | 작성자 표시 | ① **작성 시점 Discord 이름·아바타 스냅샷 저장** |
| **B** | 게시판 소유권 | ① **Discord 길드 등록 여부와 무관하게 치지직 채널 소유권만 확인** |
| **C** | 본문 형식 | ① **제한된 Markdown 부분집합** |
| **D** | 읽기 | ① **비회원 전면 허용** |
| **E** | 개설 자격 | ① **치지직 인증 스트리머만** |
| **F** | MVP 범위 | **COMMUNITY-1 Core / -2 Recruitment / -3 이후**로 3분할 |

---

### §26-A. 작성자 표시 — 스냅샷 저장 + 개인정보 최소화 계약

`community_posts` / `community_comments`에 작성 시점 값을 박아 둔다.

| 컬럼 | 값 |
|---|---|
| `author_id` | **기존 JWT의 `sub`(Discord user id)** — 새 User 테이블을 만들지 않는다 |
| `author_name` | 작성 시점 표시 이름 스냅샷 |
| `author_avatar` | 작성 시점 아바타 **URL 문자열** |

**지켜야 할 계약 (넘으면 안 되는 선)**

1. **이메일·IP·OAuth 토큰을 저장하지 않는다.** 게시글 테이블에도, 로그에도.
   기존 `log_redaction` 관례를 그대로 따른다
2. **아바타를 프록시하거나 영구 복사하지 않는다.** Discord CDN URL을 문자열로만
   들고 있는다 — 이미지를 우리 저장소로 가져오면 그 순간 삭제 요청 처리 의무가 생긴다
   (그래서 MVP에서 파일 업로드를 뺀 것과 같은 이유다)
3. **익명화가 가능해야 한다.** 계정 삭제 요청이나 작성자 요청 시
   `author_name`/`author_avatar`를 비우고 "(탈퇴한 사용자)"로 표시한다.
   `author_id`는 신고·중복 판정에 필요하므로 남기되, 필요하면 해시로 대체한다.
   **이 익명화 경로를 COMMUNITY-1에 함께 넣는다** — "나중에"는 실제로 오지 않는다
   (V2 `hourly_visitors` prune 미구현 전례, §14)
4. 스냅샷이므로 사용자가 Discord에서 이름을 바꿔도 **옛 글은 옛 이름으로 남는다.**
   이는 의도된 동작이고, 화면에 설명하지 않는다(설명하면 오히려 혼란스럽다)

---

### §26-B. 게시판 소유권 — ✅ **B-1·B-2 확정 (2026-08-14)**

**결론: 사이트 전역 치지직 OAuth 1회로 채널 소유권을 증명하고, `owner_verified_at`을
저장해 90일마다 재검증한다. Discord 길드 등록과 완전히 독립이다.**

| # | 확정 |
|---|---|
| **B-1** | 사이트 전역 치지직 OAuth **1회**로 채널 소유권 증명. 길드 등록 여부와 **독립** |
| **B-2** | `owner_verified_at` 저장 · 유효기간 **90일** · 만료돼도 **공개 읽기 유지**, 관리 작업에만 재인증 요구 |
| **B-3** | `community_boards`에 **OAuth access/refresh token을 저장하지 않는다** |
| **B-4** | **길드 탈퇴가 게시판 소유권을 자동 제거하지 않는다** |
| **B-5** | 연결 해제·소유권 상실 시 **즉시 삭제하지 않고 `orphaned`** 전환 |

---

#### B-1. 사이트 전역 소유권 증명

**기존 길드 등록 사실만으로 사이트 전역 소유권을 영구 인정하지 않는다.**
`chzzk_subscriptions`의 행은 "이 길드에서 이 채널을 등록했다"는 사실일 뿐,
"이 사람이 사이트 전역에서 이 채널의 주인이다"와 같지 않다. 길드 관리자가 남의
채널을 등록해 둔 경우까지 소유권으로 승격되면 그게 곧 계정 탈취 경로가 된다.

**새 인증 체계를 만들지 않는다.** 기존 치지직 OAuth **공급자와 검증 로직을 그대로
재사용**하고, 콜백 경로와 스코프만 사이트 전역용으로 하나 더 둔다.

| 항목 | 값 |
|---|---|
| 콜백 경로 | **`/api/chzzk/oauth/board-owner/callback`** — 기존 길드용 콜백(`chzzk_auth_router.py`)과 **경로를 분리**한다. 같은 콜백에 분기를 얹으면 "어느 흐름으로 들어온 토큰인가"가 상태에만 남아 취약해진다 |
| CSRF 방어 | **기존 `auth.build_oauth_url()` / `verify_oauth_state()`의 서명된 state JWT를 재사용**한다(10분 만료 + nonce). 새 방식을 만들지 않는다. PKCE는 치지직이 지원하면 추가하되, **state JWT가 최소 요구선**이다 |
| state에 담을 것 | `nonce` · `exp` · `purpose:"board-owner"` · **복귀 경로(같은 출처 상대 경로만)** |
| 소유권 판정 | 콜백에서 받은 토큰으로 치지직에 **본인 채널을 조회**해 `chzzk_channel_id`를 얻는다. 사용자가 제출한 채널 id를 믿지 않는다 |
| 연결 키 | **Discord JWT의 `sub` ↔ `chzzk_channel_id`** |

**Discord `sub` ↔ 치지직 `channel_id` 연결 방식**

`community_board_owners`(가칭)에 매핑을 둔다. `community_boards`에 직접 넣지 않는
이유는, 한 사람이 여러 채널을 가질 수 있고 소유권이 이전될 수 있어서다.

```
community_board_owners
  discord_user_id   TEXT NOT NULL     -- JWT sub
  chzzk_channel_id  TEXT NOT NULL
  verified_at       INTEGER NOT NULL  -- = owner_verified_at
  revoked_at        INTEGER NOT NULL DEFAULT 0
  PRIMARY KEY (discord_user_id, chzzk_channel_id)
```

**동일 채널 중복 소유권 방지** — `revoked_at = 0`인 행에 **부분 유니크 인덱스**를 건다.

```sql
CREATE UNIQUE INDEX idx_board_owner_one_active
  ON community_board_owners(chzzk_channel_id) WHERE revoked_at = 0;
```

`singcup_kr_poller_lease`의 `idx_krp_lease_one_open`과 같은 방식이다 — 애플리케이션
검사만 믿으면 동시 요청 두 개가 통과한다. **한 채널에 활성 소유자는 언제나 한 명.**

**소유권 이전·분쟁**

- 치지직 채널의 실제 주인이 바뀌는 경우는 우리가 알 수 없다. 새 사람이 OAuth를
  통과하면 그 사람이 진짜 주인이므로, **기존 행을 `revoked_at`으로 닫고 새 행을 연다.**
  같은 트랜잭션 안에서 처리해야 부분 유니크 인덱스와 충돌하지 않는다
- 게시판(`community_boards`)은 **채널에 붙어 있지 소유자에 붙어 있지 않다.**
  소유자가 바뀌어도 게시판과 글은 그대로 남고 관리 권한만 옮겨 간다
- 분쟁(양쪽이 서로 주인이라 주장)은 **OWNER가 처리**한다(§26-E). 자동 판정하지 않는다

**OAuth 실패·취소 후 복귀** — state JWT에 담아 둔 복귀 경로로 되돌린다.
**같은 출처의 상대 경로만** 허용한다(오픈 리다이렉트 방지). 값이 없거나 형식이
이상하면 `/boards`로 보낸다. 실패 사유는 쿼리 파라미터로 넘겨 화면이 배너로 설명한다
(`chzzk/page.tsx`가 `?error=`를 배너로 보여 주는 기존 방식과 같다).

---

#### B-2. 증명 시각과 재검증

| 항목 | 값 |
|---|---|
| 저장 | `owner_verified_at` (epoch) |
| 유효기간 | **90일** |
| 만료 시 공개 읽기 | **유지** — 게시판·글·댓글 전부 그대로 보인다 |
| 만료 시 기존 게시글 | **유지** — 지우지 않는다 |
| 만료 시 관리 작업 | **재인증 요구** — 게시판 설정 변경, 카테고리 관리, 공지 고정, 모집 등록 |
| 재인증 실패·연결 해제 | **`orphaned`** 전환 |
| 즉시 삭제 | **금지** |

**만료가 읽기를 막지 않는 이유:** 스트리머가 90일 동안 재인증하지 않았다는 사실과
팬들이 쓴 글이 사라져야 한다는 것은 아무 관계가 없다. 커뮤니티는 스트리머 개인이
아니라 그 공간에 쌓인 글이다.

**`orphaned` 상태의 계약**

| 동작 | orphaned |
|---|---|
| 게시판·글·댓글 읽기 | ✅ 유지 |
| 일반 사용자 새 글·댓글 | ⚠️ **정책 미확정** — COMMUNITY-1 착수 시 결정(§26-B 잔여) |
| 소유자 관리 작업 | ❌ 차단 |
| 새 모집 등록 | ❌ 차단 |
| 복구 | ✅ 원 소유자 또는 새 주인이 OAuth 재통과하면 즉시 정상화 |
| 삭제 | ❌ 자동 삭제 없음. OWNER 판단으로만 |

**OWNER의 역할은 숨김·차단·소유권 분쟁 처리로 한정한다.** 사전 심사도, 자동 삭제도
하지 않는다(§26-E와 동일한 원칙).

---

#### B-3. 토큰 저장 — 게시판 테이블에 복제하지 않는다

**`community_boards`와 `community_board_owners`에 access/refresh token을 넣지 않는다.**
이 테이블들은 공개 화면이 읽는 경로에 있어 조회 코드가 늘어날수록 유출면이 커진다.

- 소유권 판정에 필요한 것은 **"검증이 통과했다"는 사실과 시각**뿐이다.
  토큰은 그 판정을 만든 수단이지 보관 대상이 아니다
- **토큰 보관이 꼭 필요해지면**(예: 주기적 자동 재검증) **기존 보안 저장 경로를
  재사용**한다 — 지금 `chzzk_subscriptions.streamer_access_token`/`streamer_refresh_token`이
  그 역할을 하고 있다. **평문 토큰을 새 테이블에 복제하지 않는다**
- 그마저도 새 구조가 필요하면 **암호화·접근 경로·삭제 정책을 먼저 설계**하고 착수한다.
  "일단 넣고 나중에 암호화"는 하지 않는다
- MVP의 재검증은 **사용자가 관리 작업을 시도할 때 OAuth를 다시 태우는 방식**이라
  토큰 보관 자체가 필요 없다. 이게 B-3을 지킬 수 있는 이유다

**최소 보존·삭제 정책** — 콜백에서 받은 토큰은 채널 id를 얻는 **그 요청 안에서만**
쓰고 버린다. 로그에 남기지 않는다(기존 `log_redaction` 관례).

---

#### 계정 삭제 및 작성자 익명화 (§26-A와 연결)

| 대상 | 처리 |
|---|---|
| `community_board_owners` | 해당 `discord_user_id` 행을 `revoked_at`으로 닫는다 → 게시판은 `orphaned` |
| 게시판·글·댓글 | **삭제하지 않는다** — 팬들이 쓴 글까지 사라진다 |
| 작성자 표시 | `author_name`/`author_avatar`를 비우고 "(탈퇴한 사용자)"로 표시(§26-A) |
| `author_id` | 신고·중복 판정에 필요해 남기되, 필요하면 해시로 대체 |
| 토큰 | 애초에 저장하지 않는다(B-3) |

**이 익명화 경로를 COMMUNITY-1에 함께 구현한다.**

---

#### §26-B 잔여 항목 (COMMUNITY-1 착수 시 결정, 스키마 영향 없음)

1. **`orphaned` 게시판에 일반 사용자가 새 글을 쓸 수 있는가** — 읽기 유지는 확정,
   쓰기는 미정. 관리자가 없는 공간의 스팸 대응과 맞물린다
2. **치지직 OAuth의 PKCE 지원 여부** — 지원하면 state JWT에 더해 적용

**이 둘은 컬럼을 바꾸지 않으므로 `community_boards`·`community_board_owners`
스키마 작성을 막지 않는다.**

---

### §26-C. 본문 — 제한된 Markdown

**허용 문법 (이 목록이 전부다. 여기 없는 것은 텍스트로 렌더한다):**

| 문법 | 표기 |
|---|---|
| 굵게 / 기울임 | `**굵게**` · `*기울임*` |
| 인라인 코드 / 코드 블록 | `` `코드` `` · ```` ```블록``` ```` |
| 링크 | `[텍스트](https://…)` — **`http`/`https`만** |
| 인용 | `> 인용` |
| 목록 | `- 항목` · `1. 항목` |
| 구분선 | `---` |
| 줄바꿈 | 빈 줄 = 문단 |

**금지 (서버에서 파싱 단계에 제거한다. 프론트 필터에 기대지 않는다):**

- **raw HTML 전부** — `<script>` · `<style>` · `<iframe>` · `<object>` · `<embed>` ·
  `<svg>` · 이벤트 속성(`onerror` 등). 화이트리스트 노드만 남기고 나머지는 텍스트로
- `javascript:` · `data:` · `vbscript:` 스킴 링크
- **이미지 문법 `![]()`** — MVP에서는 이미지 임베드를 하지 않는다.
  외부 이미지 주소는 **링크로만** 표시한다(§2). 저장소·프록시·악성 파일 검사가
  필요 없어지는 것이 이 결정의 핵심이다
- 외부 링크에는 `rel="noopener noreferrer nofollow"` · `target="_blank"`

`dangerouslySetInnerHTML`은 **어디에도 쓰지 않는다.** 파서가 만든 노드 트리를
React 엘리먼트로 변환한다.

---

### §26-D. 읽기 — 비회원 전면 허용

- **조회는 전부 비회원 허용**: 게시판 목록·게시판·글·댓글·모집 목록·모집 상세
- **로그인이 필요한 것은 쓰기뿐**: 글·댓글 작성/수정/삭제, 반응, 참가 신청·취소,
  콘텐츠 제출, 신고, 게시판 개설·설정
- **로그인 후 원래 위치로 복귀한다.** 진입 경로를 `LoginGate`(§5) 한 곳에서만
  기억한다 — 여러 곳에서 각자 저장하면 반드시 어긋난다.
  복귀 경로는 **같은 출처의 상대 경로만** 허용한다(오픈 리다이렉트 방지)
- 게시판 진입 자체를 막지 않는 이유는 SEO다(§15). 읽기를 막으면 검색 유입이 전부 튕긴다

---

### §26-E. 개설 자격 — 치지직 인증 스트리머만

- 개설 경로는 **하나뿐**: 치지직 채널 소유 확인(§26-B)
- **OWNER 수동 승인제를 기본 경로로 두지 않는다.** 개설 비용 0이 이 서비스의
  존재 이유인데(§1), 승인 대기열을 넣으면 그게 사라진다
- OWNER가 갖는 것은 **사후 권한**뿐: 차단·숨김·복구·게시판 정지(§18).
  사전 심사가 아니라 사후 조치다

---

### §26-F. MVP 3분할 — 한 번에 구현하지 않는다

| 단계 | 범위 |
|---|---|
| **COMMUNITY-1 Core** | 기존 Discord JWT 연동 · 게시판 개설·조회 · 기본 카테고리 · 글 CRUD · 댓글 1단계 · 공지 고정 · 자유게시판 · 검색 · 신고 및 OWNER 숨김 · 모바일 UI · **최소 알림 DB 적재** |
| **COMMUNITY-2 Recruitment** | 시참 모집 · 신청/취소/마감 · 콘텐츠 모집/제출/마감 · 홈 "현재 모집 중" · `/recruits` · 관련 운영 화면 |
| **COMMUNITY-3 이후** | 실시간 알림 · 이메일/Discord DM · 파일 업로드 · 대댓글·멘션 · 추천 알고리즘 · 포인트·레벨·배지 · WebSocket · AI |

이 분할에 따라 §24의 단계표를 다시 읽는다: C1~C3이 COMMUNITY-1,
C4~C6이 COMMUNITY-2, C7~C8은 두 단계에 나눠 붙는다(신고·숨김은 Core에 포함).

**§26-B의 B-1·B-2가 확정됐으므로 COMMUNITY-1의 설계 진입 조건은 충족됐다.**
다만 이번 작업 순서상 **SINGCUP-1이 COMMUNITY-1보다 우선**이다.

---

### 이 문서의 현재 상태

- **설계만. 코드 0줄.** B-1·B-2 확정 완료 — 다만 **SINGCUP-1이 우선**이라 COMMUNITY-1은 그 뒤
- 이 문서 자체도 **사용자 승인 전 commit·push하지 않는다**
