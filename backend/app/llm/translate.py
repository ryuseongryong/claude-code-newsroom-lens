"""제목 이중 언어화 — 같은 헤드라인을 한국어와 영어로 나란히 본다.

왜 서버에서 하는가: 번역은 링크당 한 번만 하면 영구히 유효하다(제목은 바뀌지 않는다).
클라이언트에서 하면 방문자마다 같은 제목을 다시 번역하게 된다. 그래서 링크를 키로
프로세스 메모리에 쌓고, 캐시에 없는 것만 모델에 넘긴다.

원문은 절대 버리지 않는다. 번역은 원문에 '덧붙는' 값이고, 화면에서도 원문을 같이 보여
준다 — 번역된 제목만 남으면 독자가 원문을 검증할 수 없다.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from ..config import FEEDS_BY_KEY
from ..store.models import Article
from .bedrock import LensError, converse, parse_json_object

log = logging.getLogger(__name__)

# link → {"ko": ..., "en": ...}. 제목은 불변이므로 만료가 없다.
_cache: dict[str, dict[str, str]] = {}
_lock = asyncio.Lock()

# 한 번에 모델에 넘길 제목 수.
#
# 40으로 두면 안 된다 — 토큰 한계 때문이 아니라 '시간' 때문이다. 40개 묶음 한 번이
# 약 60초 걸리는데, CloudFront 의 오리진 read timeout 이 60초다. 실측으로 100건
# 요청이 64초가 나와 배포 환경에서 504 가 됐다. 20개면 묶음당 약 20초다.
BATCH_SIZE = 20

# 동시에 띄울 Bedrock 호출 수. 묶음을 작게 쪼갠 만큼 묶음 수가 늘어나므로 상한이
# 필요하다 — 제한 없이 병렬로 던지면 스로틀링(429)으로 오히려 전체가 느려진다.
# 3이면 100건(5묶음)이 두 물결에 끝나 약 40초다.
MAX_CONCURRENCY = 3
_semaphore = asyncio.Semaphore(MAX_CONCURRENCY)

# 분할 재시도 깊이. 20 → 10 → 5 → 3 → 2 → 1 이므로 5면 한 건까지 좁혀진다.
MAX_SPLIT_DEPTH = 5

SYSTEM_PROMPT = """당신은 국제 뉴스 헤드라인을 번역하는 통신사 번역 데스크다.

입력은 번호가 붙은 헤드라인 목록이고, 각 줄은 어떤 언어로 옮겨야 하는지를 함께 알려준다.
요청된 언어의 제목 하나씩만 만든다.

