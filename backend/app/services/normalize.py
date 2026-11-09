"""이익 조정·정상화·납기 기반 향후 매출 추정 (개선 1~3).

1) 경상순이익(TTM)  = (영업이익 + 이자수취 − 이자지급) × (1 − 유효세율)
   영업외 비경상 손익 = 지배순이익 − 경상순이익  (금융수익·금융원가·기타손익 내역을 함께 제시)
2) 정상 EBIT       = 최근 12개 분기 영업이익 합 ÷ 3   (수주형 업종의 사이클 평균)
   정상 순이익      = (정상 EBIT + 연평균 순이자) × (1 − 유효세율)
3) 향후 12개월 매출 = 납기(계약 종료일)가 [기준일, 기준일+12개월]에 속하는 수주잔고의 합
   - 장비 매출은 납품·검수 시점에 몰려 인식된다고 가정(분기 매출 변동성으로 근거 제시)
   - 공식 수주잔고(정기보고서) − 금액 공시 계약의 잔여액 = 금액 비공개 잔고 → 비공개 진행 계약에 균등 배분
   - 시나리오: 보수 = 납기 6개월 지연, 기준 = 공시 납기, 낙관 = 3개월 조기
유효세율 = 최근 12개 분기 법인세 ÷ 세전이익, 5~35% 밖이면 22%(법정세율 근사) 사용.
"""
from __future__ import annotations

import statistics
from datetime import date, timedelta

from app.services.engine_data import FinSeries, prev_quarter

DEFAULT_TAX = 0.22
NORMAL_QUARTERS = 12
SHIFT_DAYS = {"conservative": 182, "base": 0, "optimistic": -91}


def sum_q(fs: FinSeries, metric: str, pe: str, n: int, filed_by: str | None = None) -> float | None:
    vals = []
    for k in range(n):
        r = fs.q.get(metric, {}).get(prev_quarter(pe, k))
        if r is None or r.value is None or (filed_by and r.filed_date > filed_by):
            return None
        vals.append(r.value)
    return float(sum(vals))


def sum_q_lenient(fs: FinSeries, metric: str, pe: str, n: int, filed_by: str | None = None) -> float:
    """이자 항목처럼 없으면 0으로 볼 수 있는 계정용."""
    tot = 0.0
    for k in range(n):
        r = fs.q.get(metric, {}).get(prev_quarter(pe, k))
        if r is not None and r.value is not None and not (filed_by and r.filed_date > filed_by):
            tot += r.value
    return tot


def tax_rate(fs: FinSeries, pe: str, filed_by: str | None = None) -> tuple[float, str]:
    pbt = sum_q(fs, "pbt", pe, NORMAL_QUARTERS, filed_by)
    tax = sum_q(fs, "income_tax", pe, NORMAL_QUARTERS, filed_by)
    if pbt and pbt > 0 and tax is not None:
        r = tax / pbt
        if 0.05 <= r <= 0.35:
            return r, f"최근 12개 분기 법인세 {tax:,.0f} ÷ 세전이익 {pbt:,.0f} = {r:.1%}"
        return DEFAULT_TAX, f"최근 12개 분기 유효세율 {r:.1%}가 5~35% 범위 밖 → 법정세율 근사 {DEFAULT_TAX:.0%} 사용"
    return DEFAULT_TAX, f"세전이익 누계가 0 이하이거나 법인세 데이터 없음 → 법정세율 근사 {DEFAULT_TAX:.0%} 사용"


def core_earnings(fs: FinSeries, pe: str, filed_by: str | None = None) -> dict:
    """TTM 경상순이익과 영업외 손익 분해."""
    op = fs.ttm("operating_income", pe, filed_by)
    if op is None:
        return {}
    t, t_note = tax_rate(fs, pe, filed_by)
    ir = sum_q_lenient(fs, "interest_received", pe, 4, filed_by)
    ip = sum_q_lenient(fs, "interest_paid", pe, 4, filed_by)
    core = (op + ir - ip) * (1 - t)
    ni = fs.ttm("net_income_owners", pe, filed_by)
    if ni is None:
        ni = fs.ttm("net_income", pe, filed_by)
    pbt = fs.ttm("pbt", pe, filed_by)
    out = {"core_ni_ttm": core, "tax_rate": t, "tax_note": t_note, "interest_received_ttm": ir, "interest_paid_ttm": ip,
           "net_interest_ttm": ir - ip, "reported_ni_ttm": ni, "pbt_ttm": pbt,
           "non_operating_ttm": (pbt - op) if pbt is not None else None,
           "non_recurring_gap": (ni - core) if ni is not None else None}
    for m in ("finance_income", "finance_costs", "other_gains", "other_losses"):
        out[f"{m}_ttm"] = fs.ttm(m, pe, filed_by)
    return out


