"""End-to-end analysis pipeline with SYNTHETIC DART/KRX fakes (no network)."""
from datetime import date

import numpy as np

from app import api_analysis
from app import db as db_module
from app.models_analysis import AnalysisRun, ApiCallLog, ContractOrder, FinancialReport, StockPrice, Valuation
from app.services.analysis import create_run, execute_run
from app.services.dart import DartNoData
from tests.conftest import SAMPLE_REGISTRY, FakeDart, _markup

TODAY = date(2026, 11, 6)
LISTED = {e.corp_code: e for e in SAMPLE_REGISTRY if e.stock_code}


def _fs_rows(corp: str, year: int, code: str) -> list[dict]:
    k = int(corp[-2:])
    scale = 1e9 * (1 + k % 5)
    q = {"11013": 1, "11012": 2, "11014": 3, "11011": 4}[code]
    rev_q = scale * (1 + 0.05 * (year - 2022)) * (1 + 0.1 * q)
    op_q = rev_q * (0.08 + 0.02 * (k % 3))
    if code == "11011":
        rev, op, rev_add, op_add = rev_q * 4.2, op_q * 4.2, None, None
    else:
        rev, op = rev_q, op_q
        rev_add, op_add = rev_q * q * 1.05, op_q * q * 1.05
    rn = f"{year}{q:02d}15{corp[-6:]}"

    def r(sj, aid, nm, th, add=None):
        return {"rcept_no": rn, "sj_div": sj, "account_id": aid, "account_nm": nm, "thstrm_amount": f"{th:.0f}",
                "thstrm_add_amount": "" if add is None else f"{add:.0f}"}

    return [r("CIS", "ifrs-full_Revenue", "매출액", rev, rev_add), r("CIS", "dart_OperatingIncomeLoss", "영업이익(손실)", op, op_add),
            r("CIS", "ifrs-full_ProfitLoss", "당기순이익(손실)", op * 0.8, None if op_add is None else op_add * 0.8),
            r("BS", "ifrs-full_CashAndCashEquivalents", "현금및현금성자산", scale * 2), r("BS", "x", "단기차입금", scale),
            r("BS", "ifrs-full_Equity", "자본총계", scale * 10), r("BS", "ifrs-full_EquityAttributableToOwnersOfParent", "지배", scale * 10)]


CONTRACT = ("단일판매ㆍ공급계약체결\n1. 판매ㆍ공급계약 내용\n증착장비\n2. 계약내역\n계약금액 총액(원)\n5,000,000,000\n"
            "3. 계약상대방\n테스트디스플레이\n5. 계약기간\n시작일\n2026-03-01\n종료일\n2027-03-01\n8. 계약(수주)일자\n2026-03-01\n")
PERIODIC = ("II. 사업의 내용\n1. 사업의 개요\n장비 제조\n4. 매출 및 수주상황\n다. 수주상황\n(단위 : 백만원)\n수주총액\n기납품액\n수주잔고\n"
            "합 계\n-\n100,000\n-\n40,000\n-\n60,000\n5. 위험관리\n")


class PipelineDart(FakeDart):
    def financial_statements(self, corp, year, code, div):
        self._check()
        self.calls.append(f"fs:{corp}:{year}:{code}")
        if corp not in LISTED or div != "CFS" or (year == 2026 and code in ("11014", "11011")):
            raise DartNoData("no", "013")
        return _fs_rows(corp, year, code)

    def share_status(self, corp, year, code):
        return [{"se": "보통주", "istc_totqy": "1,000,000", "tesstk_co": "10,000", "distb_stock_co": "990,000", "rcept_no": "r"}]

    def disclosures_all(self, corp, bgn, end, pblntf_ty=None):
        self.calls.append(f"list:{corp}")
        return [{"rcept_no": f"20260302{corp[-6:]}", "report_nm": "단일판매ㆍ공급계약체결", "rcept_dt": "20260302"}]

    def disclosures(self, corp, **kw):
        return {"list": [{"rcept_no": f"20260814{corp[-6:]}", "report_nm": "반기보고서 (2026.06)", "rcept_dt": "20260814"}]}

    def document_markup(self, rcept_no):
        self.calls.append(f"doc:{rcept_no}")
        if rcept_no.startswith("20260302"):
            return CONTRACT
        return _markup(PERIODIC)


