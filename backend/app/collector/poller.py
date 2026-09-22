"""폴러 — 120초마다 4개 매체를 동시에 긁는다.

격리 규칙: 한 피드가 죽어도 나머지 셋은 갱신된다. 그래서 gather 에
`return_exceptions=True` 를 주고, 예외를 매체별 상태로 기록만 한다. 루프 자체는
어떤 경우에도 빠져나오지 않는다 — 루프가 죽으면 화면이 조용히 늙는다.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time

from ..config import FEEDS, FeedSpec, settings
from ..store import ArticleStore, store
from .feeds import FeedError, fetch_feed, make_client

log = logging.getLogger(__name__)

# 마지막으로 '실제로' 수집한 시각. 자동 주기와 수동 새로 고침이 같이 쓴다 —
# 자동 폴이 3초 전에 돌았다면 수동 새로 고침이 또 긁을 이유가 없다.
# monotonic 을 쓴다: 시스템 시계가 조정되어도 간격 계산이 음수로 뒤집히지 않는다.
_last_poll_at: float | None = None
_manual_lock = asyncio.Lock()


async def poll_once(target: ArticleStore = store) -> dict[str, int | str]:
    """전 매체 1회 수집. 매체별 결과 요약을 돌려준다(테스트·수동 트리거용)."""
    global _last_poll_at
    results: dict[str, int | str] = {}
    async with make_client() as client:
        outcomes = await asyncio.gather(
            *(fetch_feed(client, spec) for spec in FEEDS),
            return_exceptions=True,
        )
    for spec, outcome in zip(FEEDS, outcomes):
        results[spec.key] = _apply(target, spec, outcome)
    _last_poll_at = time.monotonic()
    return results


def seconds_until_refresh_allowed() -> int:
    """수동 새로 고침까지 남은 초. 0이면 지금 가능."""
    if _last_poll_at is None:
        return 0
    remaining = settings.manual_refresh_min_seconds - (time.monotonic() - _last_poll_at)
    return max(0, math.ceil(remaining))


async def request_refresh(target: ArticleStore = store) -> dict[str, object]:
    """'지금 새로 고침'. 최소 간격 안이면 긁지 않고 남은 시간을 알려준다.

    거부를 오류로 취급하지 않는다 — 사용자는 아무것도 잘못하지 않았고, 화면에 이미
    있는 기사가 최신이다. 그래서 200 으로 '안 긁었고 N초 뒤 가능' 이라고 말한다.
    """
    async with _manual_lock:
        # 락을 기다리는 동안 다른 요청이 이미 긁었을 수 있다. 다시 확인한다.
        wait = seconds_until_refresh_allowed()
        if wait > 0:
            return {"refreshed": False, "retry_after_seconds": wait}
        log.info("수동 새로 고침 요청 — 즉시 수집")
        results = await poll_once(target)
        return {
            "refreshed": True,
            "results": {k: v for k, v in results.items()},
            "retry_after_seconds": settings.manual_refresh_min_seconds,
        }


def _apply(target: ArticleStore, spec: FeedSpec, outcome: object) -> int | str:
    if isinstance(outcome, BaseException):
        # FeedError 든 예상 못 한 예외든 같은 처리: 이 매체만 실패로 표시한다.
        reason = f"{type(outcome).__name__}: {outcome}" if not isinstance(outcome, FeedError) else str(outcome)
        target.record_failure(spec.key, reason)
        log.warning("수집 실패 [%s] %s", spec.key, reason)
        return reason
    articles = list(outcome)  # type: ignore[arg-type]
    added = target.record_success(spec.key, articles)
    log.info("수집 성공 [%s] %d건 수신 / 신규 %d건", spec.key, len(articles), added)
    return added


async def poll_loop(target: ArticleStore = store) -> None:
    """앱 수명 동안 도는 백그라운드 루프."""
    interval = settings.poll_interval_seconds
    log.info("폴러 시작: %d초 간격, 매체 %d곳", interval, len(FEEDS))
    while True:
        try:
            await poll_once(target)
        except asyncio.CancelledError:
            log.info("폴러 종료")
            raise
        except Exception:  # noqa: BLE001 - 루프는 무슨 일이 있어도 계속 돈다
            log.exception("폴 주기에서 예상 못 한 오류 — 다음 주기에 재시도")
        await asyncio.sleep(interval)
