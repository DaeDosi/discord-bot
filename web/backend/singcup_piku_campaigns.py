"""싱드컵 PIKU **campaign(단계) 레지스트리** — 예선과 본선을 분리하는 단 하나의 정본.

## 왜 필요한가

PIKU 수집·매핑·공개 코드는 원래 `division`(female_solo / male_solo / groups) 하나로
모든 것을 구분했다. 2026-09-10부터 **파이널 본선**이 별도 PIKU 페이지(`2ut8Li`)에서
진행되는데, 본선은 여성·남성·그룹이 **한 표에 섞여 있는 순위 하나**다. 예선 부문
키에 억지로 넣으면 (a) 32행 계약이 그룹 부문과 겹쳐 조용히 오염되고 (b) 본선
데이터가 예선 활성본을 덮어쓴다.

그래서 **campaign**을 도입한다. 저장소의 `division` 컬럼은 이제 "source key"다:

    campaign   source key                     PIKU sourceId   기대 행 수
    qualifier  female_solo/male_solo/groups   8jGsHE/7PqH44/7fXoNs   64/64/32
    final      final                          2ut8Li          32

source key는 campaign을 넘어 **서로 겹치지 않는다.** 그래서 기존 테이블의
`(division, ...)` 기본키가 그대로 유효하고, 기존 예선 행은 한 바이트도 바뀌지
않는다(`campaign` 컬럼은 append-only로 붙고 기본값이 `qualifier`다).

## 예선은 동결(frozen)이다

사용자 PC 고장으로 예선 마지막 구간 수집이 빠졌지만 **소급 갱신은 보류**다.
게다가 예선 PIKU 페이지 세 개는 2026-09-12 실측으로 **전부 404(삭제)** 라 다시
읽을 수도 없다. 그래서 예선 campaign은 `frozen`이고, 이 상태에서는 challenge·
ingest·import·publish가 전부 `campaign_frozen`으로 거절된다 — 새 데이터가
예선 활성본을 덮어쓸 경로 자체가 없다.

## 본선 행 수 32의 근거

2026-09-12 운영자 브라우저에서 `https://www.piku.co.kr/w/rank/2ut8Li`를 읽기 전용으로
확인: DataTables `recordsTotal=32`, 순위 1~32 연속, 그룹 12·솔로 20. 페이지 제목은
`이상형 월드컵 랭킹 - [2026 치지직 싱드컵 갤럭시] - 파이널 본선 ...`. 이 페이지에는
'보기 개수' 컨트롤이 없다(`bLengthChange=false`, 10행×4페이지).
"""
from __future__ import annotations

from typing import Any

#: 예선 부문(기존 값 그대로). 다른 모듈의 `DIVISIONS`와 같은 값이어야 한다.
QUALIFIER_SOURCES: tuple[str, ...] = ("female_solo", "male_solo", "groups")
FINAL_SOURCE = "final"

CAMPAIGN_QUALIFIER = "qualifier"
CAMPAIGN_FINAL = "final"

#: source key → 정의. **여기 없는 source key는 어디에서도 받지 않는다.**
SOURCES: dict[str, dict[str, Any]] = {
    "female_solo": {
        "campaign": CAMPAIGN_QUALIFIER, "sourceId": "8jGsHE", "expected": 64,
        "label": "여성 솔로", "title": "", "paged": False, "mixed": False,
    },
    "male_solo": {
        "campaign": CAMPAIGN_QUALIFIER, "sourceId": "7PqH44", "expected": 64,
        "label": "남성 솔로", "title": "", "paged": False, "mixed": False,
    },
    "groups": {
        "campaign": CAMPAIGN_QUALIFIER, "sourceId": "7fXoNs", "expected": 32,
        "label": "그룹", "title": "", "paged": False, "mixed": False,
    },
    FINAL_SOURCE: {
        "campaign": CAMPAIGN_FINAL, "sourceId": "2ut8Li", "expected": 32,
        "label": "파이널 본선",
        # 페이지 제목에 이 문자열이 **포함**돼야 한다(정확한 sourceId 검사에 더해
        # 사람이 읽는 근거를 하나 더 둔다 — 같은 id로 내용이 바뀌면 여기서 걸린다).
        "title": "[2026 치지직 싱드컵 갤럭시] - 파이널 본선",
        # 이 페이지는 '보기 개수'를 못 바꾼다. 사람이 페이지 번호를 누르듯 넘겨 읽는다.
        "paged": True,
        # 여성·남성·그룹이 한 표에 섞여 있다 → 매핑 색인은 세 부문 전체를 합친다.
        "mixed": True,
    },
}

for _k, _v in SOURCES.items():
    _v["url"] = f"https://www.piku.co.kr/w/rank/{_v['sourceId']}"

