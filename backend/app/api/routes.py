"""API 계약 — 화면이 의존하는 유일한 표면.

  GET  /healthz       헬스체크 (ALB 타깃 그룹이 때린다)
  GET  /api/articles  매체별 최신 기사 + 매체 상태
  POST /api/lens      Bedrock 클러스터링 (10분 버킷 캐시)
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query

from ..config import FEEDS, settings
from ..llm import LensError, build_lens
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
        },
    }


@router.post("/api/lens")
async def lens(refresh: bool = Query(default=False)) -> dict[str, object]:
    """관점 비교. 같은 10분 버킷 안에서는 캐시를 돌려주고 Bedrock 을 부르지 않는다."""
    try:
        return await build_lens(force_refresh=refresh)
    except LensError as exc:
        # 502: 우리 잘못이 아니라 상위 의존(Bedrock)이 답을 못 준 것.
        raise HTTPException(status_code=502, detail=str(exc)) from exc
