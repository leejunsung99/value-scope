"""KRX Open API 일별 시세 수집 (유가증권·코스닥 전 종목).

- 하루 단위 전 종목 응답이므로 한 번 받은 날짜는 모든 프로젝트가 공유 재사용한다.
- 최근 1년은 영업일 기준 일별, 그 이전 3년까지는 월말 표본만 수집해 호출 수를 줄인다.
- 휴장일은 0건으로 기록해 재호출하지 않는다(최근 3일 이내 0건은 미반영 가능성으로 재확인).
- KRX 일별매매정보에는 수정주가가 없다 → 수익률 계산 시 상장주식수 변동으로 분할·병합을 보정한다.
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models_analysis import PriceFetch, StockPrice

log = logging.getLogger(__name__)
BASE = "https://data-dbg.krx.co.kr/svc/apis/sto"
MARKETS = {"stk": "KOSPI", "ksq": "KOSDAQ"}


class KrxError(Exception):
    pass


def _num(v):
    s = str(v or "").replace(",", "").strip()
    if s in ("", "-"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def sample_dates(today: date, daily_days: int = 370, history_years: int = 3) -> list[date]:
    end = today - timedelta(days=1)
    start_daily = today - timedelta(days=daily_days)
    out = [start_daily + timedelta(days=i) for i in range((end - start_daily).days + 1)]
    out = [d for d in out if d.weekday() < 5]
    # month-end weekdays (plus the 3 prior weekdays as holiday fallback) for older history
    hist_start = date(today.year - history_years - 1, today.month, 1)
    m = date(hist_start.year, hist_start.month, 1)
    while m < start_daily:
        nxt = date(m.year + (m.month // 12), m.month % 12 + 1, 1)
        last = nxt - timedelta(days=1)
        cands = [last - timedelta(days=k) for k in range(7) if (last - timedelta(days=k)).weekday() < 5][:3]
        out.extend(cands)
        m = nxt
    return sorted(set(out))


class KrxClient:
    def __init__(self, api_key: str, on_call=None, timeout: float = 30):
        self.api_key = api_key
        self.on_call = on_call
        self.timeout = timeout

    def daily(self, market: str, d: date) -> list[dict]:
        if not self.api_key:
            raise KrxError("KRX_API_KEY가 설정되지 않았습니다.")
        if self.on_call:
            self.on_call(f"{market}_bydd_trd")
        try:
            with httpx.Client(timeout=self.timeout) as c:
                r = c.get(f"{BASE}/{market}_bydd_trd", headers={"AUTH_KEY": self.api_key},
                          params={"basDd": d.strftime("%Y%m%d")})
        except httpx.HTTPError as e:
            raise KrxError(f"KRX 요청 실패: {type(e).__name__}") from e
        if r.status_code == 401:
            raise KrxError("KRX API 인증 실패(401) — 키 또는 해당 서비스 이용신청 승인 여부를 확인하세요.")
        if r.status_code != 200:
            raise KrxError(f"KRX HTTP {r.status_code}")
        return r.json().get("OutBlock_1", []) or []


def _needed(db: Session, market: str, dates: list[date], today: date) -> list[date]:
    done = {f.trade_date: f for f in db.scalars(select(PriceFetch).where(PriceFetch.market == market))}
    out = []
    for d in dates:
        f = done.get(d.strftime("%Y%m%d"))
        if f is None or (f.row_count == 0 and (today - d).days <= 3):
            out.append(d)
    return out


def _insert_rows(db: Session, rows: list[dict]) -> None:
    if not rows:
        return
    dialect = db.get_bind().dialect.name
    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    for i in range(0, len(rows), 2000):
        stmt = insert(StockPrice).values(rows[i:i + 2000])
        db.execute(stmt.on_conflict_do_nothing(index_elements=["stock_code", "trade_date"]))


def collect_prices(db: Session, krx: KrxClient, today: date | None = None, progress=None) -> dict:
    today = today or date.today()
    dates = sample_dates(today)
    stats = {"requested": 0, "reused": 0, "errors": 0, "rows": 0}
    for market, label in MARKETS.items():
        need = _needed(db, market, dates, today)
        stats["reused"] += len(dates) - len(need)

        def work(d: date):
            return d, krx.daily(market, d)

        done_n = 0
        with ThreadPoolExecutor(max_workers=4) as pool:
            for d, rows in pool.map(work, need):
                stats["requested"] += 1
                ds = d.strftime("%Y%m%d")
                recs = []
                for r in rows:
                    code = r.get("ISU_CD", "")
                    if r.get("BAS_DD") != ds or len(code) != 6:
                        continue
                    recs.append({"stock_code": code, "trade_date": ds, "market": label, "name": r.get("ISU_NM"),
                                 "close": _num(r.get("TDD_CLSPRC")), "open": _num(r.get("TDD_OPNPRC")),
                                 "high": _num(r.get("TDD_HGPRC")), "low": _num(r.get("TDD_LWPRC")),
                                 "volume": _num(r.get("ACC_TRDVOL")), "trade_value": _num(r.get("ACC_TRDVAL")),
                                 "market_cap": _num(r.get("MKTCAP")), "listed_shares": _num(r.get("LIST_SHRS"))})
                _insert_rows(db, recs)
                f = db.scalar(select(PriceFetch).where(PriceFetch.market == market, PriceFetch.trade_date == ds))
                if f is None:
                    db.add(PriceFetch(market=market, trade_date=ds, row_count=len(recs)))
                else:
                    f.row_count = len(recs)
                db.commit()
                stats["rows"] += len(recs)
                done_n += 1
                if progress and done_n % 20 == 0:
                    progress(f"{label} 시세 {done_n}/{len(need)}일 수집")
    return stats
