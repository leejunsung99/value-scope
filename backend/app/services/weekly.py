"""주간 주가 분석 — 1주(월~일) 동안 고객사 주가가 비교군과 같이 움직였는지, 개별 요인이 있었는지, 변동성이 컸는지.

밸류에이션은 하지 않는다. 계산은 모두 여기서(결정적), AI는 해석 문장만 쓴다.

방법
1) 기대수익률 모형: 해당 주 직전 최대 250거래일(최소 60일)로 적합 — 해당 주 데이터는 모형에 쓰지 않는다(사후 맞춤 방지).
     고객사 수익률 = α + β시장 × 시장 대용지수 + β산업 × (비교군 가중평균 수익률 중 시장으로 설명되지 않는 부분) + ε
2) 해당 주 일별: 실제 − 기대 = 개별 요인(잔차), z = 잔차 / 학습기간 잔차 표준편차
     |z| ≥ 2.5 → 이상 변동일
3) 주간: 잔차 합계, z주간 = 잔차합 / (σ × √거래일수)
     |z주간| ≥ 2 → '개별 요인 우세', 그렇지 않고 고객사·비교군 주간 방향이 같으면 '비교군 동조', 아니면 '혼조'
4) 동조성: 일별로 고객사와 비교군 평균의 방향이 같은 날 수, 기업별 같은 방향 일수, 직전 60거래일 상관계수(참고)
5) 변동성: 주중 일별 수익률 표준편차·평균 일중 변동폭(고가−저가)/전일종가·평균 거래량을 직전 60거래일(거래량 20일) 평균과 비교
     변동성 ≥ 1.5배 또는 변동폭 ≥ 1.5배 또는 거래량 ≥ 2배 → '변동성 확대'
6) 이벤트: 해당 주 고객사·비교기업 공시, 뉴스(Claude 웹검색, 해당 주 기간 고정)를 거래일에 연결
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Company
from app.models_analysis import Disclosure, StockPrice
from app.services.engine_data import adjusted_returns, load_prices, market_proxy

TRAIN_DAYS = 250
MIN_TRAIN = 60
Z_DAY = 2.5
Z_WEEK = 2.0
VOL_RATIO = 1.5
RANGE_RATIO = 1.5
VOLUME_RATIO = 2.0
GAP_NOTE = 0.05        # 동조 판정이어도 비교군과 주간 수익률 차이가 5%p 이상이면 라벨에 표시
USE_W = {"valuation": 1.0, "industry_reference": 0.5}
WEEKDAYS = ["월", "화", "수", "목", "금", "토", "일"]


class WeeklyError(Exception):
    pass


@dataclass
class PeerInfo:
    company: Company
    weight: float
    use_type: str
    category: str | None


def week_bounds(week_start: str) -> tuple[date, date]:
    try:
        mon = date.fromisoformat(week_start)
    except ValueError as e:
        raise WeeklyError("주 시작일 형식이 올바르지 않습니다(YYYY-MM-DD).") from e
    if mon.weekday() != 0:
        raise WeeklyError("주 시작일은 월요일이어야 합니다.")
    return mon, mon + timedelta(days=6)


def peer_infos(version) -> list[PeerInfo]:
    out = []
    for m in version.members:
        c = m.company
        if m.use_type in USE_W and c.stock_code and c.is_listed:
            out.append(PeerInfo(c, round(((m.score if m.score is not None else 50.0) / 100.0) * USE_W[m.use_type], 4),
                                m.use_type, m.category))
    return out


def _ohlcv(db: Session, codes: list[str], since: str, until: str) -> pd.DataFrame:
    rows = db.execute(select(StockPrice.stock_code, StockPrice.trade_date, StockPrice.close, StockPrice.high, StockPrice.low,
                             StockPrice.volume).where(StockPrice.stock_code.in_(codes), StockPrice.trade_date >= since,
                                                      StockPrice.trade_date <= until)).all()
    df = pd.DataFrame(rows, columns=["code", "date", "close", "high", "low", "volume"])
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"], format="%Y%m%d")
    return df.sort_values(["code", "date"]) if not df.empty else df


def _daily_only(s: pd.Series) -> pd.Series:
    """월말 표본 구간(관측 간격 > 7일)을 제외한 연속 일별 구간만."""
    s = s.dropna()
    gaps = s.index.to_series().diff().dt.days.fillna(1)
    return s[gaps <= 7]


def compute(db: Session, target: Company, peers: list[PeerInfo], mon: date, sun: date) -> dict:
    from statsmodels.regression.linear_model import OLS
    from statsmodels.tools.tools import add_constant

    if not target.stock_code:
        raise WeeklyError("고객사가 상장사가 아니어서 주가 분석을 할 수 없습니다.")
    codes = [target.stock_code] + [p.company.stock_code for p in peers]
    prices = load_prices(db, codes)
    if prices.empty:
        raise WeeklyError("저장된 시세가 없습니다. 통합 분석을 한 번 실행하거나 주가를 수집하세요.")
    mon_ts, sun_ts = pd.Timestamp(mon), pd.Timestamp(sun)

    rets: dict[str, pd.Series] = {}
    closes: dict[str, pd.Series] = {}
    for code in codes:
        d = prices[prices["code"] == code]
        if d.empty:
            continue
        r, _ = adjusted_returns(d)
        rets[code] = r
        closes[code] = d.set_index("date")["close"]
    t_code = target.stock_code
    if t_code not in rets:
        raise WeeklyError("고객사 시세가 없습니다.")
    t_ret = _daily_only(rets[t_code])
    week_days = t_ret[(t_ret.index >= mon_ts) & (t_ret.index <= sun_ts)].index
    if len(week_days) == 0:
        raise WeeklyError(f"{mon}~{sun} 기간의 거래일 시세가 없습니다(휴장 주이거나 아직 수집되지 않음).")
    prev_day = t_ret[t_ret.index < mon_ts].index.max()
    if prev_day is None or pd.isna(prev_day) or (mon_ts - prev_day).days > 7:
        raise WeeklyError("해당 주 직전 거래일의 일별 시세가 없어 주간 수익률을 계산할 수 없습니다(일별 시세 보관 범위: 최근 약 1년).")

    mkt_label = "KOSPI" if target.market == "KOSPI" else "KOSDAQ"
    m_ret = market_proxy(db, mkt_label)

    # 비교군 가중평균 일별 수익률 (그날 시세가 있는 비교기업만, 가중치 재정규화)
    peer_codes = [p.company.stock_code for p in peers if p.company.stock_code in rets]
    wmap = {p.company.stock_code: p.weight for p in peers}
    pr = pd.concat({c: _daily_only(rets[c]) for c in peer_codes}, axis=1) if peer_codes else pd.DataFrame()
    if not pr.empty:
        w = pd.Series({c: wmap[c] for c in pr.columns})
        mask = pr.notna()
        peer_avg = (pr.fillna(0) * w).sum(axis=1) / (mask * w).sum(axis=1).replace(0, np.nan)
    else:
        peer_avg = pd.Series(dtype=float)

    # ---- 1) 모형 (해당 주 이전 데이터만)
    df = pd.DataFrame({"r": t_ret, "m": m_ret, "ind_raw": peer_avg}).dropna(subset=["r", "m"])
    train = df[df.index < mon_ts].tail(TRAIN_DAYS)
    has_ind = "ind_raw" in train and train["ind_raw"].notna().sum() >= MIN_TRAIN
    train = train.dropna(subset=["ind_raw"]) if has_ind else train
    if len(train) < MIN_TRAIN:
        raise WeeklyError(f"해당 주 이전 일별 관측치가 {len(train)}개로 기대수익률 모형을 만들 수 없습니다(최소 {MIN_TRAIN}개).")
    if has_ind:
        aux = OLS(train["ind_raw"], add_constant(train["m"])).fit()
        train = train.assign(ind=aux.resid)
        fit = OLS(train["r"], add_constant(train[["m", "ind"]])).fit()
    else:
        fit = OLS(train["r"], add_constant(train[["m"]])).fit()
    sigma = float(np.std(fit.resid, ddof=1))
    a, bm = float(fit.params["const"]), float(fit.params["m"])
    bi = float(fit.params.get("ind", 0.0)) if has_ind else 0.0
    model = {"alpha_daily": a, "beta_market": bm, "beta_industry": bi if has_ind else None, "r2": float(fit.rsquared),
             "sigma_daily": sigma, "n": int(len(train)), "period": [str(train.index[0].date()), str(train.index[-1].date())],
             "market_label": f"{mkt_label} 시가총액가중 대용지수"}

    # ---- 2) 해당 주 일별
    wk = df.loc[week_days]
    days = []
    for t, row in wk.iterrows():
        ind_raw = row.get("ind_raw")
        ind = (ind_raw - (aux.params["const"] + aux.params["m"] * row["m"])) if has_ind and pd.notna(ind_raw) else 0.0
        exp_m, exp_i = bm * row["m"], bi * ind
        expected = a + exp_m + exp_i
        resid = row["r"] - expected
        z = resid / sigma if sigma > 0 else None
        days.append({"date": str(t.date()), "weekday": WEEKDAYS[t.weekday()], "ret": float(row["r"]), "market": float(row["m"]),
                     "peer_avg": float(ind_raw) if pd.notna(ind_raw) else None, "expected": float(expected),
                     "market_part": float(exp_m), "industry_part": float(exp_i), "residual": float(resid),
                     "z": float(z) if z is not None else None, "abnormal": bool(z is not None and abs(z) >= Z_DAY),
                     "same_dir": (None if ind_raw is None or pd.isna(ind_raw) or row["r"] == 0 or ind_raw == 0
                                  else bool(np.sign(row["r"]) == np.sign(ind_raw)))})

    def compound(s: pd.Series) -> float:
        return float((1 + s.fillna(0)).prod() - 1)

    n = len(days)
    t_week = compound(wk["r"])
    m_week = compound(wk["m"])
    p_week = compound(wk["ind_raw"]) if has_ind or wk["ind_raw"].notna().any() else None
    resid_sum = float(sum(d["residual"] for d in days))
    z_week = resid_sum / (sigma * math.sqrt(n)) if sigma > 0 else None
    same_days = sum(1 for d in days if d["same_dir"])
    judged_days = sum(1 for d in days if d["same_dir"] is not None)

    # ---- 4) 비교기업별
    t_hist = df[df.index < mon_ts].tail(60)["r"]
    peer_rows = []
    for p in peers:
        c = p.company.stock_code
        if c not in rets:
            peer_rows.append({"company_id": p.company.id, "name": p.company.name, "available": False, "weight": p.weight})
            continue
        pr_c = _daily_only(rets[c])
        pw = pr_c.reindex(week_days)
        sd = sum(1 for x, y in zip(wk["r"], pw) if pd.notna(y) and x != 0 and y != 0 and np.sign(x) == np.sign(y))
        hist = pd.concat([t_hist, pr_c], axis=1, join="inner").dropna()
        corr = float(hist.iloc[:, 0].corr(hist.iloc[:, 1])) if len(hist) >= 20 else None
        peer_rows.append({"company_id": p.company.id, "name": p.company.name, "available": True, "weight": p.weight,
                          "use_type": p.use_type, "category": p.category, "week_return": compound(pw),
                          "daily": [float(x) if pd.notna(x) else None for x in pw], "same_dir_days": sd,
                          "corr_60d": corr})

    # ---- 5) 변동성·거래량
    since = (mon_ts - pd.Timedelta(days=120)).strftime("%Y%m%d")
    ohlc = _ohlcv(db, [t_code], since, sun_ts.strftime("%Y%m%d"))
    vol = {}
    if not ohlc.empty:
        o = ohlc.set_index("date")
        o["prev_close"] = o["close"].shift(1)
        o["range"] = (o["high"] - o["low"]) / o["prev_close"]
        before = o[o.index < mon_ts].tail(60)
        inweek = o.loc[o.index.isin(week_days)]
        hist_r = t_ret[t_ret.index < mon_ts].tail(60)
        vol = {
            "ret_std_week": float(wk["r"].std(ddof=1)) if n >= 2 else None,
            "ret_std_60d": float(hist_r.std(ddof=1)) if len(hist_r) >= 20 else None,
            "range_week": float(inweek["range"].mean()) if inweek["range"].notna().any() else None,
            "range_60d": float(before["range"].mean()) if before["range"].notna().any() else None,
            "volume_week": float(inweek["volume"].mean()) if inweek["volume"].notna().any() else None,
            "volume_20d": float(before["volume"].tail(20).mean()) if before["volume"].notna().any() else None,
        }
        vol["std_ratio"] = (vol["ret_std_week"] / vol["ret_std_60d"]) if vol["ret_std_week"] and vol["ret_std_60d"] else None
        vol["range_ratio"] = (vol["range_week"] / vol["range_60d"]) if vol["range_week"] and vol["range_60d"] else None
        vol["volume_ratio"] = (vol["volume_week"] / vol["volume_20d"]) if vol["volume_week"] and vol["volume_20d"] else None
        for d in days:
            row = o.loc[pd.Timestamp(d["date"])] if pd.Timestamp(d["date"]) in o.index else None
            if row is not None:
                d["range"] = float(row["range"]) if pd.notna(row["range"]) else None
                d["volume_ratio"] = float(row["volume"] / vol["volume_20d"]) if vol.get("volume_20d") and pd.notna(row["volume"]) else None
                d["close"] = float(row["close"])
    vol_flags = [k for k, th in (("std_ratio", VOL_RATIO), ("range_ratio", RANGE_RATIO), ("volume_ratio", VOLUME_RATIO))
                 if vol.get(k) is not None and vol[k] >= th]

    # ---- 3) 주간 판정
    if z_week is not None and abs(z_week) >= Z_WEEK:
        pattern = "idiosyncratic"
        label = f"개별 요인 우세 ({'상승' if resid_sum > 0 else '하락'})"
    elif p_week is not None and t_week * p_week > 0:
        pattern = "comove"
        gap = t_week - p_week
        # 방향은 같지만 폭 차이가 크면(통계적으로 유의하지 않더라도) 함께 표시
        gap_txt = f", 비교군 대비 {gap * 100:+.1f}%p {'초과' if gap > 0 else '부진'}" if abs(gap) >= GAP_NOTE else ""
        label = f"비교군 동조 ({'동반 상승' if t_week > 0 else '동반 하락'}{gap_txt})"
    else:
        pattern = "mixed"
        label = "혼조 (비교군과 방향 다름, 개별 요인은 통계적으로 약함)"

    # 지수화 차트용: 직전 거래일 = 100
    def path(series: pd.Series) -> list[list]:
        lvl = 100.0
        out = [[str(prev_day.date()), 100.0]]
        for t in week_days:
            v = series.get(t)
            lvl *= 1 + (float(v) if v is not None and pd.notna(v) else 0.0)
            out.append([str(t.date()), round(lvl, 2)])
        return out

    chart = {"target": path(wk["r"]), "market": path(wk["m"]),
             "peer_avg": path(wk["ind_raw"]) if p_week is not None else None,
             "peers": {str(p.company.id): path(_daily_only(rets[p.company.stock_code]).reindex(week_days))
                       for p in peers if p.company.stock_code in rets}}
    t_close = closes[t_code]
    return {
        "week": {"start": str(mon), "end": str(sun), "trading_days": n, "base_date": str(prev_day.date()),
                 "base_close": float(t_close.get(prev_day)) if prev_day in t_close.index else None,
                 "last_close": float(t_close.get(week_days[-1])) if week_days[-1] in t_close.index else None},
        "summary": {"target_return": t_week, "peer_return": p_week, "market_return": m_week,
                    "excess_vs_peers": (t_week - p_week) if p_week is not None else None, "excess_vs_market": t_week - m_week,
                    "residual_sum": resid_sum, "z_week": z_week, "pattern": pattern, "pattern_label": label,
                    "same_dir_days": same_days, "judged_days": judged_days,
                    "abnormal_days": [d["date"] for d in days if d["abnormal"]],
                    "volatility_flags": vol_flags, "volatility_label": "변동성 확대" if vol_flags else "평상 수준"},
        "days": days, "peers": peer_rows, "volatility": vol, "model": model, "chart": chart,
        "thresholds": {"z_day": Z_DAY, "z_week": Z_WEEK, "vol_ratio": VOL_RATIO, "range_ratio": RANGE_RATIO, "volume_ratio": VOLUME_RATIO},
    }


def week_disclosures(db: Session, companies: list[Company], mon: date, sun: date) -> list[dict]:
    lo, hi = (mon - timedelta(days=2)).strftime("%Y%m%d"), sun.strftime("%Y%m%d")
    out = []
    for c in companies:
        for d in db.scalars(select(Disclosure).where(Disclosure.company_id == c.id, Disclosure.rcept_dt >= lo, Disclosure.rcept_dt <= hi)
                            .order_by(Disclosure.rcept_dt)):
            out.append({"date": f"{d.rcept_dt[:4]}-{d.rcept_dt[4:6]}-{d.rcept_dt[6:]}", "company_id": c.id, "company": c.name,
                        "title": d.report_nm.strip(), "url": d.url, "rcept_no": d.rcept_no})
    return out


def attach_events(result: dict, target_id: int, disclosures: list[dict], news_events: list[dict]) -> None:
    """거래일마다 당일~2일 전(주말 포함) 고객사 공시·뉴스를 연결해 개별 변동 설명 후보로 붙인다."""
    for d in result["days"]:
        dd = date.fromisoformat(d["date"])
        win = {(dd - timedelta(days=k)).isoformat() for k in range(0, 3)}
        d["disclosures"] = [x for x in disclosures if x["company_id"] == target_id and x["date"] in win]
        d["news"] = [{"event": e["event"], "date": e["date"], "url": e["articles"][0]["url"], "outlets": e["outlets"][:3],
                      "sentiment": e["sentiment"], "verification": e.get("verification")}
                     for e in news_events if e.get("company_id") == target_id and e.get("date") in win]
        if d["abnormal"]:
            d["explanation"] = ("공시와 시간적 근접" if d["disclosures"] else "언론 보도와 시간적 근접" if d["news"]
                                else "확인된 공시·보도 없음") + " (인과관계 미확정)"
