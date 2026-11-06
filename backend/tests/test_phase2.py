"""PHASE 2 engine tests — all inputs are SYNTHETIC fixtures (not real filings)."""
from datetime import date

import pandas as pd
import pytest

from app.models import Company
from app.models_analysis import ContractOrder, FinancialReport
from app.services import collect_financials as cf
from app.services import engine as eng
from app.services.backlog import parse_backlog
from app.services.collect_disclosures import contract_book, parse_contract
from app.services.engine_data import FinSeries, adjusted_returns
from app.services.report import check_numbers, generate_report, known_values


def _row(sj, aid, nm, th, add=None):
    return {"sj_div": sj, "account_id": aid, "account_nm": nm, "thstrm_amount": str(th),
            "thstrm_add_amount": "" if add is None else str(add), "rcept_no": "x"}


def _report(year, code, rev, op, ni, rev_add=None, op_add=None, ni_add=None, cash=100, debt=50, eq=1000, cfo=None):
    rows = [
        _row("CIS", "ifrs-full_Revenue", "매출액", rev, rev_add),
        _row("CIS", "dart_OperatingIncomeLoss", "영업이익(손실)", op, op_add),
        _row("CIS", "ifrs-full_ProfitLoss", "당기순이익(손실)", ni, ni_add),
        _row("CIS", "ifrs-full_ProfitLossAttributableToOwnersOfParent", "지배", ni, ni_add),
        _row("BS", "ifrs-full_CashAndCashEquivalents", "현금및현금성자산", cash),
        _row("BS", "x", "단기차입금", debt),
        _row("BS", "x2", "유동성전환사채", 10),
        _row("BS", "x3", "단기차입금의 상환", 999),  # must not count as debt (BS only rows, but name excluded)
        _row("BS", "ifrs-full_Equity", "자본총계", eq),
        _row("BS", "ifrs-full_EquityAttributableToOwnersOfParent", "지배기업 소유주지분", eq - 20),
        _row("BS", "y", "단기금융상품", 30),
    ]
    if cfo is not None:
        rows.append(_row("CF", "ifrs-full_CashFlowsFromUsedInOperatingActivities", "영업활동현금흐름", cfo))
    return rows


@pytest.fixture()
def company(db):
    c = Company(company_key="dart:99999999", corp_code="99999999", stock_code="999999", name="테스트", market="KOSDAQ",
                is_listed=True, is_verified=True)
    db.add(c)
    db.commit()
    return c


def _add(db, c, year, code, rows, filed):
    db.add(FinancialReport(company_id=c.id, bsns_year=year, reprt_code=code, fs_div="CFS", rcept_no=f"{filed}000001",
                           filed_date=filed, rows=rows))


def test_quarter_derivation_ttm_and_corrections(db, company):
    # 2025: Q1 10, Q2 20, Q3 30, FY 100 -> Q4 = 100 - 60 = 40
    _add(db, company, 2025, "11013", _report(2025, "11013", 10, 1, 1, 10, 1, 1, cfo=5), "20250515")
    _add(db, company, 2025, "11012", _report(2025, "11012", 20, 2, 2, 30, 3, 3, cfo=12), "20250814")
    _add(db, company, 2025, "11014", _report(2025, "11014", 30, 3, 3, 60, 6, 6, cfo=20), "20251114")
    _add(db, company, 2025, "11011", _report(2025, "11011", 100, 10, 10, cfo=33), "20260320")
    # correction of the annual report filed later with a different revenue -> must win
    _add(db, company, 2025, "11011", _report(2025, "11011", 110, 10, 10, cfo=33), "20260401")
    db.commit()
    cf.normalize(db, company)
    fs = FinSeries.load(db, company)
    assert fs.qval("revenue", "2025-12-31") == 110 - 60
    assert fs.qval("operating_cash_flow", "2025-06-30") == 12 - 5  # YTD differencing
    assert fs.qval("operating_cash_flow", "2025-12-31") == 33 - 20
    assert fs.ttm("revenue", "2025-12-31") == 110
    assert fs.ttm("revenue", "2025-09-30") is None  # 2024 quarters missing -> no TTM, not 0
    # debt = 단기차입금 50 + 유동성전환사채 10 (상환 row excluded)
    assert fs.pt("total_debt", "2025-12-31") == 60
    assert fs.pt("short_term_investments", "2025-12-31") == 30
    # point-in-time: before the correction was filed, the original annual applies? (latest report wins overall)
    assert fs.q["revenue"]["2025-12-31"].rcept_no.startswith("20260401")