class FakeKrx:
    def __init__(self):
        self.calls = 0
        self.on_call = None

    def daily(self, market, d):
        self.calls += 1
        if self.on_call:
            self.on_call(market)
        rng = np.random.default_rng(int(d.strftime("%Y%m%d")) + (0 if market == "ksq" else 7))
        out = []
        for e in SAMPLE_REGISTRY:
            if not e.stock_code or (market == "stk") != e.stock_code.startswith("0"):
                continue
            k = int(e.corp_code[-2:])
            px = 10000 * (1 + k / 10) * (1 + 0.0004 * (d - date(2022, 1, 1)).days) * (1 + rng.normal(0, 0.01))
            out.append({"BAS_DD": d.strftime("%Y%m%d"), "ISU_CD": e.stock_code, "ISU_NM": e.corp_name, "TDD_CLSPRC": f"{px:.0f}",
                        "TDD_OPNPRC": f"{px:.0f}", "TDD_HGPRC": f"{px:.0f}", "TDD_LWPRC": f"{px:.0f}", "ACC_TRDVOL": "1000",
                        "ACC_TRDVAL": "1", "MKTCAP": f"{px * 1e6:.0f}", "LIST_SHRS": "1000000"})
        return out


class NoMonitor:
    enabled = False


def _setup_project(client):
    pid = client.post("/api/projects", json={"corp_code": "00000001"}).json()["id"]
    client.post(f"/api/projects/{pid}/peer-search")
    peers = client.get(f"/api/projects/{pid}/peers").json()
    keep = [m["id"] for m in peers["members"] if m["company"]["name"] in ("야스", "힘스", "비아트론", "에이피시스템", "캐논토키")]
    client.post(f"/api/projects/{pid}/peers/bulk-status", json={"membership_ids": keep, "status": "included"})
    for m in peers["members"]:
        if m["id"] in keep and m["company"]["name"] != "캐논토키":
            client.patch(f"/api/projects/{pid}/peers/{m['id']}", json={"use_type": "valuation"})
    assert client.post(f"/api/projects/{pid}/peers/confirm", json={}).status_code == 201
    return pid


