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
    # 발행 시각에 타임존 표기가 없는 피드를 위한 보정(시간 단위).
    # 국내 업계지들은 '2026-09-22 15:09:04' 처럼 KST 를 타임존 없이 내보내는데,
    # feedparser 는 표기 없는 시각을 UTC 로 읽는다. 그러면 기사가 9시간 미래로
    # 밀려 전부 '방금' 으로 보이고, 정체 판정(stale)도 영원히 통과한다.
    # 0 이 아닌 값을 주면 normalize 가 그만큼 빼서 실제 UTC 로 맞춘다.
    assume_tz_offset_hours: float = 0.0
    # 이 피드가 화장품 외 분야(제약·바이오 등)를 함께 싣는가.
    # True 면 주제 키워드 필터를 적용한다. 실측: 코스인코리아 주제 적합도 68%.
    needs_topic_filter: bool = False


FEEDS: tuple[FeedSpec, ...] = (
    FeedSpec(
        key="wwd",
        label="WWD 뷰티",
        label_en="WWD Beauty",
        lang="en",
        url="https://wwd.com/beauty-industry-news/feed/",
        kind="rss",
        # 2026-09-22 실측: HTTP 200, <item> 10개, 최신 3.5시간 전, 주제 적합 8/10.
        verified="2026-09-22",
        note=(
            "미국 패션·뷰티 업계지. 브랜드 전략·마케팅·인물 중심으로 쓴다. "
            "pubDate 에 +0000 을 제대로 붙이는 유일한 소스다."
        ),
    ),
    FeedSpec(
        key="pbn",
        label="프리미엄 뷰티 뉴스",
        label_en="Premium Beauty News",
        lang="en",
        url="https://www.premiumbeautynews.com/spip.php?page=backend&lang=en",
        kind="rss",
        # 2026-09-22 실측: HTTP 200, <item> 10개, 최신 2.4시간 전, 주제 적합 18/18.
        verified="2026-09-22",
        note=(
            "유럽(프랑스) 뷰티 산업지의 영어판. 원료·용기·패키징·설비 등 공급망 쪽을 "
            "가장 깊게 다뤄 브랜드 중심 매체와 프레임이 뚜렷이 갈린다.\n"
            "URL 에 lang=en 이 필수다. 이걸 빼면 프랑스어 기사가 나오고(실측 확인), "
            "/en/spip.php?page=backend 경로는 404 다."
        ),
    ),
    FeedSpec(
        key="jangup",
        label="장업신문",
        label_en="Jangup Shinmun",
        lang="ko",
        url="https://www.jangup.com/rss/allArticle.xml",
        kind="rss",
        # 2026-09-22 실측: HTTP 200, <item> 50개, 주제 적합 41/50.
        verified="2026-09-22",
        assume_tz_offset_hours=9.0,
        note="국내 화장품 업계 전문지. <pubDate>2026-09-22 13:07:33</pubDate> — 타임존 표기 없는 KST.",
    ),
    FeedSpec(
        key="cosmorning",
        label="코스모닝",
        label_en="Cosmorning",
        lang="ko",
        url="https://www.cosmorning.com/data/rss/news.xml",
        kind="rss",
        # 2026-09-22 실측: HTTP 200, <item> 50개, 주제 적합 35/50.
        verified="2026-09-22",
        assume_tz_offset_hours=9.0,
        needs_topic_filter=True,
        note="국내 화장품 전문지. <dc:date> 에 타임존 표기가 없다(KST).",
    ),
    FeedSpec(
        key="cosin",
        label="코스인코리아",
        label_en="COS'IN Korea",
        lang="ko",
        url="https://www.cosinkorea.com/data/rss/news.xml",
        kind="rss",
        # 2026-09-22 실측: HTTP 200, <item> 25개, 주제 적합 17/25 (68%).
        verified="2026-09-22",
        assume_tz_offset_hours=9.0,
        needs_topic_filter=True,
        note=(
            "국내 화장품 전문지. 제약·바이오·헬스케어 기사를 함께 실어 주제 적합도가 "
            "68% 였다(실측 최상단 기사가 'KOREA LIFE SCIENCE WEEK'). "
            "그래서 needs_topic_filter=True — 소스 선택만으로는 '화장품 뉴스만' 을 보장하지 못한다."
        ),
    ),
)

# ── 주제 필터 ─────────────────────────────────────────────────────────────────
#
# needs_topic_filter=True 인 피드에만 적용한다. 제목+요약에 아래 낱말이 하나도 없으면
# 화장품 기사로 보지 않고 버린다.
#
# 이 목록은 '완벽한 분류기' 가 아니다. 낱말 기반이라 놓치는 기사가 반드시 생긴다
# (예: 회사명만 있고 업종 낱말이 없는 실적 기사). 그래서 두 가지를 지킨다.
#   1) 브랜드·기업명을 넉넉히 넣어 회수율을 끌어올린다.
#   2) 걸러낸 건수를 SourceStatus.filtered_out 에 남겨 화면에 보여준다.
#      조용히 버리면 소스가 죽은 것과 필터가 과하게 먹은 것을 구별할 수 없다.
TOPIC_KEYWORDS: tuple[str, ...] = (
    # 분야
    "화장품", "코스메틱", "코스메", "뷰티", "미용", "스킨케어", "메이크업", "색조",
    "기초화장", "향수", "프래그런스", "네일", "헤어", "모발", "두피", "바디",
    "선케어", "자외선차단", "마스크팩", "더마", "뷰티테크", "이너뷰티",
    # 산업 구조
    "ODM", "OEM", "원료", "용기", "패키징", "부자재", "위탁생산", "책임판매",
    "로드숍", "편집숍", "면세", "역직구", "K뷰티", "K-뷰티",
    # 국내 기업·브랜드
    "아모레", "LG생활건강", "엘지생활건강", "코스맥스", "한국콜마", "콜마", "씨앤씨",
    "클리오", "애경", "토니모리", "닥터자르트", "이니스프리", "에뛰드", "설화수",
    "미샤", "에이블씨엔씨", "네이처리퍼블릭", "잇츠한불", "현대바이오랜드", "씨티케이",
    "코스메카", "연우", "펌텍", "선진뷰티",
    # 규제·인증
    "식약처", "기능성화장품", "CPNP", "MoCRA", "ISO22716", "비건 인증",
    # 영어
    "cosmetic", "beauty", "skincare", "skin care", "makeup", "make-up", "fragrance",
    "perfume", "haircare", "hair care", "personal care", "k-beauty", "dermocosmetic",
    "l'oreal", "loreal", "estee lauder", "shiseido", "unilever", "coty", "sephora",
    "ulta", "amorepacific", "cosmax", "kolmar",
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