def test_metrics_and_negative_earnings(db, company):
    for y, rev, op, ni in [(2024, 100, 10, 8), (2025, 120, -5, -4)]:
        _add(db, company, y, "11013", _report(y, "11013", rev / 4, op / 4, ni / 4, rev / 4, op / 4, ni / 4), f"{y}0515")
        _add(db, company, y, "11012", _report(y, "11012", rev / 4, op / 4, ni / 4, rev / 2, op / 2, ni / 2), f"{y}0814")
        _add(db, company, y, "11014", _report(y, "11014", rev / 4, op / 4, ni / 4, rev * .75, op * .75, ni * .75), f"{y}1114")
        _add(db, company, y, "11011", _report(y, "11011", rev, op, ni), f"{y + 1}0320")
    db.commit()
    cf.normalize(db, company)
    fs = FinSeries.load(db, company)
    ev = eng.Evidence()
    m = eng.compute_metrics(fs, {"price": 1000.0, "mcap": 500.0, "listed_shares": 10.0, "date": "2026-01-02"},
                            {"treasury": 1.0}, ev, "20260102")
    v = m.values
    assert v["revenue_ttm"] == 120
    assert v["revenue_growth"] == pytest.approx(0.2)
    assert v["net_debt"] == 60 - 100 - 30
    assert v["minority_interest"] == 20
    assert v["ev"] == 500 + (60 - 100 - 30) + 20
    assert v["shares_out"] == 9
    assert "per" not in v and "적자" in m.na["per"]  # negative PER is never compared
    assert "ev_ebit" not in v and "영업적자" in m.na["ev_ebit"]
    assert v["pbr"] == pytest.approx(500 / 980)
    assert v["op_margin"] == pytest.approx(-5 / 120)
    # every computed number has evidence with method/source
    e = {x["eid"]: x for x in ev.items}
    assert e[m.eids["revenue_ttm"]]["source"] == "OpenDART 재무제표" and e[m.eids["revenue_ttm"]]["method"]


def test_combine_verdict_rules():
    ev = eng.Evidence()
    mk = lambda name, lo, b, hi: {"method": name, "label": name, "available": True, "notes": [],
                                  "scenarios": {"conservative": {"per_share": lo}, "base": {"per_share": b}, "optimistic": {"per_share": hi}},
                                  "assumptions": {}}
    methods = [mk("peer", 80, 100, 120), mk("history", 90, 110, 130), {"method": "forward", "label": "f", "available": False, "notes": ["없음"], "scenarios": {}}]
    out = eng.combine(methods, price=50, peer_count=4, earnings_unstable=False, undisclosed=0, ev=ev, target_name="T")
    assert out["verdict"] == "저평가" and out["scenarios"]["base"] == pytest.approx(0.4 / 0.7 * 100 + 0.3 / 0.7 * 110)
    assert out["confidence"] == 85  # one method unavailable (-15)
    out = eng.combine(methods, price=200, peer_count=4, earnings_unstable=False, undisclosed=0, ev=ev, target_name="T")
    assert out["verdict"] == "고평가"
    # too few methods or low confidence -> 판단 유보
    out = eng.combine(methods[:1] + methods[2:], price=100, peer_count=4, earnings_unstable=False, undisclosed=0, ev=ev, target_name="T")
    assert out["verdict"] == "판단 유보" and "2개 미만" in out["verdict_reason"]
    out = eng.combine(methods, price=100, peer_count=1, earnings_unstable=True, undisclosed=2, ev=ev, target_name="T")
    assert out["confidence"] == 100 - 15 - 15 - 15 - 10  # forward N/A, peers<3, unstable, undisclosed
    wide = [mk("peer", 10, 20, 30), mk("history", 90, 110, 130), methods[2]]
    out = eng.combine(wide, price=100, peer_count=1, earnings_unstable=True, undisclosed=2, ev=ev, target_name="T")
    assert out["confidence"] < 40 and out["verdict"] == "판단 유보"  # + method dispersion > 2x


def test_peer_method_uses_quantiles_and_adjustments():
    ev = eng.Evidence()
    t = eng.CompanyMetrics(1, "T", values={"operating_income_ttm": 100.0, "net_income_owners_ttm": 80.0, "net_debt": -50.0,
                                           "minority_interest": 10.0, "shares_out": 10.0})
    peers = [eng.CompanyMetrics(i, f"P{i}", values={"ev_ebit": x, "per": x + 2}) for i, x in enumerate([5.0, 7.0, 9.0], start=2)]
    r = eng.method_peer(t, peers, ev)
    assert r["available"] and r["peer_count"] == 3
    evb = 7.0 * 100  # median EV/EBIT x EBIT
    eq_ev = evb - (-50) - 10
    eq_per = 9.0 * 80
    assert r["scenarios"]["base"]["per_share"] == pytest.approx((eq_ev / 10 + eq_per / 10) / 2)