def test_full_pipeline_snapshots_and_incremental(client, db):
    from app.models import Project
    from app.services.ai import NoAIProvider

    pid = _setup_project(client)
    project = db.get(Project, pid)
    dart, krx = PipelineDart(), FakeKrx()
    run = create_run(db, project, NoAIProvider())
    execute_run(db_module._SessionLocal, run.id, dart, krx, NoAIProvider(), NoMonitor(), today=TODAY)
    db.expire_all()
    r1 = db.get(AnalysisRun, run.id)
    assert r1.status in ("succeeded", "partial"), r1.messages
    assert r1.price_as_of == "20261105"
    target = db.query(Valuation).filter_by(run_id=r1.id, is_target=True).one()
    assert target.metrics["values"]["revenue_ttm"] > 0
    assert target.fair_value.get("scenarios") or target.verdict == "판단 유보"
    # unlisted foreign peer: no fake numbers
    canon = [v for v in db.query(Valuation).filter_by(run_id=r1.id) if v.company.name == "캐논토키"][0]
    assert canon.metrics["values"].get("market_cap") is None and "price" in canon.metrics["na"]
    # official backlog parsed from periodic report and used as fact
    assert target.data_status["backlog"]["amount"] == 60_000e6
    # each contract stored once
    assert db.query(ContractOrder).count() == db.query(ContractOrder.rcept_no).distinct().count()
    # API results endpoint
    res = client.get(f"/api/projects/{pid}/analysis-runs/{r1.id}/results").json()
    assert res["report"]["status"] == "rule_based" and res["target"]["name"] == "선익시스템"
    assert res["charts"]["price_index"] and res["charts"]["multiples"]
    ev = client.get(f"/api/projects/{pid}/analysis-runs/{r1.id}/evidence").json()
    assert all(e["source"] for e in ev)
    # chart data equals stored metrics
    mc = {m["name"]: m for m in res["charts"]["market_caps"]}
    assert mc["선익시스템"]["value"] == res["target"]["metrics"]["values"]["market_cap"]

    n_fs, n_px, krx_calls = db.query(FinancialReport).count(), db.query(StockPrice).count(), krx.calls
    calls_before = len(dart.calls)

    # ---- second run: incremental (no duplicate raw rows, prices reused), first snapshot unchanged
    snapshot_before = dict(target.fair_value)
    run2 = create_run(db, project, NoAIProvider())
    execute_run(db_module._SessionLocal, run2.id, dart, krx, NoAIProvider(), NoMonitor(), today=TODAY)
    db.expire_all()
    assert db.get(AnalysisRun, run2.id).status in ("succeeded", "partial")
    assert db.query(FinancialReport).count() == n_fs
    assert db.query(StockPrice).count() == n_px
    assert krx.calls == krx_calls  # all dates already fetched
    new_calls = dart.calls[calls_before:]
    assert not any(c.startswith("doc:20260302") for c in new_calls)  # contract text not re-downloaded
    assert db.query(Valuation).filter_by(run_id=r1.id, is_target=True).one().fair_value == snapshot_before
    runs = client.get(f"/api/projects/{pid}/analysis-runs").json()
    assert len(runs) == 2
    cmp = client.get(f"/api/projects/{pid}/analysis-runs/compare/{r1.id}/{run2.id}").json()
    assert cmp["rows"][0]["label"] == "적정주가(기준)"
    # API call log was written (budget accounting)
    assert db.query(ApiCallLog).filter_by(provider="KRX").count() == krx_calls


def test_run_requires_confirmed_group_and_blocks_concurrent(client, db):
    from app.models import Project
    from app.services.ai import NoAIProvider

    pid = client.post("/api/projects", json={"corp_code": "00000001"}).json()["id"]
    assert client.post(f"/api/projects/{pid}/analysis-runs").status_code == 422
    pid = _setup_project(client) if False else pid
    project = db.get(Project, pid)
    client.post(f"/api/projects/{pid}/peer-search")
    peers = client.get(f"/api/projects/{pid}/peers").json()
    client.patch(f"/api/projects/{pid}/peers/{peers['members'][0]['id']}", json={"status": "included"})
    client.post(f"/api/projects/{pid}/peers/confirm", json={})
    db.refresh(project)
    create_run(db, project, NoAIProvider())
    api_analysis.RUN_IN_THREAD = False
    r = client.post(f"/api/projects/{pid}/analysis-runs")
    assert r.status_code == 409


def test_budget_stops_collection(client, db, monkeypatch):
    from app.config import get_settings
    from app.models import Project
    from app.services.ai import NoAIProvider

    pid = _setup_project(client)
    project = db.get(Project, pid)
    monkeypatch.setattr(get_settings(), "dart_daily_call_limit", 0)
    run = create_run(db, project, NoAIProvider())

    class BudgetDart(PipelineDart):
        def financial_statements(self, corp, year, code, div):
            if self.on_call:
                self.on_call("fnlttSinglAcntAll.json")
            return super().financial_statements(corp, year, code, div)

    execute_run(db_module._SessionLocal, run.id, BudgetDart(), FakeKrx(), NoAIProvider(), NoMonitor(), today=TODAY)
    db.expire_all()
    r = db.get(AnalysisRun, run.id)
    assert r.status == "failed" and any("호출 상한" in m["message"] for m in r.messages)
