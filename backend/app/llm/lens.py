"""Lens 조립 — 헤드라인을 넣고, 클러스터를 받고, 계약대로 검증해서 내보낸다.

LLM 출력을 그대로 믿지 않는다. 프롬프트는 요청이고 검증은 보증이다.
  - 없는 인덱스를 인용했으면 버린다 (근거 없는 주장은 화면에 올리지 않는다)
  - 2개 매체 미만 클러스터는 버린다 (비교가 성립하지 않는다)
  - 누락된 매체는 "미보도" 로 채운다 (침묵도 정보다)
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any

from ..config import FEEDS, settings
from ..store import ArticleStore, store
from ..store.models import Article
from .bedrock import LensError, converse, parse_json_object
from .prompts import SYSTEM_PROMPT, render_input

log = logging.getLogger(__name__)

NO_COVERAGE = "미보도"
MIN_SOURCES_PER_CLUSTER = 2
MIN_CLUSTERS, MAX_CLUSTERS = 1, 5

# 10분 버킷 캐시. {bucket: payload}. 버킷이 바뀌면 통째로 버린다.
_cache: dict[int, dict[str, Any]] = {}
# 동시에 여러 요청이 들어와도 Bedrock 은 한 번만 부른다.
_lock = asyncio.Lock()


def current_bucket(now: float | None = None) -> int:
    return int((now if now is not None else time.time()) // settings.lens_cache_bucket_seconds)


async def build_lens(force_refresh: bool = False, target: ArticleStore = store) -> dict[str, Any]:
    bucket = current_bucket()

    if not force_refresh and bucket in _cache:
        return {**_cache[bucket], "cached": True}

    async with _lock:
        # 락을 기다리는 동안 다른 요청이 이미 채웠을 수 있다. 다시 확인한다.
        if not force_refresh and bucket in _cache:
            return {**_cache[bucket], "cached": True}

        indexed = {
            spec.key: target.latest(spec.key, settings.lens_headlines_per_source) for spec in FEEDS
        }
        live_sources = [k for k, v in indexed.items() if v]
        if len(live_sources) < MIN_SOURCES_PER_CLUSTER:
            raise LensError(
                f"비교에는 최소 {MIN_SOURCES_PER_CLUSTER}개 매체의 기사가 필요합니다 "
                f"(현재 {len(live_sources)}곳). 잠시 후 다시 시도하세요."
            )

        raw = await converse(SYSTEM_PROMPT, render_input(indexed))
        parsed = parse_json_object(raw)
        payload = _shape(parsed, indexed, bucket)

        _cache.clear()          # 버킷은 하나만 들고 있으면 된다.
        _cache[bucket] = payload
        return {**payload, "cached": False}


def _shape(parsed: dict[str, Any], indexed: dict[str, list[Article]], bucket: int) -> dict[str, Any]:
    clusters_raw = parsed.get("clusters")
    if not isinstance(clusters_raw, list) or not clusters_raw:
        raise LensError("모델이 clusters 배열을 주지 않았습니다.")

    clusters: list[dict[str, Any]] = []
    for item in clusters_raw:
        cluster = _shape_cluster(item, indexed)
        if cluster is not None:
            clusters.append(cluster)
        if len(clusters) >= MAX_CLUSTERS:
            break

    if len(clusters) < MIN_CLUSTERS:
        raise LensError(
            f"{MIN_SOURCES_PER_CLUSTER}개 이상 매체가 함께 다룬 주제를 찾지 못했습니다. "
            "기사가 더 모인 뒤 다시 시도하세요."
        )

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "bucket": bucket,
        "bucket_seconds": settings.lens_cache_bucket_seconds,
        "model": settings.bedrock_model_id,
        "overview": str(parsed.get("overview") or "").strip(),
        "clusters": clusters,
        # 프런트가 근거 칩([bbc:3])을 실제 링크로 되돌리는 데 쓰는 색인.
        "inputs": {
            key: [
                {"index": i, "title": a.title, "link": a.link, "published": a.published}
                for i, a in enumerate(articles)
            ]
            for key, articles in indexed.items()
        },
    }


def _shape_cluster(item: Any, indexed: dict[str, list[Article]]) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None

    frames_raw = item.get("frames") if isinstance(item.get("frames"), dict) else {}
    sources_raw = item.get("sources") if isinstance(item.get("sources"), dict) else {}

    frames: dict[str, str] = {}
    sources: dict[str, list[int]] = {}
    evidence: dict[str, list[dict[str, Any]]] = {}
    covered: list[str] = []

    for spec in FEEDS:
        key = spec.key
        available = indexed.get(key, [])
        indices = _valid_indices(sources_raw.get(key), len(available))
        frame = str(frames_raw.get(key) or "").strip()

        # 인용할 기사가 없으면 무슨 말을 했든 "미보도" 다. 근거 없는 프레임은 싣지 않는다.
        if not indices:
            frames[key] = NO_COVERAGE
            sources[key] = []
            evidence[key] = []
            continue

        if not frame or frame == NO_COVERAGE:
            # 인덱스는 줬는데 프레임을 안 썼다 — 인용만 남기고 프레임은 비운다.
            frames[key] = NO_COVERAGE
            sources[key] = []
            evidence[key] = []
            continue

        frames[key] = frame
        sources[key] = indices
        evidence[key] = [
            {"index": i, "title": available[i].title, "link": available[i].link} for i in indices
        ]
        covered.append(key)

    if len(covered) < MIN_SOURCES_PER_CLUSTER:
        log.info("클러스터 폐기: 매체 %d곳만 인용됨 (topic=%r)", len(covered), item.get("topic"))
        return None

    topic = str(item.get("topic") or "").strip()
    if not topic:
        return None

    return {
        "topic": topic,
        "summary": str(item.get("summary") or "").strip(),
        "frames": frames,
        "sources": sources,
        "evidence": evidence,
        "covered": covered,
        "uncovered": [s.key for s in FEEDS if s.key not in covered],
    }


def _valid_indices(raw: Any, bound: int) -> list[int]:
    """모델이 준 인덱스 중 실제로 존재하는 것만 남긴다. 순서 유지, 중복 제거."""
    if not isinstance(raw, list):
        return []
    seen: set[int] = set()
    out: list[int] = []
    for value in raw:
        if isinstance(value, bool):
            continue
        try:
            idx = int(value)
        except (TypeError, ValueError):
            continue
        if 0 <= idx < bound and idx not in seen:
            seen.add(idx)
            out.append(idx)
    return out


def reset_cache() -> None:
    _cache.clear()
