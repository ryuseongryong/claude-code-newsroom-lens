"""API 계약 — 화면이 의존하는 유일한 표면.

  GET  /healthz       헬스체크 (ALB 타깃 그룹이 때린다)
  GET  /api/articles  매체별 최신 기사 + 매체 상태
  POST /api/lens      Bedrock 클러스터링 (10분 버킷 캐시)
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query

from ..collector import request_refresh, seconds_until_refresh_allowed
from ..config import FEEDS, settings
from ..llm import LensError, build_lens, ensure_titles
from ..llm.translate import cached_count
from ..store import store

log = logging.getLogger(__name__)
router = APIRouter()


@router.get("/healthz")
async def healthz() -> dict[str, object]:
    """가볍고 의존성이 없어야 한다. 피드가 죽어도 200 을 준다 —
    태스크를 재활용해도 RSS 는 여전히 죽어 있고, 재시작은 해결책이 아니다."""
    return {
        "status": "ok",
        "sources": len(FEEDS),
        "articles": store.total_count(),
        "model": settings.bedrock_model_id,
        "bedrock_token": bool(settings.bedrock_bearer_token),
    }


@router.get("/api/articles")
async def articles(limit: int = Query(default=20, ge=1, le=50)) -> dict[str, object]:
    snapshot = store.snapshot(limit)
    return {
        "sources": [
            {
                **status.to_dict(),
                "articles": [a.to_dict() for a in snapshot.get(status.source, [])],
            }
            for status in store.statuses()
        ],
        "meta": {
            "poll_interval_seconds": settings.poll_interval_seconds,
            "max_articles_per_source": settings.max_articles_per_source,
            "manual_refresh_min_seconds": settings.manual_refresh_min_seconds,
            "refresh_available_in": seconds_until_refresh_allowed(),
        },
    }


@router.post("/api/refresh")
async def refresh() -> dict[str, object]:
    """'지금 새로 고침' — 자동 주기를 기다리지 않고 즉시 수집한다.

    최소 간격(기본 60초) 안이면 긁지 않고 남은 시간만 알려준다. 거부도 200 이다 —
    사용자 잘못이 아니고, 화면의 기사는 이미 최신이다.
    """
    return await request_refresh()


@router.post("/api/lens")
async def lens(refresh: bool = Query(default=False)) -> dict[str, object]:
    """관점 비교. 같은 10분 버킷 안에서는 캐시를 돌려주고 Bedrock 을 부르지 않는다."""
    try:
        return await build_lens(force_refresh=refresh)
    except LensError as exc:
        # 502: 우리 잘못이 아니라 상위 의존(Bedrock)이 답을 못 준 것.
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/api/titles")
async def titles(limit: int = Query(default=20, ge=1, le=50)) -> dict[str, object]:
    """제목의 한국어·영어 두 판본. 링크를 키로 영구 캐시한다(제목은 바뀌지 않는다).

    /api/articles 와 같은 limit 을 받아 화면에 실제로 보이는 제목만 번역한다 —
    저장소에 쌓인 250건을 전부 번역하면 값을 치를 이유가 없다.
    """
    articles = [a for arts in store.snapshot(limit).values() for a in arts]
    try:
        titles_map, failed = await ensure_titles(articles)
    except LensError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {
        "titles": titles_map,
        "requested": len(titles_map),
        # 원문으로 폴백한 건수를 숨기지 않는다. 화면이 '일부는 원문' 이라고 말할 수 있어야 한다.
        "failed": len(failed),
        "cached_total": cached_count(),
    }
