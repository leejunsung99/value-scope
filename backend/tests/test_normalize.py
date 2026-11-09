"""Improvements 1-3: core earnings, normalized earnings, delivery-based forward revenue (synthetic data)."""
from datetime import date

import pytest

from app.models import Company
from app.services import collect_financials as cf
from app.services.engine_data import FinSeries
from app.services.normalize import core_earnings, forward_schedule, normalized, tax_rate
from tests.test_phase2 import _add, _row


def _q(year, code, rev, op, pbt, tax, ni, add_mult, ir_ytd, ip_ytd, fin_cost):
    def r(sj, aid, nm, th, add=None):
        return _row(sj, aid, nm, th, add)
    rows = [r("CIS", "ifrs-full_Revenue", "매출액", rev, rev * add_mult), r("CIS", "dart_OperatingIncomeLoss", "영업이익(손실)", op, op * add_mult),
            r("CIS", "ifrs-full_ProfitLossBeforeTax", "법인세비용차감전순이익(손실)", pbt, pbt * add_mult),
            r("CIS", "ifrs-full_IncomeTaxExpenseContinuingOperation", "법인세비용", tax, tax * add_mult),
            r("CIS", "ifrs-full_ProfitLoss", "당기순이익(손실)", ni, ni * add_mult),
            r("CIS", "ifrs-full_FinanceCosts", "금융원가", fin_cost, fin_cost * add_mult),
            r("CF", "ifrs-full_InterestReceivedClassifiedAsOperatingActivities", "이자수취", ir_ytd),
            r("CF", "ifrs-full_InterestPaidClassifiedAsOperatingActivities", "이자지급", -ip_ytd),  # sign varies -> abs
            r("BS", "ifrs-full_Equity", "자본총계", 1000), r("BS", "ifrs-full_CashAndCashEquivalents", "현금", 10)]
    return rows


@pytest.fixture()
def fs(db):
    c = Company(company_key="dart:88888888", corp_code="88888888", stock_code="888888", name="정상화", market="KOSDAQ",
                is_listed=True, is_verified=True)
    db.add(c)
    db.commit()
    # every quarter: rev 100, op 20, net interest 2, tax 22% of pbt; one-off finance loss -50 in 2025 Q4 only
    for y in (2023, 2024, 2025):
        for code, q in (("11013", 1), ("11012", 2), ("11014", 3)):
            _add(db, c, y, code, _q(y, code, 100, 20, 22, 22 * 0.22, 22 * 0.78, q, 1.0 * q, 0.5 * q, 0.5), f"{y}{q * 3:02d}20")
        loss = 50 if y == 2025 else 0
        pbt_fy = 88 - loss
        _add(db, c, y, "11011", _q(y, "11011", 400, 80, pbt_fy, 88 * 0.22, pbt_fy - 88 * 0.22, 1, 4.0, 2.0, 2 + loss), f"{y + 1}0320")
    db.commit()
    cf.normalize(db, c)
    return FinSeries.load(db, c)


def test_tax_rate_and_core_earnings(fs):
    t, note = tax_rate(fs, "2025-12-31")
    assert 0.05 <= t <= 0.35 and "법인세" in note
    ce = core_earnings(fs, "2025-12-31")
    assert ce["interest_paid_ttm"] == pytest.approx(2.0)  # abs() of negative CF presentation
    assert ce["core_ni_ttm"] == pytest.approx((80 + 4 - 2) * (1 - t))
    # the one-off 50 finance loss sits in the gap, not in core earnings
    assert ce["non_recurring_gap"] < -30
    assert ce["finance_costs_ttm"] == pytest.approx(52.0)


def test_normalized_earnings_use_12_quarters(fs):
    n = normalized(fs, "2025-12-31")
    assert n["ebit_norm"] == pytest.approx(80.0)  # 240 / 3
    assert n["margin_norm"] == pytest.approx(0.2)
    assert normalized(fs, "2024-12-31") == {}  # only 8 quarters available -> not computed (never 0)


