"""폴러 — 120초마다 4개 매체를 동시에 긁는다.

격리 규칙: 한 피드가 죽어도 나머지 셋은 갱신된다. 그래서 gather 에
`return_exceptions=True` 를 주고, 예외를 매체별 상태로 기록만 한다. 루프 자체는
어떤 경우에도 빠져나오지 않는다 — 루프가 죽으면 화면이 조용히 늙는다.
"""

from __future__ import annotations

import asyncio
import logging

from ..config import FEEDS, FeedSpec, settings
from ..store import ArticleStore, store
from .feeds import FeedError, fetch_feed, make_client

log = logging.getLogger(__name__)


async def poll_once(target: ArticleStore = store) -> dict[str, int | str]:
    """전 매체 1회 수집. 매체별 결과 요약을 돌려준다(테스트·수동 트리거용)."""
    results: dict[str, int | str] = {}
    async with make_client() as client:
        outcomes = await asyncio.gather(
            *(fetch_feed(client, spec) for spec in FEEDS),
            return_exceptions=True,
        )
    for spec, outcome in zip(FEEDS, outcomes):
        results[spec.key] = _apply(target, spec, outcome)
    return results


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
