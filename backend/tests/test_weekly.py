"""주간 주가 분석 — 합성 시세로 동조/개별 요인 판정, 이벤트 연결, API 흐름을 검증."""
from datetime import date

import pytest

from app import api_weekly
from app import db as db_module
from app.models import Project
from app.models_analysis import StockPrice, WeeklyAnalysis
from app.services import news as news_mod
from app.services import weekly as wk
from app.services.ai import NoAIProvider
from app.services.weekly_run import create_weekly, execute_weekly
from tests.test_phase2_pipeline import TODAY, FakeKrx, FakeNewsSearcher, NoMonitor, PipelineDart, _setup_project

WEEK = "2026-10-26"  # 월요일 (TODAY = 2026-11-06)


def test_week_bounds_validation():
    assert wk.week_bounds("2026-10-26") == (date(2026, 10, 26), date(2026, 11, 1))
    with pytest.raises(wk.WeeklyError):
        wk.week_bounds("2026-10-28")   # 수요일
    with pytest.raises(wk.WeeklyError):
        wk.week_bounds("26-10-26")


def _run(db, pid, news=None, shock=None):
    project = db.get(Project, pid)
    w = create_weekly(db, project, WEEK, include_news=news is not None)
    krx = FakeKrx()
    if shock:
        # 시세를 먼저 수집한 뒤 고객사 주가에 수요일부터 +20% 개별 충격을 준다(상장주식수 불변 → 분할로 보지 않음)
        from app.services.collect_prices import collect_prices

        collect_prices(db, krx, TODAY)
        code = project.company.stock_code
        for row in db.query(StockPrice).filter(StockPrice.stock_code == code, StockPrice.trade_date >= shock):
            row.close *= 1.2
        db.commit()
    execute_weekly(db_module._SessionLocal, w.id, krx, PipelineDart(), NoMonitor(), NoAIProvider(), news_searcher=news, today=TODAY)
    db.expire_all()
    return db.get(WeeklyAnalysis, w.id)


def test_weekly_comove_without_shock(client, db, monkeypatch):
    pid = _setup_project(client)
    w = _run(db, pid)
    assert w.status == "succeeded", w.messages
    r = w.result
    assert r["week"]["trading_days"] == 5 and r["week"]["start"] == WEEK
    assert len(r["days"]) == 5 and all(d["weekday"] in "월화수목금" for d in r["days"])
    assert r["summary"]["pattern"] in ("comove", "mixed")      # 합성 시세는 같은 추세 + 잡음
    assert r["chart"]["target"][0][1] == 100.0 and len(r["chart"]["target"]) == 6
    assert {p["name"] for p in r["peers"] if p["available"]}   # 상장 비교기업 수익률 산출
    rep = w.report
    assert rep["status"] == "rule_based" and rep["headline"] and rep["sections"]
    assert all(p["evidence_ids"] for s in rep["sections"] for p in s["paragraphs"])


def test_weekly_detects_idiosyncratic_shock(client, db):
    pid = _setup_project(client)
    w = _run(db, pid, shock="20261028")
    assert w.status == "succeeded", w.messages
    s = w.result["summary"]
    assert s["pattern"] == "idiosyncratic" and "상승" in s["pattern_label"]
    assert s["abnormal_days"] == ["2026-10-28"]
    wed = next(d for d in w.result["days"] if d["date"] == "2026-10-28")
    assert wed["abnormal"] and wed["z"] > 2.5 and wed["explanation"].startswith("확인된 공시·보도 없음")


def test_weekly_links_news_to_days(client, db, monkeypatch):
    monkeypatch.setattr(news_mod, "check_link", lambda url, c: "ok")

    class WeekNews(FakeNewsSearcher):
        def search(self, spec, since, until):
            assert (since, until) == ("2026-10-26", "2026-11-01")   # 해당 주로 고정된 검색 기간
            if spec.role == "target":
                return [{"pub_date": "2026-10-28", "date_certain": True, "outlet": "뉴스", "title": "선익시스템 수주",
                         "url": "https://n.example/1", "summary": "보도", "event": "신규 수주 보도", "sentiment": "positive",
                         "about": "선익시스템"}]
            return []

    pid = _setup_project(client)
    w = _run(db, pid, news=WeekNews(), shock="20261028")
    assert w.status == "succeeded", w.messages
    wed = next(d for d in w.result["days"] if d["date"] == "2026-10-28")
    assert wed["news"] and wed["news"][0]["event"] == "신규 수주 보도"
    assert wed["explanation"].startswith("언론 보도와 시간적 근접")
    assert any(n["event"] == "신규 수주 보도" for n in w.result["news"])


def test_weekly_api_flow(client, db, monkeypatch):
    import app.api_analysis as aa

    monkeypatch.setattr(api_weekly, "RUN_IN_THREAD", False)
    monkeypatch.setattr(aa, "make_clients", lambda: (PipelineDart(), FakeKrx(), NoAIProvider(), NoMonitor()))
    monkeypatch.setattr(aa, "make_news_searcher", lambda: None)
    pid = _setup_project(client)
    assert client.post(f"/api/projects/{pid}/weekly", json={"week_start": "2026-10-28"}).status_code == 422  # 월요일 아님
    rng = client.get(f"/api/projects/{pid}/weekly/range").json()
    assert date.fromisoformat(rng["latest"]).weekday() == 0 and date.fromisoformat(rng["earliest"]).weekday() == 0
    # (today 고정이 아니므로 실행 결과 자체는 다른 테스트에서 검증) — 목록/조회 형식만 확인
    lst = client.get(f"/api/projects/{pid}/weekly").json()
    assert lst == []
