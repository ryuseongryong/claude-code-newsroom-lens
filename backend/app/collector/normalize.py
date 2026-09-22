"""정규화 — 서로 다른 입력 모양을 Article 하나로 접는다.

여기가 이 프로젝트의 경계다. XML 이냐 JSON 이냐, 날짜가 RFC822 냐 epoch 밀리초냐
하는 차이는 전부 이 파일 안에서 죽는다. 아래 계층(store·api·llm·UI)은 Article 만 본다.
"""

from __future__ import annotations

import html
import re
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urljoin

from ..config import TOPIC_KEYWORDS, FeedSpec, settings
from ..store.models import Article

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")

NHK_BASE = "https://www3.nhk.or.jp"


def strip_html(raw: str | None) -> str:
    """태그를 걷고 엔티티를 풀고 공백을 하나로 접는다.

    엔티티 해제를 태그 제거 *뒤에* 한다. 순서를 바꾸면 `&lt;script&gt;` 가 실제
    태그로 되살아난 뒤 지워져서 본문이 사라진다.
    """
    if not raw:
        return ""
    text = _TAG_RE.sub(" ", raw)
    text = html.unescape(text)
    return _WS_RE.sub(" ", text).strip()


def clamp_summary(text: str, limit: int | None = None) -> str:
    limit = limit or settings.summary_max_chars
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def matches_topic(title: str, summary: str = "") -> bool:
    """제목+요약에 주제 키워드가 하나라도 있는가.

    낱말 기반이라 완벽하지 않다. 그래서 `needs_topic_filter=True` 인 피드에만 쓰고,
    걸러낸 건수를 상태에 남겨 화면에 보여준다 — 조용히 버리면 '소스가 죽은 것' 과
    '필터가 과하게 먹은 것' 을 구별할 수 없다.
    """
    haystack = f"{title} {summary}".lower()
    return any(kw.lower() in haystack for kw in TOPIC_KEYWORDS)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def _now_iso() -> str:
    return _iso(datetime.now(timezone.utc))


# ── RSS ──────────────────────────────────────────────────────────────────────


def _rss_published(entry: Any, spec: FeedSpec | None = None) -> str:
    """feedparser 가 준 시간을 UTC ISO 로. 실패하면 '지금'으로 떨어뜨린다.

    published_parsed 는 feedparser 가 이미 UTC 로 바꿔 놓은 struct_time 이다.
    없으면 updated_parsed 를 본다. 둘 다 없는 항목은 버리지 않고 수집 시각을 쓴다 —
    날짜 하나 때문에 기사를 잃는 쪽이 더 나쁘다.

    `spec.assume_tz_offset_hours` 가 0 이 아니면 그만큼 빼서 실제 UTC 로 맞춘다.
    국내 업계지들이 '2026-09-22 15:09:04' 처럼 타임존 없는 KST 를 보내는데,
    feedparser 는 표기 없는 시각을 UTC 로 읽어 기사를 9시간 미래로 밀어 놓는다.
    그대로 두면 모든 기사가 '방금' 으로 보이고 정체 판정이 영원히 통과한다.
    """
    offset = spec.assume_tz_offset_hours if spec else 0.0
    for attr in ("published_parsed", "updated_parsed"):
        parsed = getattr(entry, attr, None) or (entry.get(attr) if hasattr(entry, "get") else None)
        if not parsed:
            continue
        try:
            dt = datetime(*parsed[:6], tzinfo=timezone.utc)
        except (TypeError, ValueError):
            continue
        if offset:
            dt -= timedelta(hours=offset)
        return _iso(dt)
    return _now_iso()


def normalize_rss_entry(spec: FeedSpec, entry: Any) -> Article | None:
    title = strip_html(entry.get("title"))
    link = (entry.get("link") or "").strip()
    if not title or not link:
        return None  # 제목이나 링크가 없으면 비교도 인용도 불가능하다.
    raw_summary = entry.get("summary") or entry.get("description") or ""
    return Article(
        source=spec.key,
        title=title,
        link=link,
        published=_rss_published(entry, spec),
        summary=clamp_summary(strip_html(raw_summary)),
    )


# ── NHK World JSON ───────────────────────────────────────────────────────────


def _nhk_published(item: dict[str, Any]) -> str:
    """NHK 는 epoch 밀리초 문자열을 준다. 비어 있으면 id 앞 8자리(YYYYMMDD)로 후퇴."""
    for key in ("public_at", "updated_at", "created_at"):
        raw = (item.get(key) or "").strip()
        if not raw.isdigit():
            continue
        try:
            return _iso(datetime.fromtimestamp(int(raw) / 1000, tz=timezone.utc))
        except (OverflowError, OSError, ValueError):
            continue
    ident = (item.get("id") or "")[:8]
    if len(ident) == 8 and ident.isdigit():
        try:
            return _iso(datetime.strptime(ident, "%Y%m%d").replace(tzinfo=timezone.utc))
        except ValueError:
            pass
    return _now_iso()


def normalize_nhk_item(spec: FeedSpec, item: dict[str, Any]) -> Article | None:
    title = strip_html(item.get("title"))
    page_url = (item.get("page_url") or "").strip()
    if not title or not page_url:
        return None
    return Article(
        source=spec.key,
        title=title,
        # page_url 은 '/nhkworld/en/news/...' 형태의 상대 경로다. 절대 URL 로 만들어야
        # 카드의 링크가 우리 도메인으로 잘못 걸리지 않는다.
        link=urljoin(NHK_BASE, page_url),
        published=_nhk_published(item),
        summary=clamp_summary(strip_html(item.get("description"))),
    )
