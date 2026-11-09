"""공시 목록 및 단일판매·공급계약(수주) 공시 수집.

공시 목록 출처
- dart-market-monitor DB가 커버하는 기간(그 DB의 최초 공시일 이후): 공시알람 DB에서 읽음 (DART 호출 없음)
- 그 이전 과거 기간: OpenDART list.json (최초 1회, 이후 재사용)
- 공시알람 DB 미연결 시: OpenDART로 증분 수집 (마지막 수집일 − 7일부터 재확인)

수주계약
- 원문 텍스트는 공시알람 DB가 보관 중이면 재사용, 없으면 document.xml 1회 조회
- 정정공시는 '정정관련 공시서류제출일'·계약일·상대방으로 원계약과 같은 contract_key로 묶어 중복 집계를 막음
"""
from __future__ import annotations

import logging
import re
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Company
from app.models_analysis import ContractOrder, Disclosure, DisclosureScan
from app.services.dart import DartClient, DartError, viewer_url, xml_to_text
from app.services.monitor_source import MonitorSource

log = logging.getLogger(__name__)
HISTORY_YEARS = 3
CONTRACT_RE = re.compile(r"단일판매|공급계약")


def _ymd(d: date) -> str:
    return d.strftime("%Y%m%d")


def _upsert_disclosure(db: Session, company: Company, rcept_no: str, report_nm: str, rcept_dt: str, source: str) -> bool:
    if db.scalar(select(Disclosure.id).where(Disclosure.rcept_no == rcept_no)):
        return False
    db.add(Disclosure(company_id=company.id, rcept_no=rcept_no, report_nm=report_nm.strip(), rcept_dt=rcept_dt,
                      is_correction=report_nm.strip().startswith("[") and "정정" in report_nm[:8],
                      source=source, url=viewer_url(rcept_no)))
    return True


def collect_disclosure_list(db: Session, dart: DartClient, monitor: MonitorSource, company: Company,
                            today: date | None = None) -> dict:
    today = today or date.today()
    stats = {"dart_calls_ranges": 0, "monitor_rows": 0, "new": 0}
    if not company.corp_code:
        return stats
    want_from = _ymd(date(today.year - HISTORY_YEARS, today.month, 1))
    scan = db.get(DisclosureScan, company.id)
    texts: dict[str, str] = {}

    mon_start = monitor.coverage_start() if monitor.enabled else None
    dart_ranges: list[tuple[str, str]] = []
    if mon_start:
        hist_end = _ymd(date(int(mon_start[:4]), int(mon_start[4:6]), int(mon_start[6:])) - timedelta(days=1))
        if scan is None or scan.covered_from > want_from:
            dart_ranges.append((want_from, hist_end if scan is None else min(hist_end, scan.covered_from)))
        elif scan.covered_to < hist_end:
            dart_ranges.append((scan.covered_to, hist_end))
        for r in monitor.disclosures(company.corp_code, mon_start):
            stats["monitor_rows"] += 1
            stats["new"] += _upsert_disclosure(db, company, r["rcept_no"], r["report_nm"], r["rcept_dt"], "DART_MONITOR")
            if r.get("text_excerpt"):
                texts[r["rcept_no"]] = r["text_excerpt"]
    else:
        start = want_from if scan is None else _ymd(date(int(scan.covered_to[:4]), int(scan.covered_to[4:6]),
                                                         int(scan.covered_to[6:])) - timedelta(days=7))
        dart_ranges.append((start, _ymd(today)))

    for bgn, end in dart_ranges:
        if bgn > end:
            continue
        rows = dart.disclosures_all(company.corp_code, bgn, end)
        stats["dart_calls_ranges"] += 1
        for r in rows:
            stats["new"] += _upsert_disclosure(db, company, r["rcept_no"], r["report_nm"], r["rcept_dt"], "DART_LIST")

    if scan is None:
        scan = DisclosureScan(company_id=company.id, covered_from=want_from, covered_to=_ymd(today))
        db.add(scan)
    scan.covered_from = min(scan.covered_from, want_from)
    scan.covered_to = _ymd(today)
    db.commit()
    stats["texts"] = texts
    return stats


