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


# 이 시간보다 오래된 기사만 있으면 '정체' 로 본다. 국제 뉴스 매체가 24시간 동안
# 아무것도 내지 않는 일은 없다 — 그건 소스가 멈춘 것이다.
STALE_AFTER_HOURS = 24


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
    # 이 매체가 가진 가장 최신 '기사' 의 발행 시각. last_success 와 다르다 —
    # last_success 는 '우리가 가져온 시각' 이고 이건 '매체가 쓴 시각' 이다.
    newest_published: str | None = None

    @property
    def stale_hours(self) -> float | None:
        """최신 기사가 몇 시간 전인가. 알 수 없으면 None."""
        if not self.newest_published:
            return None
        try:
            newest = datetime.fromisoformat(self.newest_published)
        except ValueError:
            return None
        return (datetime.now(timezone.utc) - newest).total_seconds() / 3600

    @property
    def stale(self) -> bool:
        """가져오기는 성공하는데 내용이 늙은 상태.

        이게 별도 상태여야 하는 이유: 얼어붙은 인덱스는 HTTP 200 에 기사 수백 건을
        정상으로 돌려준다. 그래서 healthy 만 보면 영원히 '정상' 이다. 실제로 NHK 의
        낡은 엔드포인트가 17일 전 기사를 정상 응답으로 계속 내보내고 있었고, 화면은
        그걸 아무 경고 없이 보여줬다. 죽은 소스보다 이게 더 위험하다 — 아무도 모른다.
        """
        hours = self.stale_hours
        return hours is not None and hours > STALE_AFTER_HOURS

    @property
    def healthy(self) -> bool:
        return self.last_error is None and self.count > 0 and not self.stale

    def to_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "healthy": self.healthy,
            "stale": self.stale,
            "stale_hours": round(self.stale_hours, 1) if self.stale_hours is not None else None,
        }
