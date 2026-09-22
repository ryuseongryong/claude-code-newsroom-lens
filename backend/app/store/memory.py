"""인메모리 저장소. 프로세스 재시작하면 비지만, 120초면 다시 찬다.

설계 두 가지만 기억하면 된다.
  1) link 가 멱등 키다. 같은 기사를 다시 받아도 중복으로 쌓이지 않는다.
  2) 매체별 상한이 있다(기본 50건). 오래된 것부터 버린다.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone

from ..config import FEEDS, FeedSpec, settings
from .models import Article, SourceStatus


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class ArticleStore:
    """매체별 기사 + 상태. 폴러(백그라운드)와 API(요청)가 같이 만지므로 락을 쓴다."""

    def __init__(self, feeds: tuple[FeedSpec, ...] = FEEDS) -> None:
        self._lock = threading.Lock()
        self._articles: dict[str, dict[str, Article]] = {f.key: {} for f in feeds}
        self._status: dict[str, SourceStatus] = {
            f.key: SourceStatus(source=f.key, label=f.label, label_en=f.label_en, lang=f.lang)
            for f in feeds
        }

    # ── 쓰기 ──────────────────────────────────────────────────────────────

    def record_success(self, source: str, articles: list[Article]) -> int:
        """수집 성공. 새로 들어온 건수를 돌려준다.

        기사가 0건이어도 '성공'이다 — 피드가 조용한 것과 피드가 죽은 것은 다르다.
        """
        now = _utcnow_iso()
        with self._lock:
            bucket = self._articles.setdefault(source, {})
            before = len(bucket)
            for art in articles:
                bucket[art.link] = art          # link 멱등: 덮어쓰기
            self._trim_locked(source)
            status = self._status.get(source)
            if status is not None:
                status.count = len(self._articles[source])
                status.last_success = now
                status.last_attempt = now
                status.last_error = None
                status.consecutive_failures = 0
            return max(0, len(self._articles[source]) - before)

    def record_failure(self, source: str, error: str) -> None:
        """수집 실패. 기존 기사는 지우지 않는다 — 마지막으로 성공한 화면을 유지한다."""
        with self._lock:
            status = self._status.get(source)
            if status is None:
                return
            status.last_attempt = _utcnow_iso()
            status.last_error = error[:300]
            status.consecutive_failures += 1

    def _trim_locked(self, source: str) -> None:
        bucket = self._articles[source]
        limit = settings.max_articles_per_source
        if len(bucket) <= limit:
            return
        # 최신순으로 세워 놓고 상한 넘는 꼬리를 버린다.
        keep = sorted(bucket.values(), key=lambda a: a.published_dt, reverse=True)[:limit]
        self._articles[source] = {a.link: a for a in keep}

    # ── 읽기 ──────────────────────────────────────────────────────────────

    def latest(self, source: str, limit: int | None = None) -> list[Article]:
        with self._lock:
            items = sorted(
                self._articles.get(source, {}).values(),
                key=lambda a: a.published_dt,
                reverse=True,
            )
        return items[:limit] if limit else items

    def snapshot(self, limit: int | None = None) -> dict[str, list[Article]]:
        return {key: self.latest(key, limit) for key in self._status}

    def statuses(self) -> list[SourceStatus]:
        with self._lock:
            return [
                SourceStatus(**{k: v for k, v in s.__dict__.items()})
                for s in self._status.values()
            ]

    def total_count(self) -> int:
        with self._lock:
            return sum(len(b) for b in self._articles.values())


store = ArticleStore()
