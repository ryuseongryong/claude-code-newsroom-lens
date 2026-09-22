"""Lens 계약 테스트 — LLM 출력을 믿지 않는다는 것을 증명한다. Bedrock 을 타지 않는다."""

from __future__ import annotations

import json

import pytest

from app.llm import bedrock, lens
from app.llm.bedrock import LensError, parse_json_object
from app.config import FEEDS
from app.store import ArticleStore
from app.store.models import Article

SOURCES = tuple(s.key for s in FEEDS)
# 테스트는 특정 매체 이름에 의존하지 않는다. 주제를 바꿔 소스를 전부 교체해도
# 계약 검증은 그대로 성립해야 한다.
S1, S2, S3 = SOURCES[0], SOURCES[1], SOURCES[2]


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
                    frames={S1: "첫 매체 프레임", S2: "둘째 매체 프레임", S3: "미보도"},
                    sources={S1: [0, 2], S2: [1], S3: []},
                )
            ],
        }
    )
    out = await _build(monkeypatch, reply)

    cluster = out["clusters"][0]
    assert cluster["covered"] == [S1, S2]
    # 인용하지 않은 매체는 전부 미보도다. 소스를 추가해도 자동으로 따라와야 한다.
    assert cluster["uncovered"] == [k for k in SOURCES if k not in (S1, S2)]
    assert cluster["frames"][S3] == "미보도"
    # 근거가 인덱스가 아니라 실제 링크까지 해석되어 나가야 한다.
    assert [e["index"] for e in cluster["evidence"][S1]] == [0, 2]
    # 인덱스 공간은 '프롬프트에 보여준 순서' = 최신순이다. 저장 순서가 아니다.
    # 이게 어긋나면 근거 칩이 다른 기사를 가리킨다 — 조용히 틀리는 최악의 버그다.
    presented = out["inputs"][S1]
    assert [a["link"] for a in presented[:3]] == [f"https://{S1}/4", f"https://{S1}/3", f"https://{S1}/2"]
    assert cluster["evidence"][S1][0]["link"] == presented[0]["link"]
    assert cluster["evidence"][S1][1]["link"] == presented[2]["link"]
    assert out["cached"] is False
    # 프런트가 칩을 링크로 되돌릴 색인이 전 매체에 대해 있어야 한다.
    assert set(out["inputs"]) == set(SOURCES)


async def test_single_media_cluster_is_dropped(monkeypatch):
    """비교가 성립하지 않는 클러스터는 화면에 올리지 않는다."""
    reply = _model_reply(
        {
            "overview": "o",
            "clusters": [
                _cluster(topic="혼자", frames={S1: "한 곳만"}, sources={S1: [0]}),
                _cluster(topic="둘", frames={S1: "B", S2: "Y"}, sources={S1: [1], S2: [1]}),
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
                    frames={S1: "실재", S2: "환각", S3: "실재"},
                    # S2 는 5건만 있는데 99를 인용했다.
                    sources={S1: [0], S2: [99], S3: [2]},
                )
            ],
        }
    )
    out = await _build(monkeypatch, reply)
    cluster = out["clusters"][0]
    assert cluster["frames"][S2] == "미보도"
    assert cluster["sources"][S2] == []
    assert S2 in cluster["uncovered"]
    assert cluster["covered"] == [S1, S3]


async def test_frame_without_citation_is_downgraded(monkeypatch):
    """인덱스를 안 준 프레임은 못 싣는다."""
    reply = _model_reply(
        {
            "overview": "o",
            "clusters": [
                _cluster(
                    frames={S1: "근거 있음", S2: "근거 없음", S3: "근거 있음"},
                    sources={S1: [0], S2: [], S3: [0]},
                )
            ],
        }
    )
    out = await _build(monkeypatch, reply)
    assert out["clusters"][0]["frames"][S2] == "미보도"
    assert out["clusters"][0]["covered"] == [S1, S3]


async def test_duplicate_indices_are_deduped_preserving_order(monkeypatch):
    reply = _model_reply(
        {
            "overview": "o",
            "clusters": [
                _cluster(
                    frames={S1: "B", S2: "Y"},
                    sources={S1: [2, 0, 2, 0], S2: [1]},
                )
            ],
        }
    )
    out = await _build(monkeypatch, reply)
    assert out["clusters"][0]["sources"][S1] == [2, 0]


