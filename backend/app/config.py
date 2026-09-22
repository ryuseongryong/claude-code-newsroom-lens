"""Newsroom Lens 설정 — 피드 정의와 런타임 설정.

피드 URL 은 코드에 박힌 상수가 아니라 '검증 기록'이다. 아래 각 항목의
`verified` 는 실제로 curl 을 때려 200 + item 개수를 확인한 날짜다. 피드는 조용히
죽는다(도메인 이전, 경로 변경). 날짜가 오래되었으면 다시 확인하라.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Literal

# ── 피드 정의 ─────────────────────────────────────────────────────────────────

FeedKind = Literal["rss", "nhk_json"]


@dataclass(frozen=True)
class FeedSpec:
    """수집 대상 한 곳. `key` 는 API·UI·LLM 프롬프트 전체에서 쓰이는 식별자다."""

    key: str
    label: str          # 화면에 보일 매체명 (한국어)
    label_en: str       # 원문 표기
    lang: str           # 기사 원문 언어
    url: str
    kind: FeedKind
    verified: str       # 이 URL 을 마지막으로 실측 확인한 날짜 (UTC)
    note: str = ""


FEEDS: tuple[FeedSpec, ...] = (
    FeedSpec(
        key="bbc",
        label="BBC 월드",
        label_en="BBC World",
        lang="en",
        url="https://feeds.bbci.co.uk/news/world/rss.xml",
        kind="rss",
        # 2026-09-22 실측: HTTP 200, <item> 32개.
        verified="2026-09-22",
        note="BBC 공식 RSS 허브(bbc.co.uk/news/10628494)에 등재된 World 피드.",
    ),
    FeedSpec(
        key="guardian",
        label="가디언 월드",
        label_en="The Guardian World",
        lang="en",
        url="https://www.theguardian.com/world/rss",
        kind="rss",
        # 2026-09-22 실측: HTTP 200, <item> 45개.
        verified="2026-09-22",
        note="가디언은 모든 섹션 URL 에 /rss 를 붙이면 피드가 되는 규칙을 쓴다.",
    ),
    FeedSpec(
        key="nhk",
        label="NHK 월드",
        label_en="NHK WORLD-JAPAN",
        lang="en",
        url="https://www3.nhk.or.jp/nhkworld/data/news/all.json",
        kind="nhk_json",
        # 2026-09-22 실측: HTTP 200, data 574건, 최신 기사 당일(20260922) 6건.
        verified="2026-09-22",
        # 이 주석이 이 프로젝트에서 가장 중요하다. 두 번 틀렸던 자리다.
        note=(
            "NHK World 는 RSS 를 내렸다 (en/news/rss/all.xml·rss.xml·feed.xml·atom.xml "
            "전부 404). 게다가 JSON 경로도 한 번 바뀌었다.\n"
            "  낡은 경로: /nhkworld/data/en/news/all.json  ← 쓰지 말 것\n"
            "  현재 경로: /nhkworld/data/news/all.json\n"
            "낡은 경로는 지금도 HTTP 200 에 405건을 정상으로 돌려주지만 2026-09-05 에 "
            "얼어붙었다 — 죽은 게 아니라 '갱신이 멈춘' 인덱스라서 헬스체크로는 절대 "
            "안 잡힌다. 실제로 이 때문에 화면에서 NHK 만 17일 전 기사를 보여주고 있었고, "
            "렌즈는 그걸 정직하게 '미보도' 로 표시했다(우리 버그가 아니라 소스 문제였다). "
            "기사 URL 체계도 /nhkworld/en/news/20260905_100/ 에서 "
            "/nhkworld/news/20260919de51188/ 로 바뀌었다. 필드 구조는 같아서 "
            "kind='nhk_json' 어댑터는 그대로 쓴다.\n"
            "robots.txt 의 User-agent:* 는 /*/r/ 만 막으므로 이 경로는 허용 범위다."
        ),
    ),
    FeedSpec(
        key="yna",
        label="연합뉴스 국제",
        label_en="Yonhap News International",
        lang="ko",
        url="https://www.yna.co.kr/rss/international.xml",
        kind="rss",
        # 2026-09-22 실측: HTTP 200, <item> 120개.
        verified="2026-09-22",
        note="유일한 한국어 원문 소스.",
    ),
    FeedSpec(
        key="aljazeera",
        label="알자지라",
        label_en="Al Jazeera English",
        lang="en",
        url="https://www.aljazeera.com/xml/rss/all.xml",
        kind="rss",
        # 2026-09-22 실측: HTTP 200, <item> 25개.
        verified="2026-09-22",
        note=(
            "5번째 소스. 서구 3사(BBC·가디언·알자지라 중 앞의 둘)와 다른 관점을 넣기 위해 "
            "추가했다. robots.txt 의 User-agent:* 는 /api 등만 막고 /xml/rss/ 는 허용한다."
        ),
    ),
)

# 매체 수를 코드에 박지 않는다. 프롬프트·검증·UI 가 모두 이 값을 따라간다 —
# 6번째 소스를 넣을 때 FEEDS 한 곳만 고치면 되도록.
SOURCE_COUNT = len(FEEDS)

FEEDS_BY_KEY: dict[str, FeedSpec] = {f.key: f for f in FEEDS}


# ── 런타임 설정 ───────────────────────────────────────────────────────────────


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass
class Settings:
    # 수집: 기본 5분 주기. RSS 는 그보다 자주 바뀌지 않고, 매체 5곳을 2분마다 긁으면
    # 얻는 것 없이 상대 서버에 부담만 준다.
    poll_interval_seconds: int = field(default_factory=lambda: _int_env("POLL_INTERVAL_SECONDS", 300))

    # '지금 새로 고침' 의 최소 간격. 자동 주기와 무관하게 이 시간 안에는 다시 긁지 않는다.
    # 버튼을 연타해도 외부 피드로 요청이 쏟아지지 않게 하는 안전장치다. 클라이언트가
    # 아니라 서버에 둔다 — 브라우저 쪽 제한은 새로 고침 한 번으로 사라진다.
    manual_refresh_min_seconds: int = field(
        default_factory=lambda: _int_env("MANUAL_REFRESH_MIN_SECONDS", 60)
    )
    max_articles_per_source: int = field(default_factory=lambda: _int_env("MAX_ARTICLES_PER_SOURCE", 50))
    summary_max_chars: int = field(default_factory=lambda: _int_env("SUMMARY_MAX_CHARS", 300))
    fetch_timeout_seconds: float = 20.0
    user_agent: str = "NewsroomLens/1.0 (+https://github.com/; RSS reader)"

    # Bedrock (REST 직접 호출 — SDK 를 쓰지 않는다)
    bedrock_region: str = field(default_factory=lambda: os.getenv("BEDROCK_REGION", "ap-northeast-2"))
    bedrock_model_id: str = field(
        default_factory=lambda: os.getenv("BEDROCK_MODEL_ID", "global.anthropic.claude-sonnet-4-6")
    )
    # 스펙 초안은 1500 이었다. 실측으로 못 쓴다 (전부 2026-09-22 측정):
    #   매체 4곳, tone 없음  → 출력 2,233 토큰
    #   매체 5곳, tone 포함  → 출력 2,895 토큰  ← 현재 구성
    # 1500 에서는 stopReason=max_tokens 로 JSON 이 중간에 잘려 파싱 자체가 불가능했다.
    # 5000 은 현재 실측값에 약 1.7배 여유를 둔 값이다. 무제한이 아니라 '측정된 상한'
    # 이라는 점이 중요하다. 6번째 소스를 넣으면 다시 재고 이 주석을 갱신할 것.
    bedrock_max_tokens: int = field(default_factory=lambda: _int_env("BEDROCK_MAX_TOKENS", 5000))
    bedrock_timeout_seconds: float = 90.0

    # Lens 캐시: 10분 버킷. 같은 버킷 안에서는 Bedrock 을 다시 부르지 않는다.
    lens_cache_bucket_seconds: int = field(default_factory=lambda: _int_env("LENS_CACHE_BUCKET_SECONDS", 600))
    # 매체별로 LLM 에 넘길 최신 기사 수.
    #
    # 12로 두면 안 된다. 매체마다 발행 속도가 다르기 때문이다 — 연합뉴스는 피드에
    # 120건을 싣고 하루에 그만큼 쏟아내는데, BBC World 는 32건이다. 같은 '최신 12건'
    # 을 잘라오면 연합뉴스 창은 약 1시간치(그것도 국내 이슈 위주)이고 BBC 창은 하루치가
    # 되어, 겹치는 사건이 구조적으로 사라진다. 그러면 유일한 한국어 매체가 늘 "미보도"
    # 로 밀려나고, 이때의 "미보도" 는 침묵이 아니라 우리 창 크기가 만든 거짓말이다.
    # 25건이면 연합뉴스 창이 반나절 수준으로 늘어나 교집합이 실제로 잡힌다.
    # 비용: 입력 약 12k 토큰/호출, 10분에 1회 상한이므로 감당 가능하다.
    lens_headlines_per_source: int = field(default_factory=lambda: _int_env("LENS_HEADLINES_PER_SOURCE", 25))

    @property
    def bedrock_converse_url(self) -> str:
        return (
            f"https://bedrock-runtime.{self.bedrock_region}.amazonaws.com"
            f"/model/{self.bedrock_model_id}/converse"
        )

    @property
    def bedrock_bearer_token(self) -> str | None:
        """이미지에 굽지 않는다. 런타임 환경변수(Secrets 주입)에서만 읽는다."""
        return os.getenv("AWS_BEARER_TOKEN_BEDROCK") or None


settings = Settings()