def test_forward_schedule_allocates_undisclosed_and_shifts():
    book = {"contracts": [
        {"status": "active", "start_date": "2026-01-01", "end_date": "2026-12-31", "amount": 365.0, "amount_disclosed": True,
         "counterparty": "A", "description": "x", "latest_rcept_no": "1", "contract_date": "2026-01-01"},
        {"status": "active", "start_date": "2026-05-01", "end_date": "2027-03-31", "amount": None, "amount_disclosed": False,
         "counterparty": "B", "description": "y", "latest_rcept_no": "2", "contract_date": "2026-05-01"},
        {"status": "active", "start_date": "2026-02-01", "end_date": "2028-06-30", "amount": None, "amount_disclosed": False,
         "counterparty": "C", "description": "z", "latest_rcept_no": "3", "contract_date": "2026-02-01"},
        {"status": "completed", "start_date": "2025-01-01", "end_date": "2025-06-30", "amount": 999.0, "amount_disclosed": True,
         "counterparty": "D", "description": "w", "latest_rcept_no": "4", "contract_date": "2025-01-01"},
    ]}
    official = {"amount": 1000.0, "as_of": "2026-06", "last_delivery": "2028-06-30"}
    s = forward_schedule(book, official, date(2026, 11, 9))
    rem_a = 365.0 * (date(2026, 12, 31) - date(2026, 6, 30)).days / 364
    assert s["disclosed_remaining"] == pytest.approx(rem_a)
    assert s["undisclosed_pool"] == pytest.approx(1000 - rem_a)
    d_b = (date(2027, 3, 31) - date(2026, 5, 1)).days
    d_c = (date(2028, 6, 30) - date(2026, 2, 1)).days
    share = (1000 - rem_a) * d_b / (d_b + d_c)  # fix 3: allocation proportional to contract duration
    assert s["scenarios"]["base"]["revenue"] == pytest.approx(rem_a + share)  # A (Dec 26) + B (Mar 27); C is 2028
    # +6m delay: A -> 2027-07, B -> 2027-09 still inside the horizon (to 2027-11-09)
    assert s["scenarios"]["conservative"]["revenue"] == pytest.approx(rem_a + share)
    late = forward_schedule(book, official, date(2026, 6, 30))  # horizon to 2027-06-30: B +6m -> 2027-09 falls out
    assert late["scenarios"]["conservative"]["revenue"] < late["scenarios"]["base"]["revenue"]  # delays push deliveries out
    assert s["scenarios"]["optimistic"]["revenue"] >= s["scenarios"]["base"]["revenue"]


def test_undetermined_delivery_excluded_and_lapsed_status(db):
    from app.models import Company
    from app.models_analysis import ContractOrder
    from app.services.collect_disclosures import contract_book

    c = Company(company_key="dart:77777777", corp_code="77777777", name="계약", market="KOSDAQ", is_listed=True, is_verified=True)
    db.add(c)
    db.commit()
    base = dict(company_id=c.id, report_nm="단일판매ㆍ공급계약체결", source="TEST", url="u", is_correction=False,
                is_cancellation=False, completed=False, counterparty="X", description="d")
    # delayed twice then end date set to '-' (undetermined)
    db.add(ContractOrder(rcept_no="1", filed_date="20240228", contract_key="k1", amount=300.0, amount_disclosed=True,
                         start_date="2024-02-27", end_date="2025-01-26", contract_date="2024-02-27", **base))
    db.add(ContractOrder(rcept_no="2", filed_date="20250219", contract_key="k1", amount=300.0, amount_disclosed=True,
                         start_date="2024-02-27", end_date="2025-05-25", contract_date="2024-02-27", **base))
    db.add(ContractOrder(rcept_no="3", filed_date="20250923", contract_key="k1", amount=300.0, amount_disclosed=True,
                         start_date="2024-02-27", end_date=None, contract_date="2024-02-27", **base))
    # undisclosed amount, withholding expired, delivery passed, never completed -> lapsed
    db.add(ContractOrder(rcept_no="4", filed_date="20240416", contract_key="k2", amount=None, amount_disclosed=False,
                         start_date="2024-04-16", end_date="2025-03-28", withheld_until="2025-03-28", contract_date="2024-04-16", **base))
    db.commit()
    book = contract_book(db, c.id, date(2026, 6, 30))
    by = {x["key"]: x for x in book["contracts"]}
    assert by["k1"]["end_undetermined"] and by["k1"]["status"] == "active"
    assert by["k2"]["status"] == "lapsed" and book["undisclosed_active"] == 0 and book["lapsed_count"] == 1
    s = forward_schedule(book, {"amount": 1000.0, "as_of": "2026-06", "last_delivery": "2027-07-31"}, date(2026, 11, 9))
    assert s["undetermined_amount"] == 300.0
    assert all(p["label"] != "X" or not p["disclosed"] for p in s["pieces"])  # k1 not in the delivery schedule
    assert any("납기 미정" in n for n in s["notes"])


def test_forward_schedule_requires_official_backlog():
    s = forward_schedule({"contracts": []}, None, date(2026, 11, 9))
    assert s["available"] is False