규칙:
1. JSON 객체 하나만 출력한다. 코드펜스(```), 설명, 인사말을 절대 붙이지 않는다.
2. 입력에 있는 모든 번호를 빠뜨리지 않고 출력한다.
3. 제목 안에 인용부호가 필요하면 반드시 둥근 따옴표만 쓴다: ‘ ’ 또는 “ ”.
   ASCII 큰따옴표(")와 아포스트로피(')를 제목 텍스트에 절대 쓰지 마라 — JSON 문자열이 깨진다.
4. 뉴스 헤드라인의 문체를 지킨다. 완전한 문장으로 늘리지 말고, 원문의 간결함을 유지한다.
   한국어는 신문 제목처럼 조사를 아끼고 명사로 끊는다.
5. 고유명사는 해당 언어의 통용 표기를 쓴다 (Zelensky↔젤렌스키, Dujuan↔두쥐안,
   Yoon Suk-yeol↔윤석열). 확실하지 않으면 원어를 괄호로 병기한다.
6. 의미를 보태지 않는다. 원문에 없는 해석·수식어·감정을 넣지 않는다.

출력 스키마:
{"titles": [{"i": 0, "text": "요청된 언어의 제목"}]}"""

# 평문 폴백 프롬프트. JSON 을 아예 쓰지 않으므로 이스케이프 문제가 원리적으로 없다.
PLAIN_SYSTEM_PROMPT = """당신은 뉴스 헤드라인 번역가다.

헤드라인 하나와 목표 언어를 받으면, 번역된 제목 한 줄만 출력한다.
설명·인용부호·따옴표·접두사를 붙이지 않는다. 오직 제목 한 줄이다."""


def _target_lang(art: Article) -> str:
    """이 기사에 대해 '모델이 만들어야 하는' 언어. 원문 언어 쪽은 부탁하지 않는다."""
    spec = FEEDS_BY_KEY.get(art.source)
    return "en" if (spec and spec.lang == "ko") else "ko"


def _known_pair(art: Article, translated: str | None) -> dict[str, str]:
    """원문 언어 쪽은 원문 그대로, 반대쪽은 번역으로 채운다."""
    if _target_lang(art) == "en":
        return {"ko": art.title, "en": translated or art.title}
    return {"ko": translated or art.title, "en": art.title}


_LANG_WORD = {"ko": "한국어", "en": "영어"}


def _render_batch(items: list[tuple[int, Article]]) -> str:
    lines = []
    for i, art in items:
        want = _target_lang(art)
        lines.append(f"[{i}] → {_LANG_WORD[want]}로: {art.title}")
    return (
        "아래 헤드라인을 각각 지정된 언어의 제목으로 옮겨라. "
        "요청된 언어 하나만 만든다.\n\n" + "\n".join(lines)
    )


async def _translate_batch(
    items: list[tuple[int, Article]], depth: int = 0
) -> dict[str, dict[str, str]]:
    """한 묶음을 번역한다. 실패하면 반으로 쪼개서 다시 시도한다.

    쪼개는 이유: 실패는 대부분 묶음 전체의 문제가 아니라 제목 한두 개가 모델에게
    JSON 을 깨뜨리게 만드는 문제다(따옴표 등). 묶음째로 버리면 멀쩡한 39개까지 잃는다.
    반씩 좁혀 들어가면 최악의 경우에도 문제가 되는 제목 하나만 원문으로 남는다.
    """
    by_index = {i: art for i, art in items}
    try:
        async with _semaphore:
            raw = await converse(SYSTEM_PROMPT, _render_batch(items))
        parsed = parse_json_object(raw)
    except LensError as exc:
        if len(items) > 1 and depth < MAX_SPLIT_DEPTH:
            mid = len(items) // 2
            log.info("제목 번역 묶음(%d건) 실패 → %d/%d 로 분할 재시도", len(items), mid, len(items) - mid)
            halves = await asyncio.gather(
                _translate_batch(items[:mid], depth + 1),
                _translate_batch(items[mid:], depth + 1),
                return_exceptions=True,
            )
            merged: dict[str, dict[str, str]] = {}
            for half in halves:
                if isinstance(half, dict):
                    merged.update(half)
            return merged

        # 1건까지 내려왔는데도 JSON 이 깨진다 → JSON 을 포기하고 평문으로 받는다.
        log.info("제목 1건 JSON 실패 → 평문 폴백: %s", exc)
        return await _translate_plain(items[0][1])

    out: dict[str, dict[str, str]] = {}
    for entry in parsed.get("titles") or []:
        if not isinstance(entry, dict):
            continue
        try:
            idx = int(entry.get("i"))
        except (TypeError, ValueError):
            continue
        art = by_index.get(idx)
        if art is None:
            continue  # 모델이 없는 번호를 만들어냈다
        text = str(entry.get("text") or "").strip()
        if not text:
            continue
        out[art.link] = _known_pair(art, text)
    return out


async def _translate_plain(art: Article) -> dict[str, dict[str, str]]:
    """JSON 없이 한 줄만 받는다 — 이스케이프가 원리적으로 불가능한 경로.

    필요한 이유: 연합뉴스 제목에는 ASCII 큰따옴표가 흔하다(예: IMF "…43%…"). 이걸
    JSON 문자열 안에 넣으라고 하면 모델이 이스케이프를 빠뜨려 문서가 깨진다 — 제목
    하나만 넘겨도 똑같이 깨지므로 분할 재시도로는 절대 복구되지 않는다.
    """
    want = _target_lang(art)
    try:
        async with _semaphore:
            raw = await converse(
                PLAIN_SYSTEM_PROMPT,
                f"목표 언어: {_LANG_WORD[want]}\n헤드라인: {art.title}",
            )
    except LensError as exc:
        log.warning("평문 폴백도 실패: %s", exc)
        return {}

    # 첫 비어 있지 않은 줄만 쓴다. 모델이 설명을 덧붙여도 제목은 맨 앞에 온다.
    line = next((ln.strip() for ln in raw.splitlines() if ln.strip()), "")
    line = line.strip('"').strip("'").strip()
    if not line:
        return {}
    return {art.link: _known_pair(art, line)}


async def ensure_titles(
    articles: list[Article],
) -> tuple[dict[str, dict[str, str]], list[str]]:
    """주어진 기사들의 이중 언어 제목을 돌려준다. 캐시에 없는 것만 번역한다.

    반환값은 (제목맵, 번역실패한_링크들) 이다. 요청한 기사에 해당하는 부분만 담는다 —
    캐시 전체를 내보내면 응답이 시간이 갈수록 계속 커진다.
    """
    unique: dict[str, Article] = {}
    for art in articles:
        if art.link not in unique:
            unique[art.link] = art

    missing = [art for link, art in unique.items() if link not in _cache]

    if missing:
        # 동시 요청이 같은 제목을 두 번 번역하지 않게 잠근다.
        async with _lock:
            missing = [art for art in missing if art.link not in _cache]
            if missing:
                batches = [
                    list(enumerate(missing[i : i + BATCH_SIZE]))
                    for i in range(0, len(missing), BATCH_SIZE)
                ]
                results = await asyncio.gather(
                    *(_translate_batch(b) for b in batches), return_exceptions=True
                )
                for result in results:
                    if isinstance(result, dict):
                        _cache.update(result)
                    else:
                        log.warning("제목 번역 묶음에서 예상 못 한 오류: %s", result)

    # 번역이 실패한 제목은 원문으로 채운다. 화면에 구멍을 내지 않는다. 다만 몇 건이
    # 원문 폴백인지는 함께 알린다 — 조용히 원문을 내보내면 호출자가 번역이 됐다고
    # 착각한다(실제로 그렇게 HTTP 200 뒤에 실패가 숨어 있었다).
    out: dict[str, dict[str, str]] = {}
    failed: list[str] = []
    for link, art in unique.items():
        pair = _cache.get(link)
        if pair is None:
            failed.append(link)
            pair = {"ko": art.title, "en": art.title}
        out[link] = pair
    if failed:
        log.warning("제목 %d건은 원문으로 폴백했습니다", len(failed))
    return out, failed


def cached_count() -> int:
    return len(_cache)


def reset_cache() -> None:
    _cache.clear()