# ------------------------------------------------------------------ contract parsing
def _lines(text: str) -> list[str]:
    return [ln.strip() for ln in text.splitlines() if ln.strip()]


def _after(lines: list[str], labels: list[str], start: int = 0) -> tuple[str | None, int]:
    for i in range(start, len(lines)):
        ln = lines[i].replace(" ", "")
        for lab in labels:
            if ln == lab.replace(" ", "") or ln.startswith(lab.replace(" ", "")) and len(ln) <= len(lab.replace(" ", "")) + 2:
                if i + 1 < len(lines):
                    return lines[i + 1], i + 1
    return None, -1


def _amount(v: str | None) -> float | None:
    if not v:
        return None
    s = v.replace(",", "").strip()
    return float(s) if re.fullmatch(r"-?\d+(\.\d+)?", s) else None


def _date(v: str | None) -> str | None:
    if not v:
        return None
    m = re.search(r"(\d{4})[-.](\d{1,2})[-.](\d{1,2})", v)
    return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}" if m else None


def _norm(s: str | None) -> str:
    return re.sub(r"[\s\(\)㈜,.]|주식회사|co\.?|ltd\.?|inc\.?", "", (s or "").lower())[:40]


def parse_contract(text: str, report_nm: str) -> dict:
    lines = _lines(text)
    is_corr = "정정" in report_nm[:10] or any(ln.startswith("정정신고") for ln in lines[:15])
    out: dict = {"is_correction": is_corr, "is_cancellation": "해지" in report_nm}
    if is_corr:
        out["original_filed_date"] = _date(_after(lines, ["2. 정정관련 공시서류제출일"])[0])
        out["correction_reason"] = _after(lines, ["3. 정정사유"])[0]
    # body (post-correction full form) starts at the last '1. 판매ㆍ공급계약 ...' heading
    idxs = [i for i, ln in enumerate(lines) if re.match(r"1\.\s*판매ㆍ공급계약", ln) or re.match(r"1\.\s*해지", ln)]
    b = idxs[-1] if idxs else 0
    out["description"] = _after(lines, ["1. 판매ㆍ공급계약 내용", "- 체결계약명", "체결계약명", "1. 해지 계약명"], b)[0]
    amt_txt, _ = _after(lines, ["계약금액 총액(원)", "확정 계약금액", "계약금액(원)", "해지금액(원)"], b)
    out["amount"] = _amount(amt_txt)
    out["amount_disclosed"] = out["amount"] is not None
    out["recent_sales"] = _amount(_after(lines, ["최근 매출액(원)"], b)[0])
    ratio = _after(lines, ["매출액 대비(%)"], b)[0]
    out["sales_ratio"] = _amount(ratio)
    out["counterparty"] = _after(lines, ["3. 계약상대방", "계약상대방", "3. 계약상대"], b)[0]
    _, k = _after(lines, ["5. 계약기간", "계약기간"], b)
    out["start_date"] = _date(_after(lines, ["시작일"], max(b, k - 1))[0])
    out["end_date"] = _date(_after(lines, ["종료일"], max(b, k - 1))[0])
    out["contract_date"] = _date(_after(lines, ["8. 계약(수주)일자", "계약(수주)일자"], b)[0])
    out["region"] = _after(lines, ["4. 판매ㆍ공급지역"], b)[0]
    out["withheld_until"] = _date(_after(lines, ["유보기한"], b)[0])
    joined = " ".join(lines)
    out["completed"] = bool(re.search(r"이행\s*완료|계약이행완료|이행이\s*완료", (out.get("correction_reason") or "") + joined))
    if out["counterparty"] in ("-", None):
        out["counterparty"] = None
    key_date = out["contract_date"] or out["start_date"] or out.get("original_filed_date") or ""
    out["contract_key"] = f"{key_date}|{_norm(out['counterparty'])}|{_norm(out['description'])[:12]}"
    return out


