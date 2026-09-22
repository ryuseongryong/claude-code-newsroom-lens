"""수집·정규화·격리 테스트. 네트워크를 타지 않는다."""

from __future__ import annotations

import httpx
import pytest

from app.collector.feeds import FeedError, fetch_feed
from app.collector.normalize import clamp_summary, normalize_nhk_item, strip_html
from app.collector.poller import poll_once
from app.config import FEEDS, FEEDS_BY_KEY
from app.store import ArticleStore
from app.store.models import Article

RSS_OK = b"""<?xml version="1.0"?><rss version="2.0"><channel>
<item><title>Headline &amp; Co</title><link>https://example.com/a</link>
<description>&lt;p&gt;Body &lt;b&gt;text&lt;/b&gt;&lt;/p&gt;</description>
<pubDate>Mon, 22 Sep 2026 04:00:00 GMT</pubDate></item>
<item><title>Second</title><link>https://example.com/b</link>
<pubDate>Mon, 22 Sep 2026 03:00:00 GMT</pubDate></item>
</channel></rss>"""


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# ── 정규화 ────────────────────────────────────────────────────────────────────


def test_strip_html_unescapes_after_removing_tags():
    # 순서가 중요하다: 태그 제거 → 엔티티 해제. 반대면 살아난 태그가 본문을 먹는다.
    assert strip_html("<p>Body <b>text</b></p>") == "Body text"
    assert strip_html("A &amp; B") == "A & B"
    assert strip_html("&lt;script&gt;alert(1)&lt;/script&gt;") == "<script>alert(1)</script>"
    assert strip_html(None) == ""


def test_clamp_summary_cuts_at_limit():
    assert clamp_summary("가" * 400, limit=300).endswith("…")
    assert len(clamp_summary("가" * 400, limit=300)) == 300
    assert clamp_summary("짧다", limit=300) == "짧다"


def test_nhk_item_link_is_absolute_and_epoch_ms_parsed():
    art = normalize_nhk_item(
        FEEDS_BY_KEY["nhk"],
        {
            "id": "20260905_100",
            "title": "Title",
            "description": "Desc",
            "page_url": "/nhkworld/en/news/20260905_100/",
            "updated_at": "1788553140000",
        },
    )
    assert art is not None
    assert art.link.startswith("https://www3.nhk.or.jp/nhkworld/")
    assert art.published.startswith("2026-")


def test_nhk_item_falls_back_to_id_date_when_timestamps_empty():
    art = normalize_nhk_item(
        FEEDS_BY_KEY["nhk"],
        {"id": "20260905_100", "title": "T", "page_url": "/x/", "updated_at": "", "public_at": ""},
    )
    assert art is not None
    assert art.published.startswith("2026-09-05")


def test_entry_without_link_is_dropped():
    from app.collector.normalize import normalize_rss_entry

    assert normalize_rss_entry(FEEDS_BY_KEY["bbc"], {"title": "T", "link": ""}) is None
    assert normalize_rss_entry(FEEDS_BY_KEY["bbc"], {"title": "", "link": "https://x"}) is None


# ── 가져오기 ──────────────────────────────────────────────────────────────────


async def test_fetch_rss_normalizes_entries():
    async with _client(lambda r: httpx.Response(200, content=RSS_OK)) as client:
        articles = await fetch_feed(client, FEEDS_BY_KEY["bbc"])
    assert [a.title for a in articles] == ["Headline & Co", "Second"]
    assert articles[0].summary == "Body text"
    assert articles[0].published == "2026-09-22T04:00:00+00:00"


async def test_fetch_raises_feederror_on_http_500():
    async with _client(lambda r: httpx.Response(500)) as client:
        with pytest.raises(FeedError, match="HTTP 500"):
            await fetch_feed(client, FEEDS_BY_KEY["bbc"])


async def test_fetch_raises_feederror_on_empty_feed():
    empty = b'<?xml version="1.0"?><rss version="2.0"><channel></channel></rss>'
    async with _client(lambda r: httpx.Response(200, content=empty)) as client:
        with pytest.raises(FeedError, match="0건"):
            await fetch_feed(client, FEEDS_BY_KEY["bbc"])


async def test_fetch_raises_feederror_on_malformed_nhk_json():
    async with _client(lambda r: httpx.Response(200, text="not json")) as client:
        with pytest.raises(FeedError):
            await fetch_feed(client, FEEDS_BY_KEY["nhk"])


# ── 격리 (Phase 1 DoD) ────────────────────────────────────────────────────────


