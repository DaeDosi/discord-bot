"""포인트 도박 정산 공통 로직.

Discord Poll 기반(/포인트도박, cogs/points.py)과 치지직 채팅 기반(!도박, cogs/chzzk_chat.py)
도박은 투표 수집 방식은 다르지만(전자는 정산 시점에 결과를 훑고 잔액이 되는 사람만 차감,
후자는 투표 즉시 차감) 승자 판정과 배당금 계산 공식은 동일하므로 여기서 공유한다.
"""
from typing import TypeVar

K = TypeVar("K")


def resolve_gambling_winner(vote_counts: dict[K, int], discord_poll_victor: K | None = None) -> K | None:
    """다수결 승자 옵션 키를 판정한다. discord_poll_victor가 주어지면(Discord Poll API 자체
    판정 결과) 그것을 그대로 쓰고, 없으면 최다 득표 옵션으로 판정한다(동률이면 먼저 나온 항목).
    득표가 전혀 없으면 None."""
    if discord_poll_victor is not None:
        return discord_poll_victor
    if not vote_counts or not any(vote_counts.values()):
        return None
    return max(vote_counts, key=lambda k: vote_counts[k])


def calc_gambling_payout(winners: list, total_charged_count: int, bet_amount: int) -> int:
    """당첨자 1인당 배당금 = (실제 차감된 전체 인원 수 * 베팅액) // 당첨자 수."""
    if not winners:
        return 0
    pool = total_charged_count * bet_amount
    return pool // len(winners)
