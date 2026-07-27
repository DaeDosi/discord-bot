"""치지직 첫 방송일 수집 API (공개, 무인증).

`chzzk_channel_history` 모듈의 얇은 HTTP 껍데기다. 캐시·속도제한·재시도 정책은 전부
모듈 쪽에 있으므로 여기서는 입력 검증과 응답 형태만 다룬다.

무인증 공개 엔드포인트가 외부(치지직) 호출을 유발하므로 남용 방어가 필요하다 —
`rate_limit.py`의 '비싼 경로' 목록에 /api/chzzk/channel-history 를 등록해 두었다.
"""
import uuid

from chzzk_channel_history import (
    MAX_BATCH_SIZE,
    InvalidChannelError,
    collect_batch,
    get_channel_history,
    metrics_snapshot,
)
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

# 기존 chzzk_router 와 같은 prefix를 쓰지만 경로가 리터럴('channel-history')이라
# /{guild_id}/... 패턴과 충돌하지 않는다. main.py에서 chzzk_router보다 먼저 등록한다.
router = APIRouter(prefix="/api/chzzk", tags=["chzzk-history"])


class ChannelHistoryRequest(BaseModel):
    channel: str = Field(..., description="채널 ID 또는 치지직 URL")
    refresh: bool = False


class ChannelHistoryBatchRequest(BaseModel):
    channels: list[str] = Field(default_factory=list)
    refresh: bool = False


@router.post("/channel-history")
async def channel_history(body: ChannelHistoryRequest):
    """단일 채널의 첫 방송일/누적 방송시간. DB에 값이 있으면 외부 호출 없이 즉시 반환."""
    try:
        return await get_channel_history(body.channel, refresh=body.refresh)
    except InvalidChannelError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/channel-history/batch")
async def channel_history_batch(body: ChannelHistoryBatchRequest):
    """여러 채널 수집. 중복 제거 후 동시성·속도 제한을 걸어 순차적으로 처리한다."""
    if not body.channels:
        raise HTTPException(status_code=400, detail="channels가 비어 있습니다.")
    if len(body.channels) > MAX_BATCH_SIZE:
        raise HTTPException(
            status_code=400,
            detail=f"한 번에 최대 {MAX_BATCH_SIZE}개까지 요청할 수 있습니다.",
        )
    return await collect_batch(body.channels, refresh=body.refresh,
                               job_id=uuid.uuid4().hex[:8])


@router.get("/channel-history/metrics")
async def channel_history_metrics():
    """운영 모니터링용 카운터 — 성공/캐시적중률/404·403·429/평균 응답시간/대기 중 배치 수."""
    return metrics_snapshot()