async def test_one_dead_feed_does_not_kill_the_others(monkeypatch):
    """BBC 만 죽었을 때 나머지 셋은 정상 수집되어야 한다."""

    def handler(request: httpx.Request) -> httpx.Response:
        if "bbci.co.uk" in str(request.url):
            raise httpx.ConnectError("boom", request=request)
        if "nhk.or.jp" in str(request.url):
            return httpx.Response(
                200,
                json={"data": [{"id": "20260922_1", "title": "N", "page_url": "/p/", "updated_at": "1788553140000"}]},
            )
        return httpx.Response(200, content=RSS_OK)

    monkeypatch.setattr("app.collector.poller.make_client", lambda: _client(handler))

    target = ArticleStore()
    results = await poll_once(target)

    assert isinstance(results["bbc"], str)          # 실패는 이유 문자열
    assert results["guardian"] == 2                 # 나머지는 건수
    assert results["nhk"] == 1
    assert results["yna"] == 2
    # 소스를 추가해도 이 테스트가 조용히 통과하지 않도록: BBC 외에는 전부 성공이어야 한다.
    assert [k for k, v in results.items() if isinstance(v, str)] == ["bbc"]

    by_key = {s.source: s for s in target.statuses()}
    assert by_key["bbc"].last_error is not None
    assert by_key["bbc"].healthy is False
    assert by_key["guardian"].last_error is None
    assert by_key["guardian"].healthy is True
    assert target.total_count() == 2 * (len(FEEDS) - 2) + 1


async def test_failure_keeps_previously_collected_articles(monkeypatch):
    """실패했다고 화면을 비우지 않는다. 마지막으로 성공한 기사를 계속 보여준다."""
    target = ArticleStore()
    target.record_success("bbc", [Article("bbc", "T", "https://x/1", "2026-09-22T04:00:00+00:00", "s")])
    target.record_failure("bbc", "HTTP 503")

    status = {s.source: s for s in target.statuses()}["bbc"]
    assert status.last_error == "HTTP 503"
    assert status.last_success is not None      # 과거 성공 시각은 남는다
    assert len(target.latest("bbc")) == 1       # 기사도 남는다


# ── 저장소 ────────────────────────────────────────────────────────────────────


def _art(i: int, day: int = 22) -> Article:
    return Article("bbc", f"T{i}", f"https://x/{i}", f"2026-09-{day:02d}T{i % 24:02d}:00:00+00:00", "s")


def test_link_is_idempotent_key():
    target = ArticleStore()
    target.record_success("bbc", [_art(1), _art(1), _art(2)])
    target.record_success("bbc", [_art(1)])
    assert len(target.latest("bbc")) == 2


def test_cap_is_enforced_and_keeps_newest(monkeypatch):
    monkeypatch.setattr("app.store.memory.settings.max_articles_per_source", 50)
    target = ArticleStore()
    # 60건을 넣는다. 오래된 날짜 30건 + 최신 날짜 30건.
    old = [Article("bbc", f"o{i}", f"https://o/{i}", "2026-09-01T00:00:00+00:00", "s") for i in range(30)]
    new = [Article("bbc", f"n{i}", f"https://n/{i}", "2026-09-22T00:00:00+00:00", "s") for i in range(30)]
    target.record_success("bbc", old + new)

    kept = target.latest("bbc")
    assert len(kept) == 50
    # 최신 30건은 전부 살아 있어야 한다.
    assert sum(1 for a in kept if a.link.startswith("https://n/")) == 30


def test_latest_is_sorted_newest_first():
    target = ArticleStore()
    target.record_success("bbc", [_art(1, day=1), _art(2, day=22), _art(3, day=10)])
    days = [a.published[:10] for a in target.latest("bbc")]
    assert days == sorted(days, reverse=True)


def test_all_feed_urls_are_verified_and_unique():
    assert len(FEEDS) >= 4
    assert len({f.url for f in FEEDS}) == len(FEEDS)
    assert len({f.key for f in FEEDS}) == len(FEEDS)
    for spec in FEEDS:
        assert spec.verified, f"{spec.key}: 검증 날짜가 비어 있다"
        assert spec.url.startswith("https://")
    # 한국어 소스가 정확히 하나여야 한다 (비교의 전제).
    assert [f.key for f in FEEDS if f.lang == "ko"] == ["yna"]


# ── 정체 감지 (NHK 실측 사고) ─────────────────────────────────────────────────


def test_frozen_index_is_not_reported_healthy():
    """HTTP 200 + 기사 수백 건이어도 내용이 늙었으면 정상이 아니다.

    실측 사고(2026-09-22): NHK 의 낡은 JSON 인덱스가 405건을 정상 응답으로 계속
    내보내면서 2026-09-05 에 얼어붙어 있었다. 수집은 매번 성공했으므로 last_error 는
    None 이었고, 화면은 17일 전 기사를 아무 경고 없이 보여줬다. 죽은 소스는 눈에
    보이지만 얼어붙은 소스는 보이지 않는다 — 그래서 별도 상태가 필요하다.
    """
    from datetime import datetime, timedelta, timezone

    from app.store.models import STALE_AFTER_HOURS, SourceStatus

    old = (datetime.now(timezone.utc) - timedelta(days=17)).isoformat(timespec="seconds")
    frozen = SourceStatus(
        source="nhk", label="NHK", label_en="NHK", lang="en",
        count=405, last_success="2026-09-22T05:00:00+00:00", newest_published=old,
    )
    assert frozen.last_error is None      # 수집은 성공했다
    assert frozen.count > 0               # 기사도 많다
    assert frozen.stale is True           # 그래도 정체다
    assert frozen.healthy is False        # 정상으로 보고하지 않는다
    assert frozen.stale_hours > STALE_AFTER_HOURS
    assert frozen.to_dict()["stale"] is True


