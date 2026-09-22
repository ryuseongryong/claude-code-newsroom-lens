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
        url="https://www3.nhk.or.jp/nhkworld/data/en/news/all.json",
        kind="nhk_json",
        # 2026-09-22 실측: HTTP 200, data 405건.
        verified="2026-09-22",
        # 이 한 줄이 이 프로젝트에서 가장 중요한 주석이다.
        note=(
            "NHK World 는 RSS 를 내리고 JSON 으로 옮겼다. 2026-09-22 확인 결과 "
            "en/news/rss/all.xml·rss.xml·feed.xml·atom.xml 전부 404 이고, "
            "/nhkworld/en/news/ 는 /nhkworld/news/ 로 meta-refresh 된다. "
            "공식 프런트엔드가 읽는 이 JSON 엔드포인트가 현재 유일한 정식 경로라 "
            "kind='nhk_json' 어댑터를 따로 둔다. robots.txt 의 User-agent:* 는 "
            "/*/r/ 만 막으므로 이 경로는 허용 범위다."
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
        note="4개 매체 중 유일한 한국어 원문 소스.",
    ),
)

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
    # 수집
    poll_interval_seconds: int = field(default_factory=lambda: _int_env("POLL_INTERVAL_SECONDS", 120))
    max_articles_per_source: int = field(default_factory=lambda: _int_env("MAX_ARTICLES_PER_SOURCE", 50))
    summary_max_chars: int = field(default_factory=lambda: _int_env("SUMMARY_MAX_CHARS", 300))
    fetch_timeout_seconds: float = 20.0
    user_agent: str = "NewsroomLens/1.0 (+https://github.com/; RSS reader)"

    # Bedrock (REST 직접 호출 — SDK 를 쓰지 않는다)
    bedrock_region: str = field(default_factory=lambda: os.getenv("BEDROCK_REGION", "ap-northeast-2"))
    bedrock_model_id: str = field(
        default_factory=lambda: os.getenv("BEDROCK_MODEL_ID", "global.anthropic.claude-sonnet-4-6")
    )
    # 스펙 초안은 1500 이었다. 실측으로 못 쓴다: 한국어 5개 클러스터 × (요약 2문장 +
    # 프레임 4개) 출력이 2,233 토큰이었고, 1500 에서는 stopReason=max_tokens 로 JSON 이
    # 중간에 잘려 파싱 자체가 불가능했다(2026-09-22 측정). 4000 은 실측값에 약 1.8배
    # 여유를 둔 값이다. 무제한이 아니라 '측정된 상한' 이라는 점이 중요하다.
    bedrock_max_tokens: int = field(default_factory=lambda: _int_env("BEDROCK_MAX_TOKENS", 4000))
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
