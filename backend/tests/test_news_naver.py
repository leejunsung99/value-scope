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


def test_context_filter_drops_unrelated_articles(monkeypatch):
    monkeypatch.setitem(nw._naver_state, "last", 0.0)
    monkeypatch.setitem(nw._naver_state, "blocked_until", 0.0)
    items = [{"title": "日 축구, 브라질 또 꺾었다", "url": "https://s.example/1", "content": "야스 선수가 골을 넣었다", "outlet": "S"},
             {"title": "야스, OLED 증착장비 수주", "url": "https://s.example/2", "content": "야스가 장비를 공급한다", "outlet": "E"}]
    s = nw.NaverNewsSearcher(interval=(0, 0), client=httpx.Client(transport=_transport(_page(items), "")))
    spec = nw.SearchSpec("k", "비교기업", "q", 1, 10, terms=["야스"], context=nw.MARKET_CONTEXT)
    assert [o["title"] for o in s.search(spec, "2026-11-09", "2026-11-15")] == ["야스, OLED 증착장비 수주"]


def test_market_flow_words_are_not_advice():
    from app.services.weekly_run import invest_advice
    assert invest_advice("반도체 장비주 전반으로 매수세가 확산됐다는 보도가 나왔다.") is None
    assert invest_advice("외국인 순매도가 이어졌다.") is None
    assert invest_advice("매수 의견을 유지한다.") is not None


def test_more_pages_and_body_mention(monkeypatch):
    """첫 화면 10건 다음 묶음(무한 스크롤)도 읽고, 제목·요약에 없고 본문에만 회사명이 있는 기사도 남긴다."""
    monkeypatch.setitem(nw._naver_state, "last", 0.0)
    monkeypatch.setitem(nw._naver_state, "blocked_until", 0.0)
    first = _page(ITEMS[:1]).replace("</html>", '<script>new XLoad({ "url": "https:\/\/s.search.naver.com\/p\/more?x=1" });</script></html>')
    more_item = [{"title": "OLED 투자 확대 기대", "url": "https://b.example/9", "content": "패널 업체 증설", "outlet": "C뉴스"}]
    more = json.dumps({"collection": [{"html": _page(more_item)}], "url": ""})
    art = '<html><meta property="article:published_time" content="2026-11-12T09:00:00+09:00"><p>장비주가 올랐다. 증착장비 업체 선익시스템도 수혜가 기대된다. 끝.</p></html>'

    def handler(req):
        if req.url.host == "search.naver.com":
            return httpx.Response(200, text=first)
        if req.url.host == "s.search.naver.com":
            return httpx.Response(200, text=more)
        return httpx.Response(200, text=art)

    s = nw.NaverNewsSearcher(interval=(0, 0), client=httpx.Client(transport=httpx.MockTransport(handler)))
    out = s.search(nw.SearchSpec("k", "x", "q", 1, 10, terms=["선익시스템"], pages=3), "2026-11-09", "2026-11-15")
    assert [o["url"] for o in out] == ["https://a.example/1", "https://b.example/9"]
    assert "선익시스템도 수혜가 기대된다" in out[1]["summary"]


def test_theme_terms_from_company_articles():
    arts = [("OLED 투자 확대 기대", "증착장비 수요"), ("OLED 수혜주", "선익시스템 증착장비"), ("마이크로 OLED", "증착장비 수주"),
            ("ESG 등급", "AI 안경")]
    assert nw.theme_terms(arts, "선익시스템") == ["OLED 관련주", "증착장비"]
