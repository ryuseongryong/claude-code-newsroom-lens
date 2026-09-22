"""저장 스키마 — 4개 매체가 하나의 모양으로 합쳐지는 지점.

기사 본문은 저장하지 않는다. 제목·링크·요약(300자)만 들고 원문으로 링크한다.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any


@dataclass(frozen=True, slots=True)
class Article:
    """정규화된 기사 한 건. 어떤 매체에서 왔는지와 무관하게 이 모양이다."""

    source: str          # FeedSpec.key
    title: str
    link: str            # 멱등 키. 같은 link 는 같은 기사다.
    published: str       # UTC ISO8601 (예: 2026-09-22T04:31:00+00:00)
    summary: str         # HTML 제거 + 300자 컷

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def published_dt(self) -> datetime:
        """정렬용. 파싱 실패한 값이 섞여도 터지지 않게 epoch 로 떨어뜨린다."""
        try:
            return datetime.fromisoformat(self.published)
        except ValueError:
            return datetime.fromtimestamp(0, tz=timezone.utc)


@dataclass
class SourceStatus:
    """매체 한 곳의 건강 상태. 화면 상단 상태바가 이걸 그대로 보여준다."""

    source: str
    label: str
    label_en: str
    lang: str
    count: int = 0
    last_success: str | None = None   # 마지막으로 기사를 얻어낸 시각 (UTC ISO)
    last_attempt: str | None = None
    last_error: str | None = None     # 살아있는 실패 이유. None 이면 정상.
    consecutive_failures: int = 0

    @property
    def healthy(self) -> bool:
        return self.last_error is None and self.count > 0

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "healthy": self.healthy}
