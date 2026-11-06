"""분석용 데이터 조립: 재무 시계열(TTM), 가격 시계열(분할 보정 수익률), 시장 대용지수."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Company
from app.models_analysis import FinancialMetric, StockPrice

QUARTER_ENDS = ["03-31", "06-30", "09-30", "12-31"]


def prev_quarter(pe: str, n: int = 1) -> str:
    y, md = int(pe[:4]), pe[5:]
    i = QUARTER_ENDS.index(md) - n
    while i < 0:
        i += 4
        y -= 1
    return f"{y}-{QUARTER_ENDS[i]}"


@dataclass
class FinSeries:
    company: Company
    q: dict = field(default_factory=dict)      # metric -> {period_end: row}
    fy: dict = field(default_factory=dict)
    point: dict = field(default_factory=dict)

    @classmethod
    def load(cls, db: Session, company: Company) -> FinSeries:
        fs = cls(company)
        for r in db.scalars(select(FinancialMetric).where(FinancialMetric.company_id == company.id)):
            target = {"Q": fs.q, "FY": fs.fy, "POINT": fs.point}[r.basis]
            target.setdefault(r.metric, {})[r.period_end] = r
        return fs

    def quarters(self, metric: str = "revenue", filed_by: str | None = None) -> list[str]:
        rows = self.q.get(metric, {})
        return sorted(pe for pe, r in rows.items() if filed_by is None or r.filed_date <= filed_by)

    def latest_quarter(self, filed_by: str | None = None) -> str | None:
        qs = [pe for pe in self.quarters("revenue", filed_by) if self.ttm("revenue", pe, filed_by) is not None]
        return qs[-1] if qs else None

    def qval(self, metric: str, pe: str):
        r = self.q.get(metric, {}).get(pe)
        return r.value if r else None

    def ttm(self, metric: str, pe: str, filed_by: str | None = None) -> float | None:
        vals = []
        for k in range(4):
            r = self.q.get(metric, {}).get(prev_quarter(pe, k))
            if r is None or r.value is None or (filed_by and r.filed_date > filed_by):
                return None
            vals.append(r.value)
        return float(sum(vals))

    def ttm_sources(self, metric: str, pe: str) -> list[str]:
        out = []
        for k in range(4):
            r = self.q.get(metric, {}).get(prev_quarter(pe, k))
            if r:
                out.append(r.rcept_no)
        return sorted(set(out))

    def pt(self, metric: str, pe: str):
        r = self.point.get(metric, {}).get(pe)
        return r.value if r else None

    def pt_row(self, metric: str, pe: str):
        return self.point.get(metric, {}).get(pe)

    def annual(self, metric: str) -> dict[str, float]:
        return {pe[:4]: r.value for pe, r in sorted(self.fy.get(metric, {}).items()) if r.value is not None}


def load_prices(db: Session, codes: list[str]) -> pd.DataFrame:
    rows = db.execute(select(StockPrice.stock_code, StockPrice.trade_date, StockPrice.close, StockPrice.market_cap,
                             StockPrice.listed_shares, StockPrice.volume)
                      .where(StockPrice.stock_code.in_(codes))).all()
    df = pd.DataFrame(rows, columns=["code", "date", "close", "mcap", "shares", "volume"])
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["date"], format="%Y%m%d")
    return df.sort_values(["code", "date"])


def adjusted_returns(df_code: pd.DataFrame) -> tuple[pd.Series, list[dict]]:
    """Period returns between consecutive observations with split/merge correction via listed shares."""
    d = df_code.dropna(subset=["close"]).sort_values("date")
    pr = d["close"].values
    sh = d["shares"].values
    dates = d["date"].values
    rets, events = [np.nan], []
    for i in range(1, len(d)):
        p_ratio = pr[i] / pr[i - 1] if pr[i - 1] else np.nan
        s_ratio = sh[i] / sh[i - 1] if sh[i - 1] and sh[i] else 1.0
        r = p_ratio - 1
        if (s_ratio > 1.3 or s_ratio < 1 / 1.3) and abs(p_ratio * s_ratio - 1) < 0.35:
            r = p_ratio * s_ratio - 1
            events.append({"date": str(pd.Timestamp(dates[i]).date()), "share_ratio": round(float(s_ratio), 4),
                           "note": "상장주식수 변동과 주가 반비례 → 분할·병합으로 보고 수익률 보정"})
        rets.append(r)
    return pd.Series(rets, index=pd.DatetimeIndex(dates)), events


def market_proxy(db: Session, market: str) -> pd.Series:
    """시가총액가중 지수 대용치: 연속 관측일 모두 존재하는 종목의 가격수익률을 전일 시가총액으로 가중.

    공식 KRX 지수가 아님(지수 API 미승인) — 신규상장 효과를 배제한 시장 수익률 근사치.
    """
    rows = db.execute(select(StockPrice.stock_code, StockPrice.trade_date, StockPrice.close, StockPrice.market_cap)
                      .where(StockPrice.market == market)).all()
    df = pd.DataFrame(rows, columns=["code", "date", "close", "mcap"])
    if df.empty:
        return pd.Series(dtype=float)
    close = df.pivot(index="date", columns="code", values="close").sort_index()
    mcap = df.pivot(index="date", columns="code", values="mcap").sort_index()
    r = close / close.shift(1) - 1
    w = mcap.shift(1)
    valid = r.notna() & w.notna() & (r.abs() < 0.35)  # 비정상 수익률(분할 등) 제외
    num = (r.where(valid) * w.where(valid)).sum(axis=1)
    den = w.where(valid).sum(axis=1)
    idx_ret = (num / den).replace([np.inf, -np.inf], np.nan)
    idx_ret.iloc[0] = 0.0
    out = pd.Series(idx_ret.values, index=pd.to_datetime(idx_ret.index, format="%Y%m%d"))
    return out.fillna(0.0)


def cumulative(rets: pd.Series) -> pd.Series:
    return (1 + rets.fillna(0)).cumprod()


def window_return(level: pd.Series, end: pd.Timestamp, days: int) -> float | None:
    s = level[level.index <= end]
    if s.empty:
        return None
    start_date = end - pd.Timedelta(days=days)
    base = s[s.index <= start_date]
    if base.empty:
        return None
    return float(s.iloc[-1] / base.iloc[-1] - 1)


def daily_only(series: pd.Series, since: pd.Timestamp) -> pd.Series:
    return series[series.index >= since]


def to_date(s: str) -> date:
    return date(int(s[:4]), int(s[4:6]), int(s[6:8])) if "-" not in s else date.fromisoformat(s)