def collect_contracts(db: Session, dart: DartClient, company: Company, texts: dict[str, str]) -> dict:
    stats = {"new": 0, "from_monitor_text": 0, "dart_docs": 0, "errors": 0}
    discs = db.scalars(select(Disclosure).where(Disclosure.company_id == company.id)).all()
    have = set(db.scalars(select(ContractOrder.rcept_no).where(ContractOrder.company_id == company.id)))
    for d in discs:
        if not CONTRACT_RE.search(d.report_nm) or d.rcept_no in have:
            continue
        text = texts.get(d.rcept_no)
        source = "DART_MONITOR"
        if text:
            stats["from_monitor_text"] += 1
        else:
            try:
                text = xml_to_text(dart.document_markup(d.rcept_no))
                stats["dart_docs"] += 1
                source = "DART"
            except DartError as e:
                stats["errors"] += 1
                log.warning("contract doc %s failed: %s", d.rcept_no, e)
                continue
        p = parse_contract(text, d.report_nm)
        if source == "DART_MONITOR" and not (p.get("contract_date") or p.get("counterparty") or p.get("amount")):
            # 공시알람 DB 원문은 줄바꿈 없이 저장된 경우가 있어 표 구조 파싱이 안 됨 → DART 원문으로 재파싱
            try:
                text = xml_to_text(dart.document_markup(d.rcept_no))
                stats["dart_docs"] += 1
                source = "DART"
                p = parse_contract(text, d.report_nm)
            except DartError as e:
                stats["errors"] += 1
                log.warning("contract doc %s failed: %s", d.rcept_no, e)
                continue
        if not (p.get("contract_date") or p.get("counterparty") or p.get("amount")):
            stats["errors"] += 1
            log.warning("contract %s could not be parsed", d.rcept_no)
        db.add(ContractOrder(company_id=company.id, rcept_no=d.rcept_no, report_nm=d.report_nm, filed_date=d.rcept_dt,
                             source=source, url=d.url, **p))
        stats["new"] += 1
        db.commit()
    return stats


def contract_book(db: Session, company_id: int, as_of: date) -> dict:
    """계약별 최신 상태(정정 반영) + 공시 기반 잔여 계약금액 추정.

    잔여금액 추정 = 계약금액 × (종료일까지 남은 기간 ÷ 전체 계약기간) — 기간 비례 가정(추정치).
    이행완료·해지 계약은 0. 금액 비공개 계약은 별도 집계(추정 불가).
    """
    rows = db.scalars(select(ContractOrder).where(ContractOrder.company_id == company_id)
                      .order_by(ContractOrder.filed_date, ContractOrder.rcept_no)).all()
    by_key: dict[str, list[ContractOrder]] = {}
    for r in rows:
        by_key.setdefault(r.contract_key, []).append(r)
    contracts = []
    for key, filings in by_key.items():
        latest = filings[-1]
        amount = next((f.amount for f in reversed(filings) if f.amount is not None), None)
        status = "cancelled" if any(f.is_cancellation for f in filings) else ("completed" if latest.completed else "active")
        remaining = None
        if amount is not None:
            if status != "active":
                remaining = 0.0
            elif latest.start_date and latest.end_date:
                s, e = date.fromisoformat(latest.start_date), date.fromisoformat(latest.end_date)
                total = max((e - s).days, 1)
                remaining = amount * min(1.0, max(0.0, (e - as_of).days / total))
            else:
                remaining = amount
        contracts.append({
            "key": key, "description": latest.description, "counterparty": latest.counterparty,
            "contract_date": latest.contract_date, "start_date": latest.start_date, "end_date": latest.end_date,
            "amount": amount, "amount_disclosed": amount is not None, "withheld_until": latest.withheld_until,
            "status": status, "remaining_estimate": remaining, "filings": len(filings),
            "first_rcept_no": filings[0].rcept_no, "latest_rcept_no": latest.rcept_no,
            "latest_url": latest.url, "first_filed": filings[0].filed_date, "latest_filed": latest.filed_date,
            "sales_ratio": latest.sales_ratio,
        })
    contracts.sort(key=lambda c: c["contract_date"] or c["first_filed"], reverse=True)
    active = [c for c in contracts if c["status"] == "active"]
    return {
        "contracts": contracts,
        "filings_total": len(rows),
        "contracts_total": len(contracts),
        "duplicates_merged": len(rows) - len(contracts),
        "active_count": len(active),
        "undisclosed_active": sum(1 for c in active if not c["amount_disclosed"]),
        "backlog_estimate": sum(c["remaining_estimate"] or 0 for c in active),
        "as_of": as_of.isoformat(),
    }