def normalized(fs: FinSeries, pe: str, filed_by: str | None = None) -> dict:
    op12 = sum_q(fs, "operating_income", pe, NORMAL_QUARTERS, filed_by)
    rev12 = sum_q(fs, "revenue", pe, NORMAL_QUARTERS, filed_by)
    if op12 is None or rev12 is None:
        return {}
    t, _ = tax_rate(fs, pe, filed_by)
    ni_int = (sum_q_lenient(fs, "interest_received", pe, NORMAL_QUARTERS, filed_by)
              - sum_q_lenient(fs, "interest_paid", pe, NORMAL_QUARTERS, filed_by)) / 3
    ebit_n = op12 / 3
    return {"ebit_norm": ebit_n, "revenue_norm": rev12 / 3, "margin_norm": op12 / rev12 if rev12 else None,
            "ni_core_norm": (ebit_n + ni_int) * (1 - t), "net_interest_norm": ni_int,
            "window": f"{prev_quarter(pe, NORMAL_QUARTERS - 1)[:7]}~{pe[:7]}"}


def revenue_lumpiness(fs: FinSeries, pe: str, n: int = 8) -> float | None:
    """분기 매출 변동계수(표준편차/평균) — 납품 시점 인식(점 인식) 가정의 근거."""
    vals = [fs.qval("revenue", prev_quarter(pe, k)) for k in range(n)]
    vals = [v for v in vals if v is not None]
    if len(vals) < 4 or statistics.mean(vals) <= 0:
        return None
    return statistics.pstdev(vals) / statistics.mean(vals)


def forward_schedule(book: dict, official: dict | None, today: date) -> dict:
    """납기 기반 향후 12개월 매출 추정. official: 정기보고서 수주잔고(amount, as_of 'YYYY-MM', last_delivery)."""
    if not official:
        return {"available": False, "reason": "정기보고서 수주잔고(산식 검증된 값)가 없어 납기 기반 추정 불가"}
    y, m = official["as_of"].split("-")[:2]
    d0 = date(int(y), int(m), 28) + timedelta(days=4)
    d0 = d0 - timedelta(days=d0.day)  # 보고 기준 월말
    last_delivery = date.fromisoformat(official["last_delivery"]) if official.get("last_delivery") else None
    pieces, notes = [], []
    disclosed_rem = 0.0
    undisclosed = []
    for c in book["contracts"]:
        if c["status"] != "active":
            continue
        start = date.fromisoformat(c["start_date"]) if c.get("start_date") else None
        end = date.fromisoformat(c["end_date"]) if c.get("end_date") else None
        if end is not None and end < d0:
            continue  # 보고 기준일 이전 납기 — 잔고에 없음
        if c["amount_disclosed"] and c["amount"]:
            if start and end and end > start:
                rem = c["amount"] * min(1.0, max(0.0, (end - max(d0, start)).days / (end - start).days))
            else:
                rem = c["amount"]
            disclosed_rem += rem
            pieces.append({"label": c["counterparty"] or c["description"] or "계약", "amount": rem, "end": end,
                           "disclosed": True, "rcept_no": c["latest_rcept_no"], "contract_date": c["contract_date"]})
        else:
            undisclosed.append(c)
    pool = max(0.0, official["amount"] - disclosed_rem)
    if pool > 0 and undisclosed:
        share = pool / len(undisclosed)
        for c in undisclosed:
            end = date.fromisoformat(c["end_date"]) if c.get("end_date") else last_delivery
            pieces.append({"label": c["counterparty"] or c["description"] or "비공개 계약", "amount": share, "end": end,
                           "disclosed": False, "rcept_no": c["latest_rcept_no"], "contract_date": c["contract_date"]})
        notes.append(f"금액 비공개 잔고 {pool:,.0f}원(공식 수주잔고 − 공시 계약 잔여액)을 비공개 진행 계약 {len(undisclosed)}건에 균등 배분(가정)")
    elif pool > 0:
        pieces.append({"label": "계약 미특정 잔고", "amount": pool, "end": last_delivery, "disclosed": False, "rcept_no": None,
                       "contract_date": None})
        notes.append(f"공시 계약으로 설명되지 않는 잔고 {pool:,.0f}원은 수주상황 표의 최종 납기({last_delivery})에 인식된다고 가정")
    for p in pieces:
        if p["end"] is None:
            p["end"] = last_delivery or (today + timedelta(days=365))
            p["end_assumed"] = True
    horizon = (today, today + timedelta(days=365))
    scen = {}
    for s, shift in SHIFT_DAYS.items():
        tot, already = 0.0, 0.0
        for p in pieces:
            e = p["end"] + timedelta(days=shift)
            if p["end"] >= today and e < today:
                e = today  # 아직 납품 전인 계약은 앞당겨도 '이미 인식됨'으로 보지 않음
            if horizon[0] <= e <= horizon[1]:
                tot += p["amount"]
            elif e < horizon[0]:
                already += p["amount"]
        scen[s] = {"revenue": tot, "recognized_before_today": already}
    return {
        "available": True, "as_of": d0.isoformat(), "official_backlog": official["amount"], "disclosed_remaining": disclosed_rem,
        "undisclosed_pool": pool, "undisclosed_contracts": len(undisclosed), "horizon": [horizon[0].isoformat(), horizon[1].isoformat()],
        "scenarios": scen, "notes": notes,
        "pieces": [{**p, "end": p["end"].isoformat() if p["end"] else None} for p in sorted(pieces, key=lambda x: x["end"] or date.max)],
        "rule": "납기(계약 종료일)에 매출 인식 가정 · 보수=납기 6개월 지연 · 낙관=3개월 조기",
    }
