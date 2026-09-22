"""Lens 계약 테스트 — LLM 출력을 믿지 않는다는 것을 증명한다. Bedrock 을 타지 않는다."""

from __future__ import annotations

import json

import pytest

from app.llm import bedrock, lens
from app.llm.bedrock import LensError, parse_json_object
from app.store import ArticleStore
from app.store.models import Article

SOURCES = ("bbc", "guardian", "nhk", "yna")


@pytest.fixture(autouse=True)
def _clear_cache():
    lens.reset_cache()
    yield
    lens.reset_cache()


def _store(counts: dict[str, int]) -> ArticleStore:
    target = ArticleStore()
    for key, n in counts.items():
        target.record_success(
            key,
            [
                Article(key, f"{key} 기사 {i}", f"https://{key}/{i}", f"2026-09-22T{i:02d}:00:00+00:00", "요약")
                for i in range(n)
            ],
        )
    return target


def _model_reply(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False)


async def _build(monkeypatch, reply: str, counts: dict[str, int] | None = None):
    async def fake_converse(system, user):
        return reply

    monkeypatch.setattr(lens, "converse", fake_converse)
    return await lens.build_lens(target=_store(counts or {k: 5 for k in SOURCES}))


# ── 방어적 JSON 파싱 ──────────────────────────────────────────────────────────


def test_parses_plain_json():
    assert parse_json_object('{"a": 1}') == {"a": 1}


def test_parses_json_wrapped_in_code_fence():
    assert parse_json_object('```json\n{"a": 1}\n```') == {"a": 1}


def test_parses_json_with_prose_around_it():
    text = '분석 결과입니다:\n{"a": {"b": "}"}}\n도움이 되었으면 좋겠습니다.'
    assert parse_json_object(text) == {"a": {"b": "}"}}


def test_brace_inside_string_does_not_confuse_extractor():
    # 문자열 안의 '}' 를 깊이로 세면 객체가 조기에 끊긴다.
    assert parse_json_object('{"t": "a } b", "u": 2}') == {"t": "a } b", "u": 2}


def test_raises_on_truncated_json():
    with pytest.raises(LensError, match="JSON 객체를 찾지 못"):
        parse_json_object('{"clusters": [{"topic": "잘림')


def test_raises_when_no_object_at_all():
    with pytest.raises(LensError):
        parse_json_object("죄송하지만 분석할 수 없습니다.")


# ── Bedrock 호출 계약 ─────────────────────────────────────────────────────────


async def test_converse_requires_bearer_token(monkeypatch):
    monkeypatch.delenv("AWS_BEARER_TOKEN_BEDROCK", raising=False)
    with pytest.raises(LensError, match="AWS_BEARER_TOKEN_BEDROCK"):
        await bedrock.converse("sys", "user")


# ── 클러스터 검증 ─────────────────────────────────────────────────────────────


def _cluster(topic="주제", frames=None, sources=None):
    return {
        "topic": topic,
        "summary": "공통 사실 두 문장.",
        "frames": frames or {},
        "sources": sources or {},
    }


async def test_happy_path_shapes_frames_evidence_and_coverage(monkeypatch):
    reply = _model_reply(
        {
            "overview": "지형 3문장",
            "clusters": [
                _cluster(
                    frames={"bbc": "BBC 프레임", "guardian": "가디언 프레임", "nhk": "미보도", "yna": "미보도"},
                    sources={"bbc": [0, 2], "guardian": [1], "nhk": [], "yna": []},
                )
            ],
        }
    )
    out = await _build(monkeypatch, reply)

    cluster = out["clusters"][0]
    assert cluster["covered"] == ["bbc", "guardian"]
    assert cluster["uncovered"] == ["nhk", "yna"]
    assert cluster["frames"]["nhk"] == "미보도"
    # 근거가 인덱스가 아니라 실제 링크까지 해석되어 나가야 한다.
    assert [e["index"] for e in cluster["evidence"]["bbc"]] == [0, 2]
    # 인덱스 공간은 '프롬프트에 보여준 순서' = 최신순이다. 저장 순서가 아니다.
    # 이게 어긋나면 근거 칩이 다른 기사를 가리킨다 — 조용히 틀리는 최악의 버그다.
    presented = out["inputs"]["bbc"]
    assert [a["link"] for a in presented[:3]] == ["https://bbc/4", "https://bbc/3", "https://bbc/2"]
    assert cluster["evidence"]["bbc"][0]["link"] == presented[0]["link"]
    assert cluster["evidence"]["bbc"][1]["link"] == presented[2]["link"]
    assert out["cached"] is False
    # 프런트가 칩을 링크로 되돌릴 색인이 전 매체에 대해 있어야 한다.
    assert set(out["inputs"]) == set(SOURCES)


