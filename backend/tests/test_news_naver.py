"""네이버 뉴스 검색: 결과 페이지 파싱, 기사 날짜, 차단 시 Claude 대체 (실제 네트워크 사용 안 함)."""
import json
from datetime import datetime

import httpx
import pytest

from app.services import news as nw


def _page(items):
    body = {"body": {"props": {"children": [{"props": {
        "type": "searchBasic", "title": it["title"], "titleHref": it["url"], "content": it["content"],
        "imageSrc": "https://search.pstatic.net/common/?src=https%3A%2F%2Fimgnews.pstatic.net%2Fimage%2Forigin%2F5829%2F2026%2F11%2F15%2F1.jpg",
        "sourceProfile": {"title": it["outlet"], "subTexts": [{"text": "1일 전"}]}}} for it in items]}}}
    return ('<html><script>entry.bootstrap(document.getElementById("fdr-abc"), '
            + json.dumps(body, ensure_ascii=False) + ');</script></html>')


ITEMS = [{"title": "<mark>선익시스템</mark> 급등", "url": "https://a.example/1", "content": "<mark>선익시스템</mark>이 13% 올랐다", "outlet": "A뉴스"},
         {"title": "다른 회사 이야기", "url": "https://a.example/2", "content": "관련 없음", "outlet": "B뉴스"}]


def test_parse_naver_news_reads_embedded_items():
    got = nw.parse_naver_news(_page(ITEMS))
    assert [g["title"] for g in got] == ["선익시스템 급등", "다른 회사 이야기"]
    assert got[0]["outlet"] == "A뉴스" and got[0]["img_date"] == "2026-11-15" and got[0]["snippet"].startswith("선익시스템이")


def test_relative_date():
    now = datetime(2026, 11, 16, 12, 0, tzinfo=nw.KST)
    assert nw.relative_date("19시간 전", now) == "2026-11-15"
    assert nw.relative_date("3일 전", now) == "2026-11-13"
    assert nw.relative_date("2026.11.10.", now) == "2026-11-10"


def _transport(search_page, article_html, status=200):
    def handler(req: httpx.Request):
        if req.url.host == "search.naver.com":
            return httpx.Response(status, text=search_page)
        return httpx.Response(200, text=article_html)
    return httpx.MockTransport(handler)


def test_naver_searcher_keeps_mentions_and_reads_article_date(monkeypatch):
    monkeypatch.setitem(nw._naver_state, "last", 0.0)
    monkeypatch.setitem(nw._naver_state, "blocked_until", 0.0)
    art = '<meta property="article:published_time" content="2026-11-13T17:00:00+09:00"><meta property="og:description" content="XR 장비 수요 기대">'
    s = nw.NaverNewsSearcher(interval=(0, 0), client=httpx.Client(transport=_transport(_page(ITEMS), art)))
    spec = nw.SearchSpec("k", "선익시스템", "q", 1, 10, terms=["선익시스템"])
    out = s.search(spec, "2026-11-09", "2026-11-15")
    assert len(out) == 1
    o = out[0]
    assert o["pub_date"] == "2026-11-13" and o["date_certain"] and o["about"] == "선익시스템"
    assert "XR 장비 수요 기대" in o["summary"] and o["link_status"] == "ok"


def test_block_falls_back_to_claude(monkeypatch):
    monkeypatch.setitem(nw._naver_state, "last", 0.0)
    monkeypatch.setitem(nw._naver_state, "blocked_until", 0.0)
    naver = nw.NaverNewsSearcher(interval=(0, 0), client=httpx.Client(transport=_transport("", "", status=429)))

    class FakeClaude:
        model = "fake"

        def search(self, spec, since, until):
            return [{"title": "대체"}]

    comb = nw.CombinedNewsSearcher(naver, FakeClaude())
    spec = nw.SearchSpec("k", "x", "q", 1, 10, terms=["선익시스템"])
    assert comb.search(spec, "2026-11-09", "2026-11-15") == [{"title": "대체"}]
    assert nw._naver_state["blocked_until"] > 0           # 이후 요청은 잠시 멈춤
    with pytest.raises(nw.NaverBlocked):
        naver.search(spec, "2026-11-09", "2026-11-15")
    # 검색어가 없는 검색(통합 분석)은 Claude로
    assert comb.search(nw.SearchSpec("k2", "x", "q", 1, 10), "a", "b") == [{"title": "대체"}]