#: campaign 정의. `status`는 코드 상수다 — 운영 중 실수로 예선이 풀리지 않게
#: DB나 환경변수로 바꾸지 않는다(바꾸려면 코드 변경 = 커밋 = 검토).
CAMPAIGNS: dict[str, dict[str, Any]] = {
    CAMPAIGN_QUALIFIER: {
        "label": "예선",
        "status": "frozen",
        "sources": list(QUALIFIER_SOURCES),
        # 일정은 예선 공지 기준. 마지막 수집·공개는 DB가 안다(`campaign_status`).
        "collectionStartAt": "",
        "collectionEndAt": "",
        "note": "예선 마지막 구간 수집이 누락됐고 소급 갱신은 보류 상태입니다. "
                "예선 PIKU 페이지는 삭제되어 다시 읽을 수 없습니다.",
    },
    CAMPAIGN_FINAL: {
        "label": "본선",
        "status": "active",
        "sources": [FINAL_SOURCE],
        # 사용자 제공 정보(2026-09-12). 공식 공지로 교차 확인하지 못했다 — 화면에
        # 그 사실을 함께 적는다.
        "collectionStartAt": "2026-09-10T00:00:00+09:00",
        "collectionEndAt": "2026-09-16T23:59:59+09:00",
        "scheduleSource": "user_provided",
        "note": "",
    },
}

#: 지금 자동 수집이 요구하는 source. **예선 탭이 없다는 이유로 본선이 실패하면
#: 안 된다** — 스케줄러는 이 plan에 있는 source만 찾는다.
ACTIVE_CAMPAIGN = CAMPAIGN_FINAL


class CampaignError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.kind = code          # `singcup_piku.PikuError`와 같은 속성명으로도 읽히게
        self.message = message


def campaign_of(source: str) -> str:
    """source key의 campaign. 모르는 키는 예외다(추측하지 않는다)."""
    meta = SOURCES.get(source)
    if not meta:
        raise CampaignError("bad_division", "알 수 없는 부문입니다.")
    return meta["campaign"]


def source_meta(source: str) -> dict[str, Any]:
    meta = SOURCES.get(source)
    if not meta:
        raise CampaignError("bad_division", "알 수 없는 부문입니다.")
    return meta


def is_frozen(source: str) -> bool:
    return CAMPAIGNS[campaign_of(source)]["status"] != "active"


def assert_writable(source: str) -> None:
    """수집·import·공개처럼 **데이터를 바꾸는 경로**의 공통 게이트."""
    camp = campaign_of(source)
    if CAMPAIGNS[camp]["status"] != "active":
        raise CampaignError(
            "campaign_frozen",
            f"{CAMPAIGNS[camp]['label']}은(는) 동결 상태라 새 수집·공개를 받지 않습니다.")


def sources_of(campaign: str) -> list[str]:
    if campaign not in CAMPAIGNS:
        raise CampaignError("bad_campaign", "알 수 없는 단계입니다.")
    return list(CAMPAIGNS[campaign]["sources"])


def plan() -> dict[str, Any]:
    """확장 스케줄러에 내려 주는 **활성 collection plan**. secret 없음."""
    camp = ACTIVE_CAMPAIGN
    return {
        "campaign": camp,
        "sources": [{
            "key": s,
            "sourceId": SOURCES[s]["sourceId"],
            "url": SOURCES[s]["url"],
            "expected": SOURCES[s]["expected"],
            "title": SOURCES[s]["title"],
            "paged": bool(SOURCES[s]["paged"]),
        } for s in CAMPAIGNS[camp]["sources"]],
    }


def public_meta() -> list[dict[str, Any]]:
    """공개 API용 campaign 목록(정적 부분). 시각은 호출부가 DB에서 붙인다."""
    return [{
        "campaign": k,
        "label": v["label"],
        "status": v["status"],
        "sources": [{
            "key": s, "label": SOURCES[s]["label"], "sourceId": SOURCES[s]["sourceId"],
            "url": SOURCES[s]["url"], "expected": SOURCES[s]["expected"],
        } for s in v["sources"]],
        "collectionStartAt": v["collectionStartAt"],
        "collectionEndAt": v["collectionEndAt"],
        "scheduleSource": v.get("scheduleSource", ""),
        "note": v["note"],
    } for k, v in CAMPAIGNS.items()]


# ── 테스트 전용 ─────────────────────────────────────────────────────────────
def _set_for_tests(*, active: str | None = None,
                   statuses: dict[str, str] | None = None) -> dict[str, Any]:
    """레지스트리를 잠시 바꾼다. **테스트 밖에서 부르지 말 것.**

    예선 수집·공개 계약을 검증하는 기존 테스트는 예선이 `active`여야 돈다.
    돌려준 스냅샷을 `_restore_for_tests`에 넘겨 원상복구한다.
    """
    global ACTIVE_CAMPAIGN
    snap = {"active": ACTIVE_CAMPAIGN,
            "statuses": {k: v["status"] for k, v in CAMPAIGNS.items()}}
    if active is not None:
        ACTIVE_CAMPAIGN = active
    for k, st in (statuses or {}).items():
        CAMPAIGNS[k]["status"] = st
    return snap


def _restore_for_tests(snap: dict[str, Any]) -> None:
    global ACTIVE_CAMPAIGN
    ACTIVE_CAMPAIGN = snap["active"]
    for k, st in snap["statuses"].items():
        CAMPAIGNS[k]["status"] = st
