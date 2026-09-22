"""피드 가져오기 — 네트워크 I/O 와 파싱. 한 곳당 한 함수 호출로 끝난다."""

from __future__ import annotations

import json
import logging

import feedparser
import httpx

from ..config import FeedSpec, settings
from ..store.models import Article
from .normalize import normalize_nhk_item, normalize_rss_entry

log = logging.getLogger(__name__)


class FeedError(RuntimeError):
    """이 피드 하나가 실패했다는 뜻. 절대 프로세스를 죽이지 않는다."""


async def fetch_feed(client: httpx.AsyncClient, spec: FeedSpec) -> list[Article]:
    """한 매체에서 기사 목록을 받아 정규화까지 마친다.

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
        return _parse_rss(spec, response.content)
    if spec.kind == "nhk_json":
        return _parse_nhk_json(spec, response.text)
    raise FeedError(f"알 수 없는 피드 종류: {spec.kind}")


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
