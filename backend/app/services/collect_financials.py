"""재무제표 수집(OpenDART fnlttSinglAcntAll) 및 분기 지표 정규화.

- 이미 받은 과거 보고서는 다시 받지 않는다. 최근 보고서만 정정 여부 확인을 위해 재조회.
- 정정 보고서는 접수번호가 달라 별도 행으로 보존되며, 계산에는 가장 늦게 접수된 보고서를 쓴다.
- 분기 3개월 실적: 1Q=1Q, 2Q=반기 보고서 당기 3개월, 3Q=3Q 보고서 당기 3개월, 4Q=연간−3Q누적.
- 현금흐름표는 누적 공시 → 분기값은 누적 차이로 계산.
- 12월 결산 법인을 가정한다 (비12월 결산은 data_status에 경고).
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import Company
from app.models_analysis import FinancialFetch, FinancialMetric, FinancialReport
from app.services.dart import DartClient, DartError, DartNoData

REPORTS = [("11013", 1, "1분기"), ("11012", 2, "반기"), ("11014", 3, "3분기"), ("11011", 4, "사업")]
QUARTER_END = {1: "03-31", 2: "06-30", 3: "09-30", 4: "12-31"}
FILING_LAG_DAYS = {1: 50, 2: 50, 3: 50, 4: 95}  # 법정 제출기한 근사
YEARS_BACK = 6  # 정상화(12분기)·자사 과거 평가에 필요한 이력

FLOW_METRICS = {
    "revenue": (("IS", "CIS"), ["ifrs-full_Revenue"], ["매출액", "수익(매출액)", "영업수익", "매출", "매출액(수익)"]),
    "operating_income": (("IS", "CIS"), ["dart_OperatingIncomeLoss"], ["영업이익(손실)", "영업이익", "영업손실"]),
    "net_income": (("IS", "CIS"), ["ifrs-full_ProfitLoss"], ["당기순이익(손실)", "당기순이익", "당기순손실", "분기순이익(손실)", "반기순이익(손실)"]),
    "net_income_owners": (("IS", "CIS"), ["ifrs-full_ProfitLossAttributableToOwnersOfParent"], []),
    "pbt": (("IS", "CIS"), ["ifrs-full_ProfitLossBeforeTax"], ["법인세비용차감전순이익(손실)", "법인세차감전순이익", "법인세비용차감전순이익"]),
    "income_tax": (("IS", "CIS"), ["ifrs-full_IncomeTaxExpenseContinuingOperation"], ["법인세비용(수익)", "법인세비용", "법인세수익(비용)"]),
    "finance_income": (("IS", "CIS"), ["ifrs-full_FinanceIncome", "ifrs-full_OtherFinanceIncome"], ["금융수익", "기타금융수익"]),
    "finance_costs": (("IS", "CIS"), ["ifrs-full_FinanceCosts", "ifrs-full_OtherFinanceCost"], ["금융원가", "금융비용", "기타금융원가"]),
    "other_gains": (("IS", "CIS"), ["dart_OtherGains"], ["기타수익", "기타이익", "기타영업외수익"]),
    "other_losses": (("IS", "CIS"), ["dart_OtherLosses"], ["기타비용", "기타손실", "기타영업외비용"]),
}
CF_METRICS = {
    "operating_cash_flow": (("CF",), ["ifrs-full_CashFlowsFromUsedInOperatingActivities"], ["영업활동현금흐름", "영업활동으로 인한 현금흐름"]),
    "interest_paid": (("CF",), ["ifrs-full_InterestPaidClassifiedAsOperatingActivities", "ifrs-full_InterestPaidClassifiedAsFinancingActivities"],
                      ["이자지급", "이자의 지급", "이자지급(영업)", "이자의지급"]),
    "interest_received": (("CF",), ["ifrs-full_InterestReceivedClassifiedAsOperatingActivities", "ifrs-full_InterestReceivedClassifiedAsInvestingActivities"],
                          ["이자수취", "이자의 수취", "이자의수취"]),
}
ABS_METRICS = {"interest_paid", "interest_received"}  # 현금흐름표 표시 부호가 회사마다 달라 절대값 사용
POINT_METRICS = {
    "total_assets": (("BS",), ["ifrs-full_Assets"], ["자산총계"]),
    "total_liabilities": (("BS",), ["ifrs-full_Liabilities"], ["부채총계"]),
    "total_equity": (("BS",), ["ifrs-full_Equity"], ["자본총계"]),
    "equity_owners": (("BS",), ["ifrs-full_EquityAttributableToOwnersOfParent"], []),
    "cash": (("BS",), ["ifrs-full_CashAndCashEquivalents"], ["현금및현금성자산"]),
}
DEBT_NAME = re.compile(r"차입금|사채|리스부채|유동성장기부채")
DEBT_EXCLUDE = re.compile(r"상환|증가|감소|이자|충당|파생|할인|할증|전환권|조정|미지급|보증")
STI_NAME = re.compile(r"단기금융상품|단기금융자산|단기예금")
DA_NAME = re.compile(r"감가상각비|무형자산상각비|사용권자산상각비|상각비")


def _num(v) -> float | None:
    if v is None:
        return None
    s = str(v).replace(",", "").strip()
    if s in ("", "-"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _pick(rows: list[dict], sj: tuple, ids: list[str], names: list[str], field: str) -> tuple[float | None, str | None]:
    for r in rows:
        if r.get("sj_div") in sj and r.get("account_id") in ids:
            v = _num(r.get(field))
            if v is not None:
                return v, f"{r.get('account_nm')}({r.get('account_id')})"
    for r in rows:
        if r.get("sj_div") in sj and (r.get("account_nm") or "").replace(" ", "") in [n.replace(" ", "") for n in names]:
            v = _num(r.get(field))
            if v is not None:
                return v, r.get("account_nm")
    return None, None


def _sum_matching(rows: list[dict], sj: tuple, pattern: re.Pattern, exclude: re.Pattern | None, field: str):
    total, items = 0.0, []
    for r in rows:
        nm = r.get("account_nm") or ""
        if r.get("sj_div") in sj and pattern.search(nm) and not (exclude and exclude.search(nm)):
            v = _num(r.get(field))
            if v:
                total += v
                items.append(f"{nm} {v:,.0f}")
    return (total if items else None), items


def report_due(year: int, q: int, today: date) -> bool:
    mm, dd = QUARTER_END[q].split("-")
    return today >= date(year, int(mm), int(dd)) + timedelta(days=FILING_LAG_DAYS[q] - 45)


def fetch_financials(db: Session, dart: DartClient, company: Company, today: date | None = None) -> dict:
    """Fetch missing / recent statements. Returns {"fetched": n, "reused": n, "nodata": n}."""
    today = today or date.today()
    stats = {"fetched": 0, "reused": 0, "nodata": 0}
    if not company.corp_code:
        return stats
    periods = [(y, code, q) for y in range(today.year - YEARS_BACK, today.year + 1) for code, q, _ in REPORTS
               if report_due(y, q, today)]
    recent = set(periods[-2:])  # re-check the two latest for late filings / corrections
    for y, code, q in periods:
        fetch = db.scalar(select(FinancialFetch).where(FinancialFetch.company_id == company.id,
                                                       FinancialFetch.bsns_year == y, FinancialFetch.reprt_code == code))
        fresh = fetch and (datetime.now(fetch.fetched_at.tzinfo) - fetch.fetched_at) < timedelta(days=1)
        if fetch and (fetch.status == "found" and ((y, code, q) not in recent or fresh)):
            stats["reused"] += 1
            continue
        if fetch and fetch.status == "nodata" and ((y, code, q) not in recent or fresh) and \
                today > date(y, 12, 31) + timedelta(days=200):
            stats["nodata"] += 1
            continue
        rows, fs_div = None, None
        for div in ("CFS", "OFS"):
            try:
                rows = dart.financial_statements(company.corp_code, y, code, div)
                fs_div = div
                break
            except DartNoData:
                continue
        status = "found" if rows else "nodata"
        if rows:
            rcept_no = rows[0].get("rcept_no", "")
            exists = db.scalar(select(FinancialReport.id).where(
                FinancialReport.company_id == company.id, FinancialReport.bsns_year == y,
                FinancialReport.reprt_code == code, FinancialReport.rcept_no == rcept_no))
            if not exists:
                db.add(FinancialReport(company_id=company.id, bsns_year=y, reprt_code=code, fs_div=fs_div,
                                       rcept_no=rcept_no, filed_date=rcept_no[:8], rows=rows))
                stats["fetched"] += 1
            else:
                stats["reused"] += 1
        else:
            stats["nodata"] += 1
        if fetch is None:
            fetch = FinancialFetch(company_id=company.id, bsns_year=y, reprt_code=code, status=status)
            db.add(fetch)
        fetch.status = status
        fetch.fetched_at = datetime.now().astimezone()
        db.commit()
    return stats


def latest_reports(db: Session, company_id: int) -> dict[tuple[int, str], FinancialReport]:
    out: dict[tuple[int, str], FinancialReport] = {}
    for r in db.scalars(select(FinancialReport).where(FinancialReport.company_id == company_id)
                        .order_by(FinancialReport.filed_date, FinancialReport.rcept_no)):
        out[(r.bsns_year, r.reprt_code)] = r  # later filing (correction) overrides
    return out


def normalize(db: Session, company: Company) -> list[dict]:
    """Rebuild financial_metrics for a company from its latest reports. Returns metric dicts."""
    reps = latest_reports(db, company.id)
    code_q = {c: q for c, q, _ in REPORTS}
    metrics: list[dict] = []

    def add(period_end, basis, metric, value, rep, derivation):
        metrics.append({"period_end": period_end, "basis": basis, "metric": metric, "value": value,
                        "fs_div": rep.fs_div, "rcept_no": rep.rcept_no, "filed_date": rep.filed_date,
                        "derivation": derivation})

    years = sorted({y for y, _ in reps})
    for y in years:
        by_q = {code_q[c]: reps[(y, c)] for (yy, c) in reps if yy == y}
        # --- point-in-time balance sheet
        for q, rep in by_q.items():
            pe = f"{y}-{QUARTER_END[q]}"
            for m, (sj, ids, names) in POINT_METRICS.items():
                v, src = _pick(rep.rows, sj, ids, names, "thstrm_amount")
                if v is not None:
                    add(pe, "POINT", m, v, rep, src)
            debt, items = _sum_matching(rep.rows, ("BS",), DEBT_NAME, DEBT_EXCLUDE, "thstrm_amount")
            add(pe, "POINT", "total_debt", debt or 0.0, rep, "차입금·사채·리스부채 합계: " + ("; ".join(items) or "해당 계정 없음"))
            sti, items = _sum_matching(rep.rows, ("BS",), STI_NAME, None, "thstrm_amount")
            add(pe, "POINT", "short_term_investments", sti or 0.0, rep, "단기금융상품: " + ("; ".join(items) or "해당 계정 없음"))
        # --- income statement 3-month values
        for m, (sj, ids, names) in FLOW_METRICS.items():
            q_vals: dict[int, tuple[float, str, FinancialReport]] = {}
            cum: dict[int, float] = {}
            for q, rep in by_q.items():
                if q == 4:
                    continue
                v, src = _pick(rep.rows, sj, ids, names, "thstrm_amount")
                c, _ = _pick(rep.rows, sj, ids, names, "thstrm_add_amount")
                if v is not None:
                    q_vals[q] = (v, f"{rep.reprt_code} 당기 3개월 {src}", rep)
                cum[q] = c if c is not None else (v if q == 1 else None)
            if 4 in by_q:
                rep = by_q[4]
                fy, src = _pick(rep.rows, sj, ids, names, "thstrm_amount")
                if fy is not None:
                    add(f"{y}-12-31", "FY", m, fy, rep, f"사업보고서 {src}")
                    q3cum = cum.get(3)
                    if q3cum is None and all(k in q_vals for k in (1, 2, 3)):
                        q3cum = sum(q_vals[k][0] for k in (1, 2, 3))
                    if q3cum is not None:
                        q_vals[4] = (fy - q3cum, f"연간 {fy:,.0f} − 3분기 누적 {q3cum:,.0f}", rep)
            for q, (v, deriv, rep) in q_vals.items():
                add(f"{y}-{QUARTER_END[q]}", "Q", m, v, rep, deriv)
        # --- cash flow (YTD → quarterly)
        for m in [*CF_METRICS, "dep_amort"]:
            sj, ids, names = CF_METRICS.get(m, (("CF",), [], []))
            ytd: dict[int, tuple[float, FinancialReport, str]] = {}
            for q, rep in by_q.items():
                if m == "dep_amort":
                    v, items = _sum_matching(rep.rows, ("CF",), DA_NAME, re.compile("누계|환입"), "thstrm_amount")
                    src = "; ".join(items)
                else:
                    v, src = _pick(rep.rows, sj, ids, names, "thstrm_amount")
                if v is not None and m in ABS_METRICS:
                    v = abs(v)
                if v is not None:
                    ytd[q] = (v, rep, src or "")
            for q in sorted(ytd):
                v, rep, src = ytd[q]
                if q == 1:
                    add(f"{y}-03-31", "Q", m, v, rep, f"1분기 누적 {src}")
                elif (q - 1) in ytd:
                    add(f"{y}-{QUARTER_END[q]}", "Q", m, v - ytd[q - 1][0], rep, f"누적 {v:,.0f} − 전분기 누적 {ytd[q - 1][0]:,.0f}")
                if q == 4:
                    add(f"{y}-12-31", "FY", m, v, rep, f"연간 {src}")

    db.execute(delete(FinancialMetric).where(FinancialMetric.company_id == company.id))
    for m in metrics:
        db.add(FinancialMetric(company_id=company.id, **m))
    db.commit()
    return metrics


def fetch_shares(dart: DartClient, company: Company, today: date | None = None) -> dict | None:
    """최근 정기보고서의 발행주식총수·자기주식수 (stockTotqySttus)."""
    today = today or date.today()
    if not company.corp_code:
        return None
    periods = [(y, code) for y in range(today.year - 1, today.year + 1) for code, q, _ in REPORTS if report_due(y, q, today)]
    for y, code in reversed(periods):
        try:
            rows = dart.share_status(company.corp_code, y, code)
        except DartNoData:
            continue
        except DartError:
            return None
        common = next((r for r in rows if (r.get("se") or "").strip() in ("보통주", "합계")), None)
        if common:
            return {"issued": _num(common.get("istc_totqy")), "treasury": _num(common.get("tesstk_co")) or 0.0,
                    "float": _num(common.get("distb_stock_co")), "bsns_year": y, "reprt_code": code,
                    "rcept_no": common.get("rcept_no"),
                    "note": next(((r.get("tesstk_co") or "").strip() for r in rows if (r.get("se") or "").strip() == "비고"), None)}
    return None
