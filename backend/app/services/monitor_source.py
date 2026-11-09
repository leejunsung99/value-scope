"""Read-only adapter for the separate dart-market-monitor database.

The monitor already polls every DART filing. ValueScope reads its disclosure list (and the
recent parsed text it keeps for ~14 days) instead of calling DART again. The connection uses a
SELECT-only role whose sessions default to read-only; we also set read_only on the connection.
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
from datetime import date

import psycopg

log = logging.getLogger(__name__)


class MonitorSource:
    def __init__(self, url: str):
        self.url = url.replace("postgresql+psycopg://", "postgresql://") if url else ""

    @property
    def enabled(self) -> bool:
        return bool(self.url)

    @contextmanager
    def _conn(self):
        conn = psycopg.connect(self.url, connect_timeout=10)
        conn.read_only = True
        try:
            yield conn
        finally:
            conn.rollback()
            conn.close()

    def coverage_start(self) -> str | None:
        """Earliest filing date the monitor has (YYYYMMDD) — before this, DART must be queried."""
        if not self.enabled:
            return None
        try:
            with self._conn() as c:
                d = c.execute("SELECT min(rcept_date) FROM disclosure_history").fetchone()[0]
            return d.strftime("%Y%m%d") if d else None
        except psycopg.Error as e:
            log.warning("monitor coverage query failed: %s", e)
            return None

    def disclosures(self, corp_code: str, since: str) -> list[dict]:
        """Filings for a company since YYYYMMDD, with any parsed text the monitor still holds."""
        since_d = date(int(since[:4]), int(since[4:6]), int(since[6:]))
        with self._conn() as c:
            rows = c.execute(
                """SELECT h.rcept_no, h.report_name, h.rcept_date, h.is_correction, h.original_rcept_no,
                          d.data->>'text_excerpt' AS text_excerpt
                   FROM disclosure_history h LEFT JOIN disclosures d USING (rcept_no)
                   WHERE h.corp_code = %s AND h.rcept_date >= %s ORDER BY h.rcept_date, h.rcept_no""",
                (corp_code, since_d),
            ).fetchall()
        return [
            {"rcept_no": r[0], "report_nm": (r[1] or "").strip(), "rcept_dt": r[2].strftime("%Y%m%d"),
             "is_correction": bool(r[3]), "original_rcept_no": r[4], "text_excerpt": r[5]}
            for r in rows
        ]

    def market_caps(self, stock_codes: list[str]) -> dict[tuple[str, str], float]:
        with self._conn() as c:
            rows = c.execute("SELECT stock_code, as_of, market_cap FROM krx_market_caps WHERE stock_code = ANY(%s)",
                             (stock_codes,)).fetchall()
        return {(r[0], r[1].strftime("%Y%m%d")): float(r[2]) for r in rows}

    # ---- 비교기업 후보 확장 탐색용 (읽기 전용)
    def corp_classes(self) -> dict[str, str]:
        """corp_code → corp_cls (Y=KOSPI, K=KOSDAQ, N=KONEX, E=기타)."""
        with self._conn() as c:
            rows = c.execute("SELECT corp_code, corp_cls FROM companies WHERE corp_cls IS NOT NULL").fetchall()
        return {r[0]: r[1] for r in rows}

    def latest_market_caps(self) -> dict[str, float]:
        """stock_code → 가장 최근 시가총액."""
        with self._conn() as c:
            rows = c.execute(
                """SELECT DISTINCT ON (stock_code) stock_code, market_cap FROM krx_market_caps
                   ORDER BY stock_code, as_of DESC""").fetchall()
        return {r[0]: float(r[1]) for r in rows if r[1]}

    def contracts_since(self) -> str | None:
        with self._conn() as c:
            d = c.execute("SELECT min(rcept_date) FROM disclosures WHERE data->>'counterparty' IS NOT NULL").fetchone()[0]
        return d.strftime("%Y%m%d") if d else None

    def contract_counterparties(self) -> list[dict]:
        """공시알람이 구조화해 둔 단일판매·공급계약 공시의 계약상대방 목록."""
        with self._conn() as c:
            rows = c.execute(
                """SELECT corp_code, stock_code, corp_name, rcept_no, rcept_date, report_name,
                          data->>'counterparty', data->'summary'->'items'
                   FROM disclosures
                   WHERE data->>'counterparty' IS NOT NULL AND stock_code IS NOT NULL AND stock_code <> ''""").fetchall()
        out = []
        for r in rows:
            desc = None
            for it in (r[7] or []):
                if isinstance(it, dict) and it.get("label") == "계약 내용":
                    desc = it.get("value")
            out.append({"corp_code": r[0], "stock_code": r[1], "corp_name": (r[2] or "").strip(), "rcept_no": r[3],
                        "rcept_date": r[4].strftime("%Y%m%d") if r[4] else None, "report_name": (r[5] or "").strip(),
                        "counterparty": r[6], "description": desc})
        return out
