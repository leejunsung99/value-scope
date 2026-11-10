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
    ctx = w.result["context"]
    assert ctx["target_pre"] is not None and ctx["peer_pre"] is not None and ctx["target_5w"] is not None
    assert "2026-10-28" in ctx["diverged_days"] and wed["diverged"]
    assert {p["name"] for p in ctx["peers"]}   # 비교기업별 직전 4주·이번 주·5주 누적
    assert any(s["key"] == "drivers" and s["title"] == "비교기업 대비 강세 배경" for s in w.report["sections"])


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


def test_weekly_commentary_rejects_statistical_jargon():
    from app.services.ai import AIProvider, AIResult
    from app.services.weekly_run import build_evidence, write_report

    res = {"week": {"start": "2026-11-02", "end": "2026-11-08", "trading_days": 5, "base_date": "2026-10-30", "base_close": 80000.0, "last_close": 63000.0},
           "summary": {"target_return": -0.212, "peer_return": -0.104, "market_return": -0.11, "excess_vs_peers": -0.108, "excess_vs_market": -0.102,
                       "residual_sum": -0.115, "z_week": -1.19, "pattern": "comove", "pattern_label": "비교기업과 동반 하락 (비교기업보다 10.8%p 약세)",
                       "same_dir_days": 3, "judged_days": 5, "abnormal_days": [], "volatility_flags": [], "volatility_label": "변동성 평소 수준"},
           "days": [], "peers": [], "volatility": {}, "model": {"market_label": "KOSDAQ 시가총액가중 대용지수"}}
    ev = build_evidence("선익시스템", res, [], [])
    ok_id = res["summary"]["eids"]["target"]

    class FakeAI(AIProvider):
        name = "fake"

        def complete_json(self, system, prompt, schema):
            assert "z값" in system and "평서형" in system   # 리포트 문체·용어 금지 지침 전달
            return AIResult(data={"headline": "선익시스템은 -21.2%로 개별 요인 합계 -11.5%(z=-1.19)였다.",
                                  "sections": [{"key": "overview", "paragraphs": [
                                      {"text": "선익시스템 주가는 이번 주 -21.2% 내렸다.", "evidence_ids": [ok_id]},
                                      {"text": "개별 요인 합계는 통계적으로 유의하지 않았다.", "evidence_ids": [ok_id]}]}]},
                            provider="fake", model="f")

    rep = write_report(FakeAI(), "선익시스템", res, ev)
    paras = rep["sections"][0]["paragraphs"]
    assert paras[0]["text"] == "선익시스템 주가는 이번 주 -21.2% 내렸다."
    assert [p["text"] for p in paras[1:]] == ["이번 주 회사 관련 공시나 언론 보도는 없었습니다."]   # 요약에 이슈 유무가 빠지면 자동 보충
    assert rep["held"] and "통계 용어" in rep["held"][0]["issues"][0]
    assert "z=" not in rep["headline"] and "개별 요인" not in rep["headline"]   # 규칙 기반 헤드라인으로 대체


def test_hedging_sentences_are_removed_and_overview_states_issue_status():
    from app.services.ai import NoAIProvider
    from app.services.weekly_run import build_evidence, strip_hedges, write_report

    t = "11월 3일(화) 기재정정 공시가 있었다. 같은 날 주가는 -9.4% 내렸다. 다만 정정 내용과 주가 반응의 관계는 확인된 자료만으로 단정하기 어렵다."
    assert strip_hedges(t) == "11월 3일(화) 기재정정 공시가 있었다. 같은 날 주가는 -9.4% 내렸다."
    assert strip_hedges("인과관계는 확인되지 않는다.") == ""

    res = {"week": {"start": "2026-11-02", "end": "2026-11-08", "trading_days": 5, "base_date": "2026-10-30", "base_close": None, "last_close": None},
           "summary": {"target_return": -0.2, "peer_return": -0.1, "market_return": -0.1, "excess_vs_peers": -0.1, "excess_vs_market": -0.1,
                       "pattern": "comove", "pattern_label": "비교기업과 동반 하락", "same_dir_days": 3, "judged_days": 5,
                       "abnormal_days": [], "volatility_flags": [], "volatility_label": "변동성 평소 수준"},
           "days": [], "peers": [], "volatility": {}, "model": {"market_label": "KOSDAQ 시가총액가중 대용지수"}}
    ev = build_evidence("T", res, [], [], target_id=1)
    rep = write_report(NoAIProvider(), "T", res, ev)   # 규칙 기반
    overview = next(s for s in rep["sections"] if s["key"] == "overview")
    assert any("공시나 언론 보도는 없었습니다" in p["text"] for p in overview["paragraphs"])

    discs = [{"date": "2026-11-03", "company_id": 1, "company": "T", "title": "[기재정정]단일판매ㆍ공급계약체결", "url": "u", "rcept_no": "1"},
             {"date": "2026-11-05", "company_id": 2, "company": "P", "title": "잠정실적", "url": "u2", "rcept_no": "2"}]
    ev2 = build_evidence("T", res, discs, [], target_id=1)
    assert res["target_issues"]["disclosures"] == [{"date": "2026-11-03", "title": "[기재정정]단일판매ㆍ공급계약체결"}]  # 비교기업 공시는 제외
    rep2 = write_report(NoAIProvider(), "T", res, ev2)
    ov2 = next(s for s in rep2["sections"] if s["key"] == "overview")
    assert any("11월 3일 '단일판매·공급계약 체결(정정)' 공시가 있었고" in p["text"] for p in ov2["paragraphs"])