def test_contract_parse_and_dedupe(db, company):
    original = ("단일판매ㆍ공급계약체결\n1. 판매ㆍ공급계약 내용\n증착장비\n2. 계약내역\n계약금액 총액(원)\n1,000,000,000\n"
                "3. 계약상대방\n테스트디스플레이\n5. 계약기간\n시작일\n2025-01-01\n종료일\n2025-12-31\n8. 계약(수주)일자\n2025-01-01\n")
    corr = ("정정신고(보고)\n2. 정정관련 공시서류제출일\n2025-01-02\n3. 정정사유\n계약금액 변경\n" + original.replace("1,000,000,000", "1,200,000,000"))
    done = corr.replace("계약금액 변경", "계약이행 완료에 따른 정정")
    for rno, nm, txt, dt in [("20250102000001", "단일판매ㆍ공급계약체결", original, "20250102"),
                             ("20250601000001", "[기재정정]단일판매ㆍ공급계약체결", corr, "20250601"),
                             ("20260105000001", "[기재정정]단일판매ㆍ공급계약체결", done, "20260105")]:
        p = parse_contract(txt, nm)
        db.add(ContractOrder(company_id=company.id, rcept_no=rno, report_nm=nm, filed_date=dt, source="TEST", url="u", **p))
    db.commit()
    book = contract_book(db, company.id, date(2025, 7, 1))
    assert book["filings_total"] == 3 and book["contracts_total"] == 1 and book["duplicates_merged"] == 2
    c = book["contracts"][0]
    assert c["amount"] == 1.2e9 and c["status"] == "completed" and c["remaining_estimate"] == 0


def test_backlog_parser_requires_arithmetic_check():
    good = "다. 수주상황\n(단위 : 백만원)\n품목\n수주총액\n기납품액\n수주잔고\n합 계\n-\n1,000\n-\n300\n-\n700\n5. 위험관리\n"
    b = parse_backlog(good)
    assert b and b["amount"] == 700e6
    bad = good.replace("\n700\n", "\n650\n")
    assert parse_backlog(bad) is None  # totals don't reconcile -> not used as fact


def test_split_adjusted_returns():
    df = pd.DataFrame({"date": pd.to_datetime(["2025-01-02", "2025-01-03", "2025-01-06"]),
                       "close": [10000.0, 2000.0, 2100.0], "shares": [1e6, 5e6, 5e6]})
    r, events = adjusted_returns(df)
    assert r.iloc[1] == pytest.approx(0.0) and len(events) == 1
    assert r.iloc[2] == pytest.approx(0.05)


def test_report_number_check_and_rule_based_fallback():
    ev = eng.Evidence()
    e1 = ev.add("fact", "매출", 51_576_100_000_0.0, "KRW", "5,158억원")
    e2 = ev.add("calc", "EV/EBIT", 10.24, "x", "10.2배")
    vals = known_values(ev.items)
    assert check_numbers("매출은 5,158억원, EV/EBIT 10.2배", vals) == []
    assert check_numbers("매출은 7,000억원", vals) == ["7,000억원"]

    class NoAI:
        name = "none"

        def complete_json(self, *a):
            from app.services.ai import AIUnavailable
            raise AIUnavailable("off")

    payload = {"name": "T", "verdict": "적정", "price_eid": e1, "fair_value_eid": e2}
    rep = generate_report(NoAI(), payload, ev.items)
    assert rep["status"] == "rule_based"
    assert {s["key"] for s in rep["content"]["sections"]} >= {"overall", "conclusion", "triggers"}
    assert rep["review"]["verdict_consistent"]


def test_report_flags_ai_numbers_not_in_evidence():
    ev = eng.Evidence()
    e1 = ev.add("fact", "영업이익", 1e11, "KRW", "1,000억원")

    class FakeAI:
        name = "fake"
        model = "m"

        def __init__(self):
            self.n = 0

        def complete_json(self, system, prompt, schema):
            from app.services.ai import AIResult
            self.n += 1
            if self.n == 1:
                return AIResult(provider="fake", model="m", data={
                    "summary": "s", "final_verdict": "고평가",
                    "sections": [{"key": "overall", "paragraphs": [{"text": "영업이익 1,000억원", "evidence_ids": [e1], "claim_type": "fact"},
                                                                   {"text": "영업이익 3,000억원 예상", "evidence_ids": [e1], "claim_type": "estimate"}]},
                                 {"key": "conclusion", "paragraphs": [{"text": "결론", "evidence_ids": [e1], "claim_type": "judgment"}]}]})
            return AIResult(provider="fake", model="m", data={"issues": [], "counter_evidence_reviewed": True, "overall_comment": "ok"})

    rep = generate_report(FakeAI(), {"name": "T", "verdict": "적정"}, ev.items)
    paras = {p["ref"]: p for s in rep["content"]["sections"] for p in s["paragraphs"]}
    assert paras["overall.1"]["status"] == "verified"
    assert paras["overall.2"]["status"] == "unverified"
    assert paras["conclusion.1"]["status"] == "unverified"  # writer verdict 고평가 ≠ engine 적정
    assert rep["content"]["final_verdict"] == "적정"
