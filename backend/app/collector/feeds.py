"""피드 가져오기 — 네트워크 I/O 와 파싱. 한 곳당 한 함수 호출로 끝난다."""

from __future__ import annotations

import json
import logging
from typing import NamedTuple

import feedparser
import httpx

from ..config import FeedSpec, settings
from ..store.models import Article
from .normalize import matches_topic, normalize_nhk_item, normalize_rss_entry

log = logging.getLogger(__name__)


class FeedError(RuntimeError):
    """이 피드 하나가 실패했다는 뜻. 절대 프로세스를 죽이지 않는다."""


class FetchResult(NamedTuple):
    """가져온 기사와, 주제 필터가 걸러낸 건수.

    걸러낸 건수를 굳이 들고 다니는 이유: 이걸 버리면 '피드가 조용한 것' 과
    '필터가 다 먹은 것' 이 화면에서 똑같이 0건으로 보인다.
    """

    articles: list[Article]
    filtered_out: int = 0


async def fetch_feed(client: httpx.AsyncClient, spec: FeedSpec) -> FetchResult:
    """한 매체에서 기사 목록을 받아 정규화·주제 필터까지 마친다.

    실패는 전부 FeedError 로 좁혀서 올린다 — 호출자가 매체별로 격리하기 쉽게.
    """
    try:
        response = await client.get(spec.url)
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise FeedError(f"HTTP {exc.response.status_code}") from exc
    except httpx.HTTPError as exc:
        raise FeedError(f"{type(exc).__name__}: {exc}") from exc

    if spec.kind == "rss":
        articles = _parse_rss(spec, response.content)
    elif spec.kind == "nhk_json":
        articles = _parse_nhk_json(spec, response.text)
    else:
        raise FeedError(f"알 수 없는 피드 종류: {spec.kind}")

    return _apply_topic_filter(spec, articles)


def _apply_topic_filter(spec: FeedSpec, articles: list[Article]) -> FetchResult:
    """화장품 외 분야를 함께 싣는 피드에서 주제 밖 기사를 덜어낸다.

    needs_topic_filter=False 인 피드는 건드리지 않는다. 화장품 전문지의 기사를
    낱말 유무로 다시 판정하면, 업종 낱말 없는 실적·인사 기사가 잘못 버려진다.
    """
    if not spec.needs_topic_filter:
        return FetchResult(articles, 0)

    kept = [a for a in articles if matches_topic(a.title, a.summary)]
    dropped = len(articles) - len(kept)
    if dropped:
        log.info("주제 필터 [%s] %d건 중 %d건 제외", spec.key, len(articles), dropped)
    if not kept:
        # 전부 걸러졌다. 실패로 올리지 않는다 — 수집은 성공했고, 이 시점에 이 매체가
        # 화장품 기사를 안 썼을 수도 있다. 다만 0건이라는 사실은 그대로 보고한다.
        log.warning("주제 필터 [%s] 남은 기사 0건 (수신 %d건)", spec.key, len(articles))
    return FetchResult(kept, dropped)


def _parse_rss(spec: FeedSpec, payload: bytes) -> list[Article]:
    # feedparser 는 예외를 던지지 않고 bozo 플래그를 세운다. 망가진 XML 에서도
    # 살릴 수 있는 항목은 살리므로, bozo 만으로 실패 처리하지 않고 entries 를 본다.
    parsed = feedparser.parse(payload)
    entries = getattr(parsed, "entries", []) or []
    if not entries:
        reason = getattr(parsed, "bozo_exception", None)
        raise FeedError(f"항목 0건{f' ({reason})' if reason else ''}")
    articles = [normalize_rss_entry(spec, e) for e in entries]
    return [a for a in articles if a is not None]


def _parse_nhk_json(spec: FeedSpec, payload: str) -> list[Article]:
    try:
        data = json.loads(payload).get("data") or []
    except (json.JSONDecodeError, AttributeError) as exc:
        raise FeedError(f"JSON 파싱 실패: {exc}") from exc
    if not isinstance(data, list) or not data:
        raise FeedError("항목 0건")
    articles = [normalize_nhk_item(spec, item) for item in data if isinstance(item, dict)]
    kept = [a for a in articles if a is not None]
    if not kept:
        raise FeedError("정규화 가능한 항목 0건")
    return kept


def make_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=settings.fetch_timeout_seconds,
        follow_redirects=True,
        headers={
            "User-Agent": settings.user_agent,
            "Accept": "application/rss+xml, application/xml, application/json;q=0.9, */*;q=0.8",
        },
    )