async def test_cluster_count_is_capped_at_five(monkeypatch):
    reply = _model_reply(
        {
            "overview": "o",
            "clusters": [
                _cluster(topic=f"주제{i}", frames={S1: "B", S2: "Y"}, sources={S1: [0], S2: [0]})
                for i in range(9)
            ],
        }
    )
    out = await _build(monkeypatch, reply)
    assert len(out["clusters"]) == 5


async def test_raises_when_every_cluster_is_single_media(monkeypatch):
    reply = _model_reply(
        {"overview": "o", "clusters": [_cluster(frames={S1: "B"}, sources={S1: [0]})]}
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
        await lens.build_lens(target=_store({S1: 3}))
    assert called is False


# ── 10분 버킷 캐시 ────────────────────────────────────────────────────────────


async def test_second_call_in_same_bucket_is_served_from_cache(monkeypatch):
    calls = 0

    async def fake_converse(system, user):
        nonlocal calls
        calls += 1
        return _model_reply(
            {"overview": "o", "clusters": [_cluster(frames={S1: "B", S2: "Y"}, sources={S1: [0], S2: [0]})]}
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


# ── 논조 (Bonus C) ────────────────────────────────────────────────────────────


async def test_tone_is_passed_through_for_covered_media(monkeypatch):
    reply = _model_reply(
        {
            "overview": "o",
            "clusters": [
                {
                    **_cluster(frames={S1: "B", S2: "G"}, sources={S1: [0], S2: [1]}),
                    "tone": {S1: 0, S2: -2},
                }
            ],
        }
    )
    out = await _build(monkeypatch, reply)
    assert out["clusters"][0]["tone"][S1] == 0
    assert out["clusters"][0]["tone"][S2] == -2


async def test_tone_out_of_range_is_clamped_not_rejected(monkeypatch):
    """막대 폭을 이 값으로 그리므로 범위를 벗어나면 레이아웃이 깨진다."""
    reply = _model_reply(
        {
            "overview": "o",
            "clusters": [
                {
                    **_cluster(frames={S1: "B", S2: "G"}, sources={S1: [0], S2: [1]}),
                    "tone": {S1: 99, S2: -99},
                }
            ],
        }
    )
    out = await _build(monkeypatch, reply)
    assert out["clusters"][0]["tone"] == {**out["clusters"][0]["tone"], S1: 2, S2: -2}


async def test_unparseable_tone_falls_back_to_neutral(monkeypatch):
    reply = _model_reply(
        {
            "overview": "o",
            "clusters": [
                {
                    **_cluster(frames={S1: "B", S2: "G"}, sources={S1: [0], S2: [1]}),
                    # 모델은 실제로 이런 값들을 보낸다.
                    "tone": {S1: "중립", S2: 1.6},
                }
            ],
        }
    )
    out = await _build(monkeypatch, reply)
    assert out["clusters"][0]["tone"][S1] == 0      # 문자열 → 0
    assert out["clusters"][0]["tone"][S2] == 2  # 1.6 → round → 2


async def test_uncovered_media_tone_is_zero(monkeypatch):
    reply = _model_reply(
        {
            "overview": "o",
            "clusters": [
                {
                    **_cluster(frames={S1: "B", S2: "G"}, sources={S1: [0], S2: [1]}),
                    "tone": {S1: 1, S2: 1, S3: -2},   # nhk 는 미보도인데 논조를 줬다
                }
            ],
        }
    )
    out = await _build(monkeypatch, reply)
    assert out["clusters"][0]["tone"][S3] == 0
    assert S3 in out["clusters"][0]["uncovered"]


async def test_every_source_appears_in_frames_tone_and_sources(monkeypatch):
    """소스를 추가해도 프런트가 키 누락으로 깨지지 않아야 한다."""
    reply = _model_reply(
        {
            "overview": "o",
            "clusters": [_cluster(frames={S1: "B", S2: "G"}, sources={S1: [0], S2: [1]})],
        }
    )
    out = await _build(monkeypatch, reply)
    c = out["clusters"][0]
    for field in ("frames", "tone", "sources", "evidence"):
        assert set(c[field]) == set(SOURCES), f"{field} 키 누락"


def test_prompt_enumerates_every_configured_source():
    """매체 목록을 하드코딩하지 않았음을 증명한다."""
    from app.llm.prompts import SYSTEM_PROMPT

    for spec in FEEDS:
        assert f'"{spec.key}"' in SYSTEM_PROMPT, spec.key
        assert spec.label in SYSTEM_PROMPT, spec.label
    assert f"{len(FEEDS)}개 화장품·뷰티 업계 전문지" in SYSTEM_PROMPT


# ── 잘못된 이스케이프 수리 (실측 버그) ───────────────────────────────────────


def test_repairs_invalid_single_quote_escape():
    """모델이 아포스트로피를 \\' 로 이스케이프한다. JSON 표준에 없어서 문서 전체가 거부된다.

    2026-09-22 실측: 제목 40개 묶음이 이것 하나 때문에 통째로 버려졌다.
    """
    raw = r'{"titles": [{"ko": "전 \'암살단\' 지도자, 감비아 군사 재판 출석"}]}'
    import json as _json
    with pytest.raises(_json.JSONDecodeError):
        _json.loads(raw)                       # 표준 파서는 거부한다
    out = parse_json_object(raw)               # 우리는 살려낸다
    assert out["titles"][0]["ko"] == "전 '암살단' 지도자, 감비아 군사 재판 출석"


def test_repair_preserves_legitimate_escapes():
    from app.llm.bedrock import repair_json_escapes

    # 정당한 이스케이프는 건드리지 않는다.
    assert repair_json_escapes(r'"a\nb"') == r'"a\nb"'
    assert repair_json_escapes(r'"a\"b"') == r'"a\"b"'
    assert repair_json_escapes(r'"a\\b"') == r'"a\\b"'
    assert repair_json_escapes(r'"a\u00e9b"') == r'"a\u00e9b"'
    assert repair_json_escapes(r'"a\/b"') == r'"a\/b"'


def test_repair_does_not_corrupt_backslash_before_quote():
    r"""`\\` 를 먼저 소비하지 않으면 정당한 백슬래시의 두 번째 문자가 다음 문자와
    짝지어져 잘못 지워진다. 이 경우가 회귀하면 경로가 깨진다."""
    from app.llm.bedrock import repair_json_escapes

    raw = r'{"p": "C:\\dir", "q": "it\'s"}'
    assert json.loads(repair_json_escapes(raw)) == {"p": "C:\\dir", "q": "it's"}


async def test_lens_survives_apostrophe_escaped_frames(monkeypatch):
    """렌즈도 같은 파서를 쓴다. 한국어 프레임이 영어 표현을 인용하면 이 경로를 밟는다."""
    # 모델이 아포스트로피를 역슬래시로 이스케이프한 '깨진 JSON' 을 그대로 흉내낸다.
    # 바깥을 큰따옴표로 감싸야 본문의 \' 가 온전히 남는다.
    # 키 이름은 실제 소스 키여야 하므로 % 로 끼운다.
    raw = (
        '{"overview": "o", "clusters": [{"topic": "t", "summary": "s",'
        " \"frames\": {\"%s\": \"\\'Trump TV\\' 피드를 운영했다\", \"%s\": \"G\"},"
        ' "sources": {"%s": [0], "%s": [1]}}]}'
    ) % (S1, S2, S1, S2)
    assert r"\'" in raw, "테스트가 검증하려는 잘못된 이스케이프가 실제로 들어 있어야 한다"

    async def fake_converse(system, user):
        return raw

    monkeypatch.setattr(lens, "converse", fake_converse)
    out = await lens.build_lens(target=_store({k: 3 for k in SOURCES}))
    assert "Trump TV" in out["clusters"][0]["frames"][S1]


def test_parse_error_message_names_the_actual_cause():
    """'객체를 찾지 못했다' 만 남기면 잘림·이스케이프·거절이 같은 메시지가 된다."""
    with pytest.raises(LensError, match="마지막 오류"):
        parse_json_object('{"clusters": [{"topic": "잘림')
