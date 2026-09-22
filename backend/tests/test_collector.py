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

    by_key = {s.source: s for s in target.statuses()}
    assert by_key["bbc"].last_error is not None
    assert by_key["bbc"].healthy is False
    assert by_key["guardian"].last_error is None
    assert by_key["guardian"].healthy is True
    assert target.total_count() == 5


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
    assert len(FEEDS) == 4
    assert len({f.url for f in FEEDS}) == 4
    assert len({f.key for f in FEEDS}) == 4
    for spec in FEEDS:
        assert spec.verified, f"{spec.key}: 검증 날짜가 비어 있다"
        assert spec.url.startswith("https://")
    # 한국어 소스가 정확히 하나여야 한다 (비교의 전제).
    assert [f.key for f in FEEDS if f.lang == "ko"] == ["yna"]
