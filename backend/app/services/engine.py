"""가치평가 계산 엔진 — 모든 수치 계산은 여기서만 한다 (LLM은 해석·서술만).

평가 방법 (독립 검토 후 가중 결합)
 A. forward  : 수주잔고 → 향후 12개월 매출 전환 가정 × 영업이익률 시나리오 × EV/EBIT
 B. peer     : 비교기업(적정가치 비교용·상장·흑자) EV/EBIT, PER 분포(하위/중앙/상위)
 C. history  : 자사 과거 3년 월말 EV/EBIT·PER 분포(공시 시점 기준 TTM, 미래정보 미사용)
지분가치 = EV − 순차입금(차입금·사채·리스 − 현금 − 단기금융상품) − 비지배지분
주당가치 = 지분가치 ÷ (상장주식수 − 자기주식수)
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field

import numpy as np
import pandas as pd


from app.services.engine_data import FinSeries, prev_quarter

MODEL_VERSION = "valuescope-engine-1.0"
SCENARIOS = ("conservative", "base", "optimistic")
SCENARIO_LABEL = {"conservative": "보수적", "base": "기준", "optimistic": "낙관적"}
METHOD_LABEL = {"forward": "A. 미래이익(수주) 기반", "peer": "B. 동종업계 상대평가", "history": "C. 자사 과거 밸류에이션"}
METHOD_WEIGHT = {"peer": 0.4, "history": 0.3, "forward": 0.3}
BACKLOG_CONVERSION = {"conservative": 0.5, "base": 0.7, "optimistic": 0.9}
# 배수 분포의 상위/하위 비율이 이 값을 넘으면 그 배수는 비교 근거로 불안정하다고 보고 제외한다.
MAX_MULTIPLE_DISPERSION = 3.0
# 결합 적정주가 범위(낙관/보수)가 이 값을 넘으면 판단 유보.
MAX_RANGE_RATIO = 3.0
# 방법별 기준값의 최대/최소 비율이 이 값을 넘으면 방법 간 결론 불일치 → 판단 유보.
MAX_METHOD_DISAGREEMENT = 3.0
EARNINGS_KEYS = {"ev_ebit_norm", "pe_norm", "ev_ebit", "per", "per_adj"}
# 고객사 TTM 영업이익 ÷ 정상(5년 평균) EBIT 가 이 값 이상이면 사업 규모가 구조적으로 커진 것으로 보고
# 상대평가 이익 기준을 TTM으로 전환한다 (5년 평균에 비교기업 배수를 곱하면 과소평가되므로).
SCALE_SWITCH_RATIO = 2.0
PEER_PLANS = {
    "normalized": [("ev_ebit_norm", "ebit_norm", "EV/정상EBIT", "ev"), ("pe_norm", "ni_core_norm", "정상 P/E", "eq")],
    "ttm": [("ev_ebit", "operating_income_ttm", "EV/EBIT(TTM)", "ev"), ("per_adj", "core_ni_ttm", "조정 P/E(TTM)", "eq")],
}


def dispersion(qs: dict, vals: list[float] | None = None) -> float | None:
    """분포 분산도. 표본 6개 미만이면 4분위가 보간으로 좁아지므로 최대/최소로 판정한다."""
    if vals is not None and len(vals) < 6:
        lo, hi = min(vals), max(vals)
        return hi / lo if lo > 0 else None
    return qs["optimistic"] / qs["conservative"] if qs["conservative"] > 0 else None


def fmt_krw(v: float | None) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "데이터 없음"
    a = abs(v)
    if a >= 1e12:
        return f"{v / 1e12:,.2f}조원"
    if a >= 1e8:
        return f"{v / 1e8:,.0f}억원"
    return f"{v:,.0f}원"


def fmt_x(v): return "N/A" if v is None else f"{v:,.1f}배"
def fmt_pct(v): return "N/A" if v is None else f"{v * 100:,.1f}%"


# ------------------------------------------------------------------ evidence registry
class Evidence:
    def __init__(self) -> None:
        self.items: list[dict] = []

    def add(self, kind: str, label: str, value=None, unit=None, display=None, period=None, as_of=None,
            company=None, method=None, source=None, url=None, rcept_no=None, impact=None, detail=None) -> str:
        eid = f"E{len(self.items) + 1}"
        self.items.append({
            "eid": eid, "kind": kind, "label": label,
            "value": None if value is None or (isinstance(value, float) and math.isnan(value)) else float(value),
            "unit": unit, "display": display, "period": period, "as_of": as_of,
            "company_id": company.id if company else None, "company_name": company.name if company else None,
            "method": method, "source": source, "url": url, "rcept_no": rcept_no, "impact": impact,
            "detail": detail or {},
        })
        return eid


def dart_url(rcept_no: str | None) -> str | None:
    return f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}" if rcept_no else None


# ------------------------------------------------------------------ metrics
@dataclass
class CompanyMetrics:
    company_id: int
    name: str
    values: dict = field(default_factory=dict)
    na: dict = field(default_factory=dict)  # metric -> reason
    eids: dict = field(default_factory=dict)
    weight: float = 1.0  # 비교군 가중치 (적합성 점수 × 활용 구분)


def compute_metrics(fs: FinSeries, px: dict, shares: dict | None, ev: Evidence, as_of: str) -> CompanyMetrics:
    """px: {"price", "mcap", "listed_shares", "date"} from KRX (may be empty)."""
    c = fs.company
    m = CompanyMetrics(c.id, c.name)
    v, na, e = m.values, m.na, m.eids
    lq = fs.latest_quarter()
    v["latest_quarter"] = lq
    if lq is None:
        na["financials"] = "DART 분기 재무제표를 4개 분기 이상 확보하지 못해 TTM 계산 불가"
    else:
        for key, label in [("revenue", "매출액"), ("operating_income", "영업이익"), ("net_income_owners", "지배주주순이익"),
                           ("net_income", "당기순이익"), ("operating_cash_flow", "영업활동현금흐름"), ("dep_amort", "감가상각비")]:
            val = fs.ttm(key, lq)
            if key == "net_income_owners" and val is None:
                val = fs.ttm("net_income", lq)
                if val is not None:
                    na["net_income_owners_note"] = "지배주주순이익 계정 없음 → 당기순이익 사용(별도재무제표 등)"
            v[f"{key}_ttm"] = val
            if val is None:
                na[f"{key}_ttm"] = f"{label} 최근 4개 분기 데이터 부족"
                continue
            srcs = fs.ttm_sources(key, lq)
            e[f"{key}_ttm"] = ev.add("calc", f"{c.name} {label}(TTM)", val, "KRW", fmt_krw(val),
                                     period=f"{prev_quarter(lq, 3)[:7]}~{lq[:7]} (4개 분기 합)", as_of=lq, company=c,
                                     method="최근 4개 분기 3개월 실적 합계(4분기=연간−3분기 누적)", source="OpenDART 재무제표",
                                     url=dart_url(srcs[-1] if srcs else None), rcept_no=srcs[-1] if srcs else None,
                                     detail={"rcept_nos": srcs})
        for key, label in [("cash", "현금및현금성자산"), ("short_term_investments", "단기금융상품"), ("total_debt", "차입금·사채·리스부채"),
                           ("total_equity", "자본총계"), ("equity_owners", "지배주주지분")]:
            row = fs.pt_row(key, lq)
            val = row.value if row else None
            if key == "equity_owners" and val is None:
                val = fs.pt("total_equity", lq)
            v[key] = val
            if row is not None:
                e[key] = ev.add("fact", f"{c.name} {label}", val, "KRW", fmt_krw(val), period=f"{lq} 말", as_of=lq,
                                company=c, method=row.derivation, source="OpenDART 재무제표", url=dart_url(row.rcept_no),
                                rcept_no=row.rcept_no)
        v["minority_interest"] = (v["total_equity"] - v["equity_owners"]) if v.get("total_equity") is not None and v.get("equity_owners") is not None else 0.0
        if v.get("total_debt") is not None and v.get("cash") is not None:
            v["net_debt"] = v["total_debt"] - v["cash"] - (v.get("short_term_investments") or 0.0)
            e["net_debt"] = ev.add("calc", f"{c.name} 순차입금", v["net_debt"], "KRW", fmt_krw(v["net_debt"]),
                                   period=f"{lq} 말", as_of=lq, company=c,
                                   method="차입금·사채·리스부채 − 현금및현금성자산 − 단기금융상품 (음수=순현금)",
                                   source="OpenDART 재무제표", detail={"inputs": [e.get("total_debt"), e.get("cash")]})
        # growth (TTM vs TTM 4 quarters earlier)
        lq4 = prev_quarter(lq, 4)
        for key in ("revenue", "operating_income"):
            now_, prev_ = v.get(f"{key}_ttm"), fs.ttm(key, lq4)
            if now_ is not None and prev_ and prev_ > 0:
                v[f"{key}_growth"] = now_ / prev_ - 1
                e[f"{key}_growth"] = ev.add("calc", f"{c.name} {'매출' if key == 'revenue' else '영업이익'} 성장률(TTM YoY)",
                                            v[f"{key}_growth"], "ratio", fmt_pct(v[f"{key}_growth"]),
                                            period=f"{lq4[:7]} → {lq[:7]}", as_of=lq, company=c,
                                            method=f"TTM({lq[:7]}) ÷ TTM({lq4[:7]}) − 1", source="OpenDART 재무제표")
            else:
                na[f"{key}_growth"] = "비교 기간 데이터 없음 또는 전년 적자"
        if v.get("revenue_ttm"):
            if v.get("operating_income_ttm") is not None:
                v["op_margin"] = v["operating_income_ttm"] / v["revenue_ttm"]
                e["op_margin"] = ev.add("calc", f"{c.name} 영업이익률(TTM)", v["op_margin"], "ratio", fmt_pct(v["op_margin"]),
                                        period=f"TTM {lq[:7]}", as_of=lq, company=c, method="영업이익(TTM) ÷ 매출액(TTM)",
                                        source="OpenDART 재무제표")
        eq_prev = fs.pt("equity_owners", lq4) or fs.pt("total_equity", lq4)
        if v.get("net_income_owners_ttm") is not None and v.get("equity_owners") and eq_prev:
            v["roe"] = v["net_income_owners_ttm"] / ((v["equity_owners"] + eq_prev) / 2)
            e["roe"] = ev.add("calc", f"{c.name} ROE(TTM)", v["roe"], "ratio", fmt_pct(v["roe"]), period=f"TTM {lq[:7]}",
                              as_of=lq, company=c, method="지배순이익(TTM) ÷ 평균 지배주주지분", source="OpenDART 재무제표")

    # ---- 1) 경상순이익(TTM) · 2) 정상화 이익
    if lq is not None:
        from app.services.normalize import core_earnings, normalized

        ce = core_earnings(fs, lq)
        if ce:
            v.update({k: ce[k] for k in ce if k != "tax_note"})
            v["tax_note"] = ce["tax_note"]
            e["tax_rate"] = ev.add("calc", f"{c.name} 적용 세율", ce["tax_rate"], "ratio", fmt_pct(ce["tax_rate"]), as_of=lq, company=c,
                                   method=ce["tax_note"], source="OpenDART 재무제표")
            e["core_ni_ttm"] = ev.add("calc", f"{c.name} 경상순이익(TTM)", ce["core_ni_ttm"], "KRW", fmt_krw(ce["core_ni_ttm"]),
                                      period=f"TTM {lq[:7]}", as_of=lq, company=c,
                                      method=f"(영업이익 {fmt_krw(v.get('operating_income_ttm'))} + 이자수취 {fmt_krw(ce['interest_received_ttm'])}"
                                             f" − 이자지급 {fmt_krw(ce['interest_paid_ttm'])}) × (1 − 세율 {ce['tax_rate']:.1%})",
                                      source="OpenDART 재무제표(손익계산서·현금흐름표)", impact="일회성·평가성 영업외 손익을 제외한 이익 기준",
                                      detail={k: ce[k] for k in ("interest_received_ttm", "interest_paid_ttm", "tax_rate")})
            if ce.get("non_recurring_gap") is not None:
                e["non_recurring_gap"] = ev.add(
                    "calc", f"{c.name} 영업외 비경상 손익(TTM, 세후 근사)", ce["non_recurring_gap"], "KRW", fmt_krw(ce["non_recurring_gap"]),
                    period=f"TTM {lq[:7]}", as_of=lq, company=c,
                    method=f"지배순이익 {fmt_krw(ce['reported_ni_ttm'])} − 경상순이익 {fmt_krw(ce['core_ni_ttm'])}. "
                           f"영업외 내역(TTM): 금융수익 {fmt_krw(ce.get('finance_income_ttm'))}, 금융원가 {fmt_krw(ce.get('finance_costs_ttm'))}, "
                           f"기타수익 {fmt_krw(ce.get('other_gains_ttm'))}, 기타비용 {fmt_krw(ce.get('other_losses_ttm'))}, "
                           f"세전 영업외손익 {fmt_krw(ce.get('non_operating_ttm'))}",
                    source="OpenDART 재무제표", impact="보고 PER과 조정 PER의 차이 원인",
                    detail={k: ce.get(k) for k in ("finance_income_ttm", "finance_costs_ttm", "other_gains_ttm", "other_losses_ttm",
                                                   "non_operating_ttm", "reported_ni_ttm")})
        nm = normalized(fs, lq)
        if nm:
            v.update({k: nm[k] for k in ("ebit_norm", "revenue_norm", "margin_norm", "ni_core_norm", "net_interest_norm", "quarters")})
            v["norm_quarters"] = nm["quarters"]
            v["norm_window"] = nm["window"]
            e["ebit_norm"] = ev.add("calc", f"{c.name} 정상 EBIT({nm['quarters']}개 분기 연평균)", nm["ebit_norm"], "KRW", fmt_krw(nm["ebit_norm"]),
                                    period=nm["window"], as_of=lq, company=c,
                                    method=f"최근 {nm['quarters']}개 분기 영업이익 합 ÷ {nm['quarters'] / 4:g}년 (수주 사이클 평균, 최대 5년)",
                                    source="OpenDART 재무제표", impact="상대·과거 평가의 이익 기준")
            e["margin_norm"] = ev.add("calc", f"{c.name} 영업이익률({nm['quarters']}개 분기 평균)", nm["margin_norm"], "ratio", fmt_pct(nm["margin_norm"]),
                                      period=nm["window"], as_of=lq, company=c, method=f"{nm['quarters']}개 분기 영업이익 합 ÷ 같은 기간 매출 합",
                                      source="OpenDART 재무제표")
            e["ni_core_norm"] = ev.add("calc", f"{c.name} 정상 순이익", nm["ni_core_norm"], "KRW", fmt_krw(nm["ni_core_norm"]),
                                       period=nm["window"], as_of=lq, company=c,
                                       method="(정상 EBIT + 연평균 순이자) × (1 − 적용 세율)", source="OpenDART 재무제표")
        else:
            na["ebit_norm"] = "연속 12개 분기 이상의 재무 이력이 없어 정상화 이익 산출 불가"

    # ---- market data
    if px.get("price"):
        v["price"], v["market_cap"], v["listed_shares"], v["price_date"] = px["price"], px["mcap"], px["listed_shares"], px["date"]
        e["price"] = ev.add("fact", f"{c.name} 종가", px["price"], "KRW/주", f"{px['price']:,.0f}원", as_of=px["date"],
                            company=c, source="KRX 일별매매정보", method="KRX Open API 일별매매정보 종가")
        e["market_cap"] = ev.add("fact", f"{c.name} 시가총액", px["mcap"], "KRW", fmt_krw(px["mcap"]), as_of=px["date"],
                                 company=c, source="KRX 일별매매정보")
        treasury = (shares or {}).get("treasury") or 0.0
        v["shares_out"] = px["listed_shares"] - treasury if px.get("listed_shares") else None
        v["treasury_shares"] = treasury
        if shares:
            e["shares_out"] = ev.add("calc", f"{c.name} 유통 기준 주식수", v["shares_out"], "주", f"{v['shares_out']:,.0f}주",
                                     as_of=px["date"], company=c,
                                     method=f"KRX 상장주식수 {px['listed_shares']:,.0f} − 자기주식 {treasury:,.0f} "
                                            f"(자기주식: {shares.get('bsns_year')} {shares.get('reprt_code')} 주식총수현황)",
                                     source="KRX·OpenDART", rcept_no=shares.get("rcept_no"), url=dart_url(shares.get("rcept_no")),
                                     detail={"note": shares.get("note")})
    else:
        na["price"] = "KRX 시세 없음 (비상장·상장폐지 또는 수집 실패)"

    # ---- multiples
    mc = v.get("market_cap")
    if mc and lq:
        ev_val = mc + (v.get("net_debt") or 0.0) + (v.get("minority_interest") or 0.0) if v.get("net_debt") is not None else None
        v["ev"] = ev_val
        if ev_val is not None:
            e["ev"] = ev.add("calc", f"{c.name} EV(기업가치)", ev_val, "KRW", fmt_krw(ev_val), as_of=px["date"], company=c,
                             method="시가총액 + 순차입금 + 비지배지분", source="KRX·OpenDART",
                             detail={"inputs": [e.get("market_cap"), e.get("net_debt")]})

        def mult(key, num, den, label, reason_neg):
            if num is None or den is None:
                na[key] = "입력값 데이터 없음"
                return
            if den <= 0:
                na[key] = reason_neg
                return
            v[key] = num / den
            e[key] = ev.add("calc", f"{c.name} {label}", v[key], "x", fmt_x(v[key]), as_of=px["date"], company=c,
                            method=f"{label} = {'EV' if key.startswith('ev_') else '시가총액'} ÷ 기준지표(TTM/{lq[:7]})",
                            source="KRX·OpenDART")

        mult("per", mc, v.get("net_income_owners_ttm"), "PER(TTM)", "TTM 지배순이익 적자 → 음수 PER은 비교하지 않음")
        mult("pbr", mc, v.get("equity_owners"), "PBR", "자본잠식")
        mult("psr", mc, v.get("revenue_ttm"), "PSR(TTM)", "매출 없음")
        mult("ev_ebit", ev_val, v.get("operating_income_ttm"), "EV/EBIT(TTM)", "TTM 영업적자 → EV/EBIT 산출 불가")
        if v.get("dep_amort_ttm"):
            mult("ev_ebitda", ev_val, (v.get("operating_income_ttm") or 0) + v["dep_amort_ttm"], "EV/EBITDA(TTM)", "EBITDA 적자")
        else:
            na["ev_ebitda"] = "공시 현금흐름표에서 감가상각비 계정을 확인하지 못해 EBITDA 산출 불가"
        mult("ev_sales", ev_val, v.get("revenue_ttm"), "EV/Sales(TTM)", "매출 없음")
        mult("per_adj", mc, v.get("core_ni_ttm"), "조정 PER(경상순이익 TTM)", "TTM 경상순이익 적자")
        mult("ev_ebit_norm", ev_val, v.get("ebit_norm"), "EV/정상EBIT", "정상 EBIT 0 이하 또는 12개 분기 이력 부족")
        mult("pe_norm", mc, v.get("ni_core_norm"), "정상 P/E", "정상 순이익 0 이하 또는 12개 분기 이력 부족")
    # 정상 ROE — 비교군 품질 백분위(수익성) 산정에 사용
    if v.get("ni_core_norm") is not None and v.get("equity_owners") and v["equity_owners"] > 0:
        v["roe_norm"] = v["ni_core_norm"] / v["equity_owners"]
        e["roe_norm"] = ev.add("calc", f"{c.name} 정상 ROE", v["roe_norm"], "ratio", fmt_pct(v["roe_norm"]), as_of=lq, company=c,
                               method="정상 순이익 ÷ 지배주주지분", source="OpenDART 재무제표")

    na["forward_per"] = "신뢰 가능한 공식 전망치(컨센서스)를 확보하지 않아 미산출"
    return m


def quarter_table(fs: FinSeries, n: int = 12) -> list[dict]:
    qs = fs.quarters("revenue")[-n:]
    out = []
    for pe in qs:
        rev, op = fs.qval("revenue", pe), fs.qval("operating_income", pe)
        out.append({"period": pe[:7], "revenue": rev, "operating_income": op, "net_income": fs.qval("net_income_owners", pe) or fs.qval("net_income", pe),
                    "op_margin": (op / rev) if rev and op is not None else None,
                    "rcept_no": fs.q["revenue"][pe].rcept_no})
    return out


# ------------------------------------------------------------------ valuation methods
def _quantiles(vals: list[float]) -> dict[str, float]:
    vals = sorted(vals)
    if len(vals) < 4:
        return {"conservative": vals[0], "base": statistics.median(vals), "optimistic": vals[-1]}
    q = np.percentile(vals, [25, 50, 75])
    return {"conservative": float(q[0]), "base": float(q[1]), "optimistic": float(q[2])}


def _equity_from_ev(ev_val: float, mv: dict) -> float:
    return ev_val - (mv.get("net_debt") or 0.0) - (mv.get("minority_interest") or 0.0)


def _per_share(equity: float | None, mv: dict) -> float | None:
    so = mv.get("shares_out")
    if equity is None or not so:
        return None
    return max(equity, 0.0) / so


OUTLIER_FACTOR = 2.5   # 가중 중앙값 대비 2.5배 밖이면 이상치
MIN_PEERS_AFTER_TRIM = 3
QUALITY_METRICS = [("margin_norm", "정상 영업이익률"), ("roe_norm", "정상 ROE"), ("revenue_growth", "매출 성장률(TTM)")]
QUALITY_METRICS_TTM = [("op_margin", "영업이익률(TTM)"), ("roe", "ROE(TTM)"), ("revenue_growth", "매출 성장률(TTM)")]


def _wquantile(vals: list[float], weights: list[float], q: float) -> float:
    """가중 분위수 (선형 보간)."""
    pairs = sorted(zip(vals, weights))
    tot = sum(w for _, w in pairs)
    cum, pts = 0.0, []
    for v, w in pairs:
        pts.append(((cum + w / 2) / tot, v))
        cum += w
    if q <= pts[0][0]:
        return pts[0][1]
    if q >= pts[-1][0]:
        return pts[-1][1]
    for (q0, v0), (q1, v1) in zip(pts, pts[1:]):
        if q0 <= q <= q1:
            return v0 + (v1 - v0) * (q - q0) / (q1 - q0) if q1 > q0 else v1
    return pts[-1][1]


def quality_percentile(target: CompanyMetrics, peers: list[CompanyMetrics], metrics=None) -> dict:
    """고객사가 비교군 안에서 몇 번째 수준인지(가중 백분위). 지표별 + 평균."""
    out = {"metrics": {}, "percentile": None}
    ps = []
    for key, label in (metrics or QUALITY_METRICS):
        tv = target.values.get(key)
        pv = [(p.values.get(key), p.weight) for p in peers if p.values.get(key) is not None]
        if tv is None or len(pv) < 2:
            continue
        tot = sum(w for _, w in pv)
        below = sum(w for v, w in pv if v < tv) + 0.5 * sum(w for v, w in pv if v == tv)
        pct = below / tot
        out["metrics"][key] = {"label": label, "target": tv, "percentile": pct,
                               "peer_median": _wquantile([v for v, _ in pv], [w for _, w in pv], 0.5),
                               "peers": [{"name": p.name, "value": p.values.get(key)} for p in peers if p.values.get(key) is not None]}
        ps.append(pct)
    if ps:
        out["percentile"] = sum(ps) / len(ps)
    return out


def _peer_multiples(plans, target: CompanyMetrics, peers: list[CompanyMetrics], p_used: float, res: dict):
    """비교군 배수 산출(가중치·이상치 제거·품질 백분위). (used, low_rel) 반환. notes/excluded는 res에 기록."""
    mv = target.values
    used, low_rel = [], False
    for key, base_key, label, kind in plans:
        base = mv.get(base_key)
        raw = [(p.name, p.values[key], p.weight, p.eids.get(key)) for p in peers if p.values.get(key) is not None and p.values[key] > 0]
        if base is None or base <= 0:
            res["notes"].append(f"{label}: 고객사 기준지표가 0 이하 또는 없음 → 적용 불가")
            continue
        if len(raw) < MIN_PEERS_AFTER_TRIM:
            res["notes"].append(f"{label}: 유효 비교기업 {len(raw)}개(3개 미만) → 적용 불가")
            continue
        med = _wquantile([x[1] for x in raw], [x[2] for x in raw], 0.5)
        kept = [x for x in raw if med / OUTLIER_FACTOR <= x[1] <= med * OUTLIER_FACTOR]
        dropped = [x for x in raw if x not in kept]
        if dropped:
            res["notes"].append(f"{label}: 이상치 제외(가중 중앙값 {med:.1f}배의 {OUTLIER_FACTOR}배 밖) — "
                                + ", ".join(f"{n} {v:.1f}" for n, v, _, _ in dropped))
        if len(kept) < MIN_PEERS_AFTER_TRIM:
            res["notes"].append(f"{label}: 이상치 제외 후 비교기업 {len(kept)}개(3개 미만) → 적용 불가")
            res.setdefault("excluded", []).append({"multiple": label, "quantiles": {"conservative": med, "base": med, "optimistic": med},
                                                   "dispersion": None, "peers": [{"name": n, "value": v} for n, v, _, _ in raw]})
            continue
        vals, ws = [x[1] for x in kept], [x[2] for x in kept]
        qs = {"conservative": _wquantile(vals, ws, max(0.0, p_used - 0.25)), "base": _wquantile(vals, ws, p_used),
              "optimistic": _wquantile(vals, ws, min(1.0, p_used + 0.25))}
        dsp = dispersion({"conservative": _wquantile(vals, ws, 0.25), "optimistic": _wquantile(vals, ws, 0.75)}, vals)
        reliable = dsp is not None and dsp <= MAX_MULTIPLE_DISPERSION
        if not reliable:
            low_rel = True
            res["notes"].append(f"{label}: 이상치 제외 후에도 분산 {dsp or 0:.1f}배(>{MAX_MULTIPLE_DISPERSION:.0f}배) — 사용하되 신뢰도 낮음")
        per = {}
        for s in SCENARIOS:
            if kind == "ev":
                evv = qs[s] * base
                eq = _equity_from_ev(evv, mv)
            else:
                evv, eq = None, qs[s] * base
            per[s] = {"multiple": qs[s], "base_metric": base, "enterprise_value": evv, "equity_value": eq, "per_share": _per_share(eq, mv)}
        used.append({"multiple": label, "key": key, "fallback": False, "dispersion": dsp, "reliable": reliable,
                     "peers": [{"name": n, "value": v, "weight": w, "eid": i} for n, v, w, i in kept],
                     "dropped": [{"name": n, "value": v} for n, v, _, _ in dropped],
                     "weighted_median": med, "quantiles": qs, "results": per, "base_eid": target.eids.get(base_key)})
    return used, low_rel


def method_peer(target: CompanyMetrics, peers: list[CompanyMetrics], ev: Evidence) -> dict:
    """B. 동종업계 상대평가 (비교군 활용 규칙 v2).

    1) 비교군 가중치 = STEP 1 적합성 점수(없으면 50) × 활용구분(적정가치 비교용 1.0 · 산업 참고용 0.5)
    2) 배수: EV/정상EBIT, 정상 P/E  (PBR÷ROE는 수학적으로 P/E와 같아 중복이므로 쓰지 않음)
       단, 고객사 TTM 영업이익 ÷ 정상 EBIT ≥ 2배(규모 구조적 확대)이면 EV/EBIT(TTM)·조정 P/E(TTM)로 전환
    3) 이상치: 가중 중앙값 대비 2.5배 밖 제외, 남은 비교기업 3개 미만이면 그 배수 미사용
    4) 기준 배수 = 고객사 품질 백분위 p(정상 이익률·ROE·성장률 평균, 25~75%로 제한)의 비교군 가중 분위수
       보수 = p−25%p 분위수, 낙관 = p+25%p 분위수 (품질 위치를 중심으로 한 시나리오)
    5) 이상치 제거 후에도 분산(표본 6개 미만은 최대/최소)이 3배를 넘으면 사용하되 '신뢰도 낮음'
    """
    mv = target.values
    res = {"method": "peer", "label": METHOD_LABEL["peer"], "available": False, "scenarios": {}, "notes": [], "inputs": [],
           "rules": "가중치·이상치 제거·이익 기준 자동 선택(정상화/TTM)·품질 백분위 배수"}
    # 이익 기준 선택: 고객사 사업 규모가 5년 평균 대비 구조적으로 커졌으면 TTM 기준 배수 사용
    ttm_ebit, norm_ebit = mv.get("operating_income_ttm"), mv.get("ebit_norm")
    scale = ttm_ebit / norm_ebit if (ttm_ebit and norm_ebit and norm_ebit > 0 and ttm_ebit > 0) else None
    res["scale_ratio"] = scale
    basis = "ttm" if scale is not None and scale >= SCALE_SWITCH_RATIO else "normalized"
    res["basis"] = basis

    def _quality(metrics):
        q = quality_percentile(target, peers, metrics)
        pr = q["percentile"]
        return q, pr, (min(0.75, max(0.25, pr)) if pr is not None else 0.5)

    qual, p_raw, p_used = _quality(QUALITY_METRICS)
    norm_res = {"notes": []}
    norm_used, norm_low = _peer_multiples(PEER_PLANS["normalized"], target, peers, p_used, norm_res)
    res["inputs_norm"] = norm_used  # 미래이익 평가(정상화 기준)에서 재사용
    if basis == "ttm":
        res["basis_note"] = (f"이익 기준 TTM 전환: 고객사 TTM 영업이익 {fmt_krw(ttm_ebit)}이 {int(mv.get('norm_quarters') or 20)}개 분기 평균"
                             f" {fmt_krw(norm_ebit)}의 {scale:.1f}배(≥{SCALE_SWITCH_RATIO:g}배) — 사업 규모가 커져 5년 평균에 배수를 곱하면 과소평가되므로"
                             f" 비교기업·고객사 모두 TTM(경상) 이익 기준 배수 사용")
        res["notes"].append(res["basis_note"])
        qual_t, p_raw_t, p_used_t = _quality(QUALITY_METRICS_TTM)
        used, low_rel = _peer_multiples(PEER_PLANS["ttm"], target, peers, p_used_t, res)
        if used:
            qual, p_raw, p_used = qual_t, p_raw_t, p_used_t
        if not used:
            res["notes"].append("TTM 기준 배수를 산출할 비교기업이 부족해 정상화(5년 평균) 기준으로 대체")
            res["basis"] = "normalized"
            res["notes"].extend(norm_res["notes"])
            res.setdefault("excluded", []).extend(norm_res.get("excluded", []))
            used, low_rel = norm_used, norm_low
    else:
        res["notes"].extend(norm_res["notes"])
        res.setdefault("excluded", []).extend(norm_res.get("excluded", []))
        used, low_rel = norm_used, norm_low
    res["quality"] = qual
    res["quality_percentile_used"] = p_used
    if not used:
        # 최후 대체: 매출·자산 배수 (이익 기반 아님 → 단독으로는 판단 근거가 되지 않음)
        for key, base_key, label in [("ev_sales", "revenue_ttm", "EV/Sales"), ("pbr", "equity_owners", "PBR")]:
            base = mv.get(base_key)
            raw = [(p.name, p.values[key], p.weight) for p in peers if p.values.get(key) is not None and p.values[key] > 0]
            if not base or base <= 0 or len(raw) < MIN_PEERS_AFTER_TRIM:
                continue
            vals, ws = [x[1] for x in raw], [x[2] for x in raw]
            qs = {"conservative": _wquantile(vals, ws, 0.25), "base": _wquantile(vals, ws, 0.5), "optimistic": _wquantile(vals, ws, 0.75)}
            per = {}
            for s in SCENARIOS:
                if key == "ev_sales":
                    evv = qs[s] * base
                    eq = _equity_from_ev(evv, mv)
                else:
                    evv, eq = None, qs[s] * base
                per[s] = {"multiple": qs[s], "base_metric": base, "enterprise_value": evv, "equity_value": eq, "per_share": _per_share(eq, mv)}
            used.append({"multiple": label, "key": key, "fallback": True, "peers": [{"name": n, "value": v, "weight": w} for n, v, w in raw],
                         "quantiles": qs, "results": per, "base_eid": target.eids.get(base_key)})
        if used:
            res["fallback"] = True
            res["notes"].append("이익 기반 배수를 쓸 수 없어 대체 배수(" + ", ".join(u["multiple"] for u in used) + ")만 사용 — 신뢰도 하향")
    if not used:
        return res
    if low_rel and not res.get("fallback"):
        res["fallback"] = True  # 가중치 절반·신뢰도 감점 경로 재사용
    res["available"] = True
    res["inputs"] = used
    res["earnings_based"] = any(u["key"] in EARNINGS_KEYS for u in used)
    for s in SCENARIOS:
        vals = [u["results"][s]["per_share"] for u in used if u["results"][s]["per_share"] is not None]
        res["scenarios"][s] = {"per_share": sum(vals) / len(vals) if vals else None,
                               "components": {u["multiple"]: u["results"][s] for u in used}}
    res["peer_count"] = max(len(u["peers"]) for u in used)
    b = res["scenarios"]["base"]["per_share"]
    qtxt = ", ".join(f"{m['label']} {m['percentile']:.0%}" for m in qual["metrics"].values())
    method_txt = "; ".join(f"{u['multiple']} 비교군({', '.join(f'{p['name']} {p['value']:.1f}' for p in u['peers'])}) "
                           f"{'품질 백분위' if not u.get('fallback') else '중앙값'} 배수 {u['quantiles']['base']:.1f}배" for u in used)
    if p_raw is not None:
        method_txt += f" | 고객사 품질 백분위 {p_raw:.0%}(적용 {p_used:.0%})"
    res["eid"] = ev.add("estimate", f"{target.name} 동종업계 상대평가 주당가치(기준)", b, "KRW/주", f"{b:,.0f}원" if b else "N/A",
                        company=None, method=method_txt, source="ValueScope 계산",
                        impact="동종업계 대비 적정가치 기준점",
                        detail={"inputs": used, "basis": res["basis"], "scale_ratio": scale, "quality": qual, "quality_percentile_used": p_used, "notes": res["notes"]})
    ev.items[-1]["method"] = ((ev.items[-1]["method"] or "") + (f" (지표별: {qtxt})" if qtxt else "")
                              + " | 규칙: 가중치=적합성점수×활용구분, 이상치(중앙값 2.5배 밖) 제외, 기준배수=품질 백분위 p(25~75%), 보수/낙관=p∓25%p"
                              + "".join(f" | {n}" for n in res["notes"]))
    return res


def method_history(target: CompanyMetrics, fs: FinSeries, monthly_caps: pd.Series, ev: Evidence) -> dict:
    """자사 과거 월말 EV/정상EBIT·정상 P/E·PBR 분포. 각 시점에는 그때까지 공시된 실적만 사용(미래정보 미사용)."""
    from app.services.normalize import normalized

    mv = target.values
    res = {"method": "history", "label": METHOD_LABEL["history"], "available": False, "scenarios": {}, "notes": [], "observations": {}}
    obs = {"ev_ebit_norm": [], "pe_norm": [], "pbr": []}
    for d, cap in monthly_caps.items():
        filed_by = d.strftime("%Y%m%d")
        lq = fs.latest_quarter(filed_by)
        if not lq:
            continue
        nrm = normalized(fs, lq, filed_by)
        debt, cash = fs.pt("total_debt", lq), fs.pt("cash", lq)
        sti = fs.pt("short_term_investments", lq) or 0.0
        te, eo = fs.pt("total_equity", lq), fs.pt("equity_owners", lq)
        minority = (te - eo) if te is not None and eo is not None else 0.0
        if nrm.get("ebit_norm") and nrm["ebit_norm"] > 0 and debt is not None and cash is not None:
            obs["ev_ebit_norm"].append({"date": str(d.date()), "value": (cap + debt - cash - sti + minority) / nrm["ebit_norm"], "quarter": lq})
        if nrm.get("ni_core_norm") and nrm["ni_core_norm"] > 0:
            obs["pe_norm"].append({"date": str(d.date()), "value": cap / nrm["ni_core_norm"], "quarter": lq})
        bv = eo if eo is not None else te
        if bv and bv > 0:
            obs["pbr"].append({"date": str(d.date()), "value": cap / bv, "quarter": lq})
    used = []
    for key, base_key, label, is_ev in [("ev_ebit_norm", "ebit_norm", "EV/정상EBIT", True), ("pe_norm", "ni_core_norm", "정상 P/E", False),
                                        ("pbr", "equity_owners", "PBR", False)]:
        vals = [o["value"] for o in obs[key]]
        res["observations"][key] = obs[key]
        base = mv.get(base_key)
        if len(vals) < 8:
            res["notes"].append(f"{label}: 과거 유효 관측치 {len(vals)}개(8개 미만) → 적용 불가")
            continue
        if base is None or base <= 0:
            res["notes"].append(f"{label}: 현재 기준지표 0 이하 → 적용 불가")
            continue
        qs = _quantiles(vals)
        dsp = dispersion(qs, vals)
        if dsp is None or dsp > MAX_MULTIPLE_DISPERSION:
            res["notes"].append(f"{label}: 과거 {len(vals)}개 관측 분산 과대(상위/하위 {dsp or 0:.1f}배, 범위 {min(vals):.1f}~{max(vals):.1f}배) → 제외")
            res.setdefault("excluded", []).append({"multiple": label, "quantiles": qs, "dispersion": dsp, "range": [min(vals), max(vals)], "n": len(vals)})
            continue
        per = {}
        for s in SCENARIOS:
            if is_ev:
                evv = qs[s] * base
                eq = _equity_from_ev(evv, mv)
            else:
                evv, eq = None, qs[s] * base
            per[s] = {"multiple": qs[s], "base_metric": base, "enterprise_value": evv, "equity_value": eq, "per_share": _per_share(eq, mv)}
        used.append({"multiple": label, "key": key, "n": len(vals), "quantiles": qs, "results": per, "dispersion": dsp,
                     "range": [min(vals), max(vals)], "base_eid": target.eids.get(base_key)})
    if not used:
        return res
    res["available"] = True
    res["inputs"] = used
    res["earnings_based"] = any(u["key"] in EARNINGS_KEYS for u in used)
    for s in SCENARIOS:
        vals = [u["results"][s]["per_share"] for u in used if u["results"][s]["per_share"] is not None]
        res["scenarios"][s] = {"per_share": sum(vals) / len(vals) if vals else None,
                               "components": {u["multiple"]: u["results"][s] for u in used}}
    b = res["scenarios"]["base"]["per_share"]
    res["eid"] = ev.add("estimate", f"{target.name} 자사 과거 밸류에이션 기준 주당가치(기준)", b, "KRW/주", f"{b:,.0f}원" if b else "N/A",
                        method="; ".join(f"과거 {u['n']}개 월말 {u['multiple']} 중앙값 {u['quantiles']['base']:.1f}배(범위 {u['range'][0]:.1f}~{u['range'][1]:.1f}배)" for u in used)
                        + " × 현재 기준지표(정상화 이익·자본) (각 시점 공시 완료된 실적만 사용)"
                        + ("".join(f" | 제외: {n}" for n in res["notes"]) if res["notes"] else ""),
                        source="ValueScope 계산 (KRX 시가총액·OpenDART 재무)", impact="자사 역사적 평가 수준 대비 현재 위치",
                        detail={"inputs": [{k: u[k] for k in ("multiple", "n", "quantiles", "range")} for u in used],
                                "excluded": res.get("excluded", [])})
    return res


def method_forward(target: CompanyMetrics, fs: FinSeries, schedule: dict, peer_res: dict, hist_res: dict, ev: Evidence) -> dict:
    """A. 납기 기반 향후 12개월 매출 × 이익률 시나리오 × EV/정상EBIT(비교기업 중앙값, 고정)."""
    mv = target.values
    res = {"method": "forward", "label": METHOD_LABEL["forward"], "available": False, "scenarios": {}, "notes": [], "assumptions": {}}
    if not schedule or not schedule.get("available"):
        res["notes"].append(schedule.get("reason") if schedule else "수주잔고 납기 정보 없음")
        return res
    m3, ttm = mv.get("margin_norm"), mv.get("op_margin")
    if m3 is None:
        res["notes"].append("12개 분기 이익률 이력이 없어 미래이익 기반 평가 불가")
        return res
    lo, hi = (min(m3, ttm), max(m3, ttm)) if ttm is not None else (m3, m3)
    opm = {"conservative": lo, "base": m3, "optimistic": hi}
    mult_src = None
    for src in (peer_res, hist_res):
        comp = next((u for u in (src.get("inputs_norm") or src.get("inputs", [])) if u["key"] == "ev_ebit_norm"), None)
        if comp:
            from_norm = bool(src.get("inputs_norm")) and src.get("basis") == "ttm"
            peers_txt = ", ".join(f"{p_['name']} {p_['value']:.1f}" for p_ in comp.get("peers", []) or [])
            mult_src = (src["label"] + " EV/정상EBIT"
                        + (" (정상화 기준 — 상대평가 본 산식은 TTM 기준으로 전환됨)" if from_norm else "")
                        + (f": {peers_txt}" if peers_txt else ""), comp["quantiles"])
            break
    if not mult_src:
        exc = next((x for x in peer_res.get("excluded", []) if x["multiple"] == "EV/정상EBIT" and len(x.get("peers", [])) >= 2), None)
        if exc:
            mult_src = ("B. 비교기업 EV/정상EBIT 중앙값(분산 큼)", exc["quantiles"])
            res["fallback"] = True
            res["notes"].append(f"비교기업 EV/정상EBIT 분산이 커(상위/하위 {exc['dispersion'] or 0:.1f}배) 중앙값 {exc['quantiles']['base']:.1f}배만 사용 — 가중치·신뢰도 하향")
        else:
            res["notes"].append("적용할 EV/정상EBIT 배수(비교기업·자사 과거)가 없어 미래이익 기반 평가 불가")
            return res
    m_base = mult_src[1]["base"]
    from app.services.normalize import sum_q

    lq = mv.get("latest_quarter")
    nq = int(mv.get("norm_quarters") or 12)
    past_q = nq - 4
    op8 = sum_q(fs, "operating_income", lq, past_q) if (fs is not None and lq) else None
    if op8 is None:
        res["notes"].append(f"최근 {past_q}개 분기 영업이익 이력이 없어 향후 이익을 정상화 기준으로 환산할 수 없음 → 적용 불가")
        return res
    years = nq / 4
    res["available"] = True
    res["earnings_based"] = True
    res["assumptions"] = {
        "backlog": schedule["official_backlog"], "backlog_source": "정기보고서 수주상황 표", "backlog_as_of": schedule["as_of"],
        "forward_revenue": {s: schedule["scenarios"][s]["revenue"] for s in SCENARIOS}, "recognition_rule": schedule["rule"],
        "op_margin": opm, "op_margin_basis": f"보수 min({nq}분기 평균, TTM) · 기준 {nq}분기 평균 · 낙관 max",
        "multiple_source": mult_src[0], "multiple": m_base, "multiples": mult_src[1], "op_last8": op8,
        "normalization": f"향후 정상 EBIT = (최근 {past_q}개 분기 영업이익 + 향후 12개월 영업이익) ÷ {years:g}년 — 배수(EV/정상EBIT)와 같은 {years:g}년 평균 기준",
        "past_quarters": past_q,
        "undisclosed_contracts": schedule["undisclosed_contracts"], "undisclosed_pool": schedule["undisclosed_pool"],
    }
    for s in SCENARIOS:
        rev = schedule["scenarios"][s]["revenue"]
        ebit = rev * opm[s]
        ebit_n = (op8 + ebit) / years
        if ebit_n > 0:
            evv = ebit_n * m_base
            eq = _equity_from_ev(evv, mv)
        else:
            evv, eq = None, max(0.0, -(mv.get("net_debt") or 0.0) - (mv.get("minority_interest") or 0.0))
        res["scenarios"][s] = {"forward_revenue": rev, "forward_ebit": ebit, "forward_ebit_norm": ebit_n, "multiple": m_base, "enterprise_value": evv,
                               "equity_value": eq, "per_share": _per_share(eq, mv)}
    res["notes"].extend(schedule.get("notes", []))
    b = res["scenarios"]["base"]
    res["eid"] = ev.add("estimate", f"{target.name} 미래이익(수주 납기) 기반 주당가치(기준)", b["per_share"], "KRW/주",
                        f"{b['per_share']:,.0f}원" if b["per_share"] else "N/A",
                        method=f"향후 12개월 납기 도래 수주잔고 {fmt_krw(b['forward_revenue'])} × 영업이익률 {opm['base']:.1%}({nq}개 분기 평균)"
                               f" = 향후 영업이익 {fmt_krw(b['forward_ebit'])} → 정상 EBIT 환산 (최근 {past_q}분기 {fmt_krw(op8)} + 향후) ÷ {years:g}"
                               f" = {fmt_krw(b['forward_ebit_norm'])} × EV/정상EBIT {m_base:.1f}배({mult_src[0]}) − 순차입금 − 비지배지분 → 주당가치",
                        source="ValueScope 추정(명시적 가정)", impact="수주 납기 기반 향후 이익 반영",
                        detail={"assumptions": res["assumptions"], "scenarios": res["scenarios"]})
    return res


RECOVER = {
    "forward": "정기보고서 수주상황 표(수주총액−기납품액=수주잔고 검증)와 계약 납기 정보가 확보되면 회복",
    "peer": "적정가치 비교용 상장 비교기업이 정상화 이익 흑자로 2개 이상, 배수 분산 3배 이내이면 회복",
    "history": "12개 분기 이상 재무 이력과 흑자 기간이 쌓이고 과거 배수 분산이 3배 이내이면 회복",
}


def combine(methods: list[dict], price: float | None, peer_count: int, earnings_unstable: bool,
            undisclosed: int, ev: Evidence, target_name: str, official_backlog: bool = False) -> dict:
    avail = [m for m in methods if m.get("available") and all(m["scenarios"].get(s, {}).get("per_share") for s in SCENARIOS)]
    breakdown, conf = [], 100.0

    def deduct(points, reason, recover):
        nonlocal conf
        conf -= points
        breakdown.append({"points": -points, "reason": reason, "recover": recover})

    for m in methods:
        if not m.get("available"):
            deduct(15, f"{m['label']} 적용 불가: {' / '.join(m.get('notes', [])[:2]) or '데이터 부족'}", RECOVER.get(m["method"], ""))
        elif m.get("fallback"):
            deduct(10, f"{m['label']}: 분산이 큰 배수의 중앙값 또는 대체 배수 사용", "비교기업 배수 분산이 3배 이내가 되도록 비교군 재구성")
    if peer_count < 3:
        deduct(15, f"유효 비교기업 수 {peer_count}개(3개 미만)", "적정가치 비교용 상장 비교기업 추가")
    if earnings_unstable:
        deduct(10, "최근 4개 분기 중 2개 분기 이상 영업적자 — 단기 실적 불안정(정상화 이익으로 일부 보완)",
               "흑자 분기 누적 시 회복")
    if undisclosed:
        if official_backlog:
            deduct(5, f"금액 비공개 진행 계약 {undisclosed}건 — 잔고 총액은 정기보고서로 확인되나 계약별 금액·납기 배분은 가정",
                   "유보기한 이후 계약금액이 공시되면 회복")
        else:
            deduct(10, f"금액 비공개 진행 계약 {undisclosed}건 — 수주 규모 불확실", "정기보고서 수주잔고 확보 또는 계약금액 공시 시 회복")
    out = {"available_methods": [m["method"] for m in avail], "weights": {}, "scenarios": {}}
    if avail:
        bases = [m["scenarios"]["base"]["per_share"] for m in avail]
        if len(bases) >= 2 and min(bases) > 0:
            out["method_dispersion"] = max(bases) / min(bases)
        weights = {}
        for m in avail:
            weights[m["method"]] = METHOD_WEIGHT[m["method"]] * (0.5 if m.get("fallback") else 1.0)
        tot = sum(weights.values())
        out["weights"] = {k: w / tot for k, w in weights.items()}
        for s in SCENARIOS:
            out["scenarios"][s] = sum(out["weights"][m["method"]] * m["scenarios"][s]["per_share"] for m in avail)
        lo, mid, hi = sorted(out["scenarios"].values())
        out["scenarios"] = {"conservative": lo, "base": mid, "optimistic": hi}
        env_lo = min(m["scenarios"]["conservative"]["per_share"] for m in avail)
        env_hi = max(m["scenarios"]["optimistic"]["per_share"] for m in avail)
        out["envelope"] = {"low": env_lo, "high": env_hi}
        out["range_ratio"] = env_hi / env_lo if env_lo > 0 else None
        # 결과 분산은 한 번만 감점 (방법 간 기준값 차이가 주 지표, 단일 방법이면 범위 폭)
        md = out.get("method_dispersion")
        if md is not None:
            if md > 4:
                deduct(25, f"평가 방법 간 기준값 차이 {md:.1f}배(4배 초과)", "방법 간 기준값 차이가 2배 이내가 되면 회복")
            elif md > 2:
                deduct(15, f"평가 방법 간 기준값 차이 {md:.1f}배(2배 초과)", "방법 간 기준값 차이가 2배 이내가 되면 회복")
        elif out["range_ratio"] is None or out["range_ratio"] > MAX_RANGE_RATIO:
            deduct(10, f"적정주가 범위 과대(낙관/보수 {out['range_ratio'] or 0:.1f}배)", "시나리오 가정의 불확실성이 줄면 회복")
    conf = max(0.0, min(100.0, conf))
    out["confidence"] = conf
    out["confidence_breakdown"] = breakdown
    verdict, reason = "판단 유보", None
    if not avail:
        reason = "적용 가능한 평가 방법이 없음"
    elif len(avail) < 2:
        reason = "독립 평가 방법이 2개 미만"
    elif not any(m.get("earnings_based") for m in avail):
        reason = "이익 기반 평가(정상 EBIT·순이익 배수, 수주 기반 이익)를 적용할 수 없음(적자 등) — 매출·자산 배수만으로는 판단하지 않음"
    elif out.get("method_dispersion") and out["method_dispersion"] > MAX_METHOD_DISAGREEMENT:
        reason = (f"평가 방법 간 기준값이 {out['method_dispersion']:.1f}배 차이(>{MAX_METHOD_DISAGREEMENT:.0f}배) — "
                  "방법 간 결론이 불일치해 단일 판단을 내리지 않음")
    elif conf < 40:
        reason = f"분석 신뢰도 {conf:.0f}점(40점 미만)"
    elif out.get("range_ratio") is None or out["range_ratio"] > MAX_RANGE_RATIO * 2:
        reason = f"적정주가 범위가 너무 넓음(낙관/보수 {out.get('range_ratio') or 0:.1f}배)"
    elif price:
        s = out["scenarios"]
        if price < s["conservative"]:
            verdict = "저평가"
        elif price > s["optimistic"]:
            verdict = "고평가"
        else:
            verdict = "적정"
    else:
        reason = "현재 주가 없음"
    out["verdict"], out["verdict_reason"] = verdict, reason
    if price and out["scenarios"]:
        out["upside_base"] = out["scenarios"]["base"] / price - 1
    if out["scenarios"]:
        s = out["scenarios"]
        out["eid"] = ev.add("estimate", f"{target_name} 적정주가 범위(가중 결합)", s["base"], "KRW/주",
                            f"{s['conservative']:,.0f}~{s['optimistic']:,.0f}원 (기준 {s['base']:,.0f}원)",
                            method="가용 평가방법 가중평균: " + ", ".join(f"{METHOD_LABEL[k]} {w:.0%}" for k, w in out["weights"].items()),
                            source="ValueScope 계산", impact=f"최종 판단: {verdict}",
                            detail={"scenarios": s, "weights": out["weights"], "envelope": out.get("envelope"),
                                    "method_dispersion": out.get("method_dispersion"),
                                    "methods": {m["method"]: {k: m["scenarios"][k]["per_share"] for k in SCENARIOS} for m in avail}})
    out["confidence_eid"] = ev.add("calc", f"{target_name} 분석 신뢰도", conf, "점", f"{conf:.0f}점/100", method="100점에서 감점: " + "; ".join(
        f"{b['reason']}({b['points']})" for b in breakdown) if breakdown else "감점 없음", source="ValueScope 규칙",
        detail={"breakdown": breakdown, "range_ratio": out.get("range_ratio"), "method_dispersion": out.get("method_dispersion"),
                "scale_max": 100})
    return out


# ------------------------------------------------------------------ price attribution
def attribution(target_ret: pd.Series, market_ret: pd.Series, peer_rets: list[pd.Series], disclosures: list[dict],
                end: pd.Timestamp) -> dict:
    """시장·산업(시장 영향 제거한 동종업계)·개별 요인 분해. 산업요인은 시장에 직교화해 중복 합산을 막는다."""
    from statsmodels.regression.linear_model import OLS
    from statsmodels.tools.tools import add_constant

    df = pd.DataFrame({"r": target_ret, "m": market_ret}).dropna()
    df = df[df.index >= end - pd.Timedelta(days=370)]
    if peer_rets:
        ind = pd.concat(peer_rets, axis=1).mean(axis=1)
        df = df.join(ind.rename("ind_raw"), how="inner")
    if len(df) < 60:
        return {"available": False, "reason": f"일별 관측치 {len(df)}개 — 회귀분석에 부족(60개 미만)"}
    # keep consecutive daily observations only (gaps > 7 days = monthly sample section)
    gaps = df.index.to_series().diff().dt.days.fillna(1)
    df = df[gaps <= 7]
    out = {"available": True, "n": int(len(df)), "period": [str(df.index[0].date()), str(df.index[-1].date())]}
    if "ind_raw" in df:
        aux = OLS(df["ind_raw"], add_constant(df["m"])).fit()
        df["ind"] = aux.resid
        X = add_constant(df[["m", "ind"]])
    else:
        X = add_constant(df[["m"]])
    fit = OLS(df["r"], X).fit()
    df["fitted_m"] = fit.params["m"] * df["m"]
    df["fitted_i"] = fit.params.get("ind", 0.0) * df["ind"] if "ind" in df else 0.0
    df["idio"] = df["r"] - df["fitted_m"] - df["fitted_i"]
    out["model"] = {"beta_market": float(fit.params["m"]), "t_market": float(fit.tvalues["m"]),
                    "beta_industry": float(fit.params.get("ind", float("nan"))) if "ind" in df else None,
                    "t_industry": float(fit.tvalues.get("ind", float("nan"))) if "ind" in df else None,
                    "alpha_daily": float(fit.params["const"]), "r2": float(fit.rsquared),
                    "method": "OLS: 개별수익률 = α + β시장×시장대용지수 + β산업×(동종업계 평균수익률 중 시장으로 설명되지 않는 부분) + ε"}
    windows = {}
    for label, days in (("1M", 31), ("3M", 92), ("6M", 183), ("1Y", 366)):
        w = df[df.index > end - pd.Timedelta(days=days)]
        if len(w) < 5:
            continue
        windows[label] = {"total": float(w["r"].sum()), "market": float(w["fitted_m"].sum()),
                          "industry": float(w["fitted_i"].sum()) if "ind" in df else 0.0,
                          "individual": float(w["idio"].sum()), "days": int(len(w))}
    out["windows"] = windows
    sigma = df["idio"].std()
    events = []
    by_date: dict[str, list[dict]] = {}
    for d in disclosures:
        by_date.setdefault(d["rcept_dt"], []).append(d)
    for t, row in df[df["idio"].abs() > 2.5 * sigma].iterrows():
        near = []
        for k in range(0, 3):
            day = (t - pd.Timedelta(days=k)).strftime("%Y%m%d")
            near.extend(by_date.get(day, []))
        events.append({"date": str(t.date()), "return": float(row["r"]), "idiosyncratic": float(row["idio"]),
                       "disclosures": [{"rcept_no": x["rcept_no"], "report_nm": x["report_nm"], "rcept_dt": x["rcept_dt"],
                                        "url": x["url"]} for x in near][:5],
                       "classification": "개별기업 이벤트 후보(공시와 시간적 근접 — 인과관계 미확정)" if near else "원인이 확인되지 않은 변동"})
    out["abnormal_days"] = sorted(events, key=lambda e: e["date"], reverse=True)[:15]
    out["note"] = ("일별 수익률 합산 기준 근사 분해. 시장지수는 KRX 일별매매정보 기반 시가총액가중 대용치(공식 지수 아님). "
                   "공시와 주가 움직임이 같은 시기에 있어도 인과관계를 확정하지 않는다.")
    return out