async def test_single_media_cluster_is_dropped(monkeypatch):
    """비교가 성립하지 않는 클러스터는 화면에 올리지 않는다."""
    reply = _model_reply(
        {
            "overview": "o",
            "clusters": [
                _cluster(topic="혼자", frames={"bbc": "BBC 만"}, sources={"bbc": [0]}),
                _cluster(topic="둘", frames={"bbc": "B", "yna": "Y"}, sources={"bbc": [1], "yna": [1]}),
            ],
        }
    )
    out = await _build(monkeypatch, reply)
    assert [c["topic"] for c in out["clusters"]] == ["둘"]


async def test_out_of_range_index_is_discarded_and_media_becomes_uncovered(monkeypatch):
    """없는 기사를 인용하면 그 매체의 프레임은 살리지 않는다 — 근거 없는 주장 금지."""
    reply = _model_reply(
        {
            "overview": "o",
            "clusters": [
                _cluster(
                    frames={"bbc": "실재", "guardian": "환각", "yna": "실재"},
                    # guardian 은 5건만 있는데 99를 인용했다.
                    sources={"bbc": [0], "guardian": [99], "yna": [2]},
                )
            ],
        }
    )
    out = await _build(monkeypatch, reply)
    cluster = out["clusters"][0]
    assert cluster["frames"]["guardian"] == "미보도"
    assert cluster["sources"]["guardian"] == []
    assert "guardian" in cluster["uncovered"]
    assert cluster["covered"] == ["bbc", "yna"]


async def test_frame_without_citation_is_downgraded(monkeypatch):
    """인덱스를 안 준 프레임은 못 싣는다."""
    reply = _model_reply(
        {
            "overview": "o",
            "clusters": [
                _cluster(
                    frames={"bbc": "근거 있음", "guardian": "근거 없음", "nhk": "근거 있음"},
                    sources={"bbc": [0], "guardian": [], "nhk": [0]},
                )
            ],
        }
    )
    out = await _build(monkeypatch, reply)
    assert out["clusters"][0]["frames"]["guardian"] == "미보도"
    assert out["clusters"][0]["covered"] == ["bbc", "nhk"]


async def test_duplicate_indices_are_deduped_preserving_order(monkeypatch):
    reply = _model_reply(
        {
            "overview": "o",
            "clusters": [
                _cluster(
                    frames={"bbc": "B", "yna": "Y"},
                    sources={"bbc": [2, 0, 2, 0], "yna": [1]},
                )
            ],
        }
    )
    out = await _build(monkeypatch, reply)
    assert out["clusters"][0]["sources"]["bbc"] == [2, 0]


async def test_cluster_count_is_capped_at_five(monkeypatch):
    reply = _model_reply(
        {
            "overview": "o",
            "clusters": [
                _cluster(topic=f"주제{i}", frames={"bbc": "B", "yna": "Y"}, sources={"bbc": [0], "yna": [0]})
                for i in range(9)
            ],
        }
    )
    out = await _build(monkeypatch, reply)
    assert len(out["clusters"]) == 5


async def test_raises_when_every_cluster_is_single_media(monkeypatch):
    reply = _model_reply(
        {"overview": "o", "clusters": [_cluster(frames={"bbc": "B"}, sources={"bbc": [0]})]}
    )
    with pytest.raises(LensError, match="매체가 함께 다룬 주제"):
        await _build(monkeypatch, reply)


async def test_raises_when_clusters_key_missing(monkeypatch):
    with pytest.raises(LensError, match="clusters"):
        await _build(monkeypatch, '{"overview": "o"}')


async def test_refuses_to_call_bedrock_with_fewer_than_two_live_sources(monkeypatch):
    """비교 대상이 하나면 Bedrock 을 부르지도 않는다 — 돈과 시간을 버리지 않는다."""
    called = False

    async def fake_converse(system, user):
        nonlocal called
        called = True
        return "{}"

    monkeypatch.setattr(lens, "converse", fake_converse)
    with pytest.raises(LensError, match="최소 2개 매체"):
        await lens.build_lens(target=_store({"bbc": 3}))
    assert called is False


# ── 10분 버킷 캐시 ────────────────────────────────────────────────────────────


async def test_second_call_in_same_bucket_is_served_from_cache(monkeypatch):
    calls = 0

    async def fake_converse(system, user):
        nonlocal calls
        calls += 1
        return _model_reply(
            {"overview": "o", "clusters": [_cluster(frames={"bbc": "B", "yna": "Y"}, sources={"bbc": [0], "yna": [0]})]}
        )

    monkeypatch.setattr(lens, "converse", fake_converse)
    target = _store({k: 5 for k in SOURCES})

    first = await lens.build_lens(target=target)
    second = await lens.build_lens(target=target)

    assert calls == 1
    assert first["cached"] is False and second["cached"] is True
    assert first["generated_at"] == second["generated_at"]

    # force_refresh 는 캐시를 무시한다.
    third = await lens.build_lens(force_refresh=True, target=target)
    assert calls == 2
    assert third["cached"] is False


def test_bucket_advances_every_ten_minutes():
    assert lens.current_bucket(0) == lens.current_bucket(599)
    assert lens.current_bucket(600) == lens.current_bucket(0) + 1