def test_fresh_source_is_healthy_and_not_stale():
    from datetime import datetime, timedelta, timezone

    from app.store.models import SourceStatus

    fresh = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat(timespec="seconds")
    ok = SourceStatus(
        source="bbc", label="BBC", label_en="BBC", lang="en",
        count=32, last_success="2026-09-22T05:00:00+00:00", newest_published=fresh,
    )
    assert ok.stale is False
    assert ok.healthy is True


def test_missing_newest_published_is_not_treated_as_stale():
    """발행 시각을 모르는 것과 늙은 것은 다르다. 모를 때 경고를 띄우면 늑대소년이 된다."""
    from app.store.models import SourceStatus

    unknown = SourceStatus(source="x", label="X", label_en="X", lang="en", count=5)
    assert unknown.stale_hours is None
    assert unknown.stale is False
    assert unknown.healthy is True


def test_store_records_newest_article_time_not_fetch_time():
    """newest_published 는 '매체가 쓴 시각' 이다. '우리가 가져온 시각' 과 구별되어야 한다."""
    target = ArticleStore()
    target.record_success("bbc", [
        Article("bbc", "old", "https://x/1", "2026-09-01T00:00:00+00:00", "s"),
        Article("bbc", "new", "https://x/2", "2026-09-22T04:00:00+00:00", "s"),
    ])
    status = {s.source: s for s in target.statuses()}["bbc"]
    assert status.newest_published.startswith("2026-09-22T04:00")
    assert status.last_success != status.newest_published


# ── 수동 새로 고침 속도 제한 ──────────────────────────────────────────────────


async def test_manual_refresh_is_rate_limited(monkeypatch):
    """버튼을 연타해도 외부 피드로 요청이 쏟아지지 않아야 한다."""
    from app.collector import poller

    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        if "nhk.or.jp" in str(request.url):
            return httpx.Response(200, json={"data": [
                {"id": "20260922_1", "title": "N", "page_url": "/p/", "updated_at": "1788553140000"}]})
        return httpx.Response(200, content=RSS_OK)

    def counting_client():
        nonlocal calls
        calls += 1
        return _client(handler)

    monkeypatch.setattr(poller, "make_client", counting_client)
    monkeypatch.setattr(poller.settings, "manual_refresh_min_seconds", 60)
    monkeypatch.setattr(poller, "_last_poll_at", None)

    target = ArticleStore()

    first = await poller.request_refresh(target)
    assert first["refreshed"] is True
    assert calls == 1

    # 즉시 다시 누른다 → 긁지 않고 남은 시간만 알려준다.
    second = await poller.request_refresh(target)
    assert second["refreshed"] is False
    assert 0 < second["retry_after_seconds"] <= 60
    assert calls == 1, "제한 중인데 피드를 다시 긁었다"

    # 제한이 지나면 다시 긁는다.
    monkeypatch.setattr(poller, "_last_poll_at", poller.time.monotonic() - 61)
    third = await poller.request_refresh(target)
    assert third["refreshed"] is True
    assert calls == 2


async def test_automatic_poll_also_arms_the_rate_limit(monkeypatch):
    """자동 폴이 방금 돌았으면 수동 새로 고침이 또 긁을 이유가 없다."""
    from app.collector import poller

    monkeypatch.setattr(poller, "make_client", lambda: _client(
        lambda r: httpx.Response(200, content=RSS_OK)))
    monkeypatch.setattr(poller.settings, "manual_refresh_min_seconds", 60)
    monkeypatch.setattr(poller, "_last_poll_at", None)

    target = ArticleStore()
    await poller.poll_once(target)                    # 자동 주기가 돌았다고 가정
    assert poller.seconds_until_refresh_allowed() > 0
    assert (await poller.request_refresh(target))["refreshed"] is False


def test_refresh_allowed_before_any_poll():
    """기동 직후에는 기다릴 이유가 없다."""
    from app.collector import poller

    saved = poller._last_poll_at
    try:
        poller._last_poll_at = None
        assert poller.seconds_until_refresh_allowed() == 0
    finally:
        poller._last_poll_at = saved


def test_default_poll_interval_is_five_minutes():
    from app.config import Settings

    assert Settings().poll_interval_seconds == 300
    assert Settings().manual_refresh_min_seconds == 60
