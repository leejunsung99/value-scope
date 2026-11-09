from datetime import date

import httpx

from app.models import Company
from app.models_analysis import Disclosure, NewsItem, NewsSearch
from app.services import news

TODAY = date(2026, 11, 9)


def test_parse_items_extracts_array_and_drops_bad_rows():
    text = """여기 결과입니다.
```json
[{"date":"2026-10-21","date_certain":true,"outlet":"뉴스핌","title":"선익시스템, OLEDoS 증착장비 추가 수주",
  "url":"https://www.newspim.com/news/view/20261021000895","summary":"해외 고객사와 공급계약","event":"OLEDoS 추가 수주",
  "sentiment":"positive","about":"선익시스템"},
 {"date":"21일","title":"날짜 형식 틀림","url":"https://a.kr/1","sentiment":"great"},
 {"title":"링크 없음","url":"not-a-url"},
 {"url":"https://b.kr/2"}]
```"""
    items = news.parse_items(text)
    assert len(items) == 2
    assert items[0]["pub_date"] == "2026-10-21" and items[0]["date_certain"] and items[0]["sentiment"] == "positive"
    assert items[1]["pub_date"] is None and items[1]["date_certain"] is False and items[1]["sentiment"] == "neutral"
    assert news.parse_items("검색 결과 없음") == []


def _client(status_by_url: dict[str, int]) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(status_by_url.get(str(req.url), 200))))


def test_check_link_classifies_status():
    c = _client({"https://x.kr/404": 404, "https://x.kr/403": 403})
    assert news.check_link("https://x.kr/ok", c) == "ok"
    assert news.check_link("https://x.kr/404", c) == "gone"
    assert news.check_link("https://x.kr/403", c) == "unverified"


class FakeSearcher:
    model = "fake"

    def __init__(self, items):
        self.items = items
        self.calls = []

    def search(self, spec, since, until):
        self.calls.append((spec.query_key, since, until))
        return [dict(i) for i in self.items]


def _item(url, d="2026-10-21", event="OLEDoS 추가 수주", certain=True):
    return {"pub_date": d, "date_certain": certain, "outlet": "언론", "title": f"기사 {url[-1]}", "url": url,
            "summary": "요약", "event": event, "sentiment": "positive", "about": "T"}


def test_run_searches_cache_incremental_limit_and_link_filter(db):
    t = Company(company_key="dart:T", corp_code="T", name="T", stock_code="000001", is_listed=True)
    db.add(t)
    db.commit()
    spec = news.SearchSpec(f"company:{t.id}", "T", "q", months=6, max_items=10, company_id=t.id)
    s = FakeSearcher([_item("https://n.kr/1"), _item("https://n.kr/2"), _item("https://n.kr/3", d="2025-01-01")])
    client = _client({"https://n.kr/2": 404})
    r = news.run_searches(db, s, [spec], TODAY, daily_limit=10, link_client=client)
    assert r.outcomes[0].status == "searched" and r.outcomes[0].new_items == 1   # 404 dropped, out-of-window dropped
    assert [i.url for i in r.items[spec.query_key]] == ["https://n.kr/1"]
    # same day -> cached, no new search
    r2 = news.run_searches(db, s, [spec], TODAY, daily_limit=10, link_client=client)
    assert r2.outcomes[0].status == "cached" and len(s.calls) == 1
    # next week -> incremental from last until minus overlap
    r3 = news.run_searches(db, s, [spec], date(2026, 11, 16), daily_limit=10, link_client=client)
    assert r3.outcomes[0].status == "searched" and s.calls[-1][1] == "2026-11-06"
    # daily limit reached -> skipped, analysis can continue
    spec2 = news.SearchSpec("theme:p:customers", "전방", "q", months=6, max_items=10)
    r4 = news.run_searches(db, s, [spec2], date(2026, 11, 16), daily_limit=1, link_client=client)
    assert r4.outcomes[0].status == "skipped_limit"
    assert db.query(NewsSearch).count() == 2


def test_failed_search_is_recorded_and_does_not_raise(db):
    class Boom:
        model = "x"

        def search(self, *a):
            raise RuntimeError("cli down")

    spec = news.SearchSpec("theme:p:x", "x", "q", months=3, max_items=5)
    r = news.run_searches(db, Boom(), [spec], TODAY, daily_limit=5, link_client=_client({}))
    assert r.outcomes[0].status == "failed" and "cli down" in r.outcomes[0].error


def test_group_events_and_disclosure_match(db):
    t = Company(company_key="dart:T2", corp_code="T2", name="T2", stock_code="000002", is_listed=True)
    db.add(t)
    db.flush()
    db.add(Disclosure(company_id=t.id, rcept_no="20261020000001", report_nm="단일판매ㆍ공급계약체결", rcept_dt="20261020",
                      source="DART_LIST", url="https://dart/1"))
    items = [NewsItem(query_key="k", company_id=t.id, url=f"https://n.kr/{i}", title=f"수주 기사 {i}", outlet=f"언론{i}",
                      pub_date="2026-10-21", date_certain=i == 0, event="OLEDoS 추가 수주", sentiment="positive", summary="s")
             for i in range(3)]
    items.append(NewsItem(query_key="k", company_id=t.id, url="https://n.kr/x", title="경영진 인터뷰", outlet="언론X",
                          pub_date="2026-08-01", date_certain=True, event="경영진 인터뷰", sentiment="neutral"))
    evs = news.group_events(items)
    assert len(evs) == 2
    big = next(e for e in evs if e["event"] == "OLEDoS 추가 수주")
    assert len(big["articles"]) == 3 and big["date_certain"] and big["outlets"] == ["언론0", "언론1", "언론2"]
    news.match_disclosures(db, evs)
    assert big["verification"] == "confirmed" and big["disclosure"]["rcept_no"] == "20261020000001"
    other = next(e for e in evs if e["event"] == "경영진 인터뷰")
    assert other["verification"] == "news_only"


def test_numbers_from_news_summary_are_citable_but_not_from_other_text():
    from app.services.report import check_numbers, known_values

    items = [{"eid": "E1", "kind": "news", "value": None, "display": "2026-08-18 상반기 실적",
              "method": "상반기 매출 363억원, 영업손실 87억원 | 보도 2건", "detail": {"articles": [{"summary": "수주잔고 6,420억원"}]}},
             {"eid": "E2", "kind": "calc", "value": None, "display": "x", "method": "중간 계산 999억원", "detail": {}}]
    vals = known_values(items)
    assert check_numbers("보도에 따르면 매출 363억원, 수주잔고 6,420억원", vals) == []
    assert check_numbers("계산값 999억원", vals) != []
