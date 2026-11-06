"""정기보고서 'II. 사업의 내용 > 수주상황' 표에서 수주잔고 합계를 결정적으로 추출."""
from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Company, SourceDocument, utcnow
from app.services.dart import DartClient, DartError, DartNoData, extract_business_section, viewer_url

UNIT = {"원": 1, "천원": 1e3, "백만원": 1e6, "억원": 1e8, "십억원": 1e9}
NUM = re.compile(r"^-?[\d,]+(\.\d+)?$")


def parse_backlog(text: str) -> dict | None:
    # the order-book sub-table (e.g. '다. 수주상황'), not the parent '4. 매출 및 수주상황' heading
    hm = list(re.finditer(r"(?m)^\s*[가-하]\.\s*수주\s*(상황|현황)", text))
    if not hm:
        return None
    i = hm[0].start()
    sec = text[i: i + 6000]
    end = re.search(r"\n\s*(\d+\.\s*(위험관리|파생|주요\s*계약|연구개발)|[가-하]\.\s*(?!수주))", sec[5:])
    if end:
        sec = sec[: end.start() + 5]
    if "수주잔고" not in sec:
        return None
    um = re.search(r"\(단위\s*[:：]\s*([^)]+)\)", sec)
    unit_txt = (um.group(1).strip().replace(" ", "") if um else "원")
    mult = next((v for k, v in sorted(UNIT.items(), key=lambda kv: -len(kv[0])) if unit_txt.startswith(k)), None)
    if mult is None:
        return None
    lines = [ln.strip() for ln in sec.splitlines() if ln.strip()]
    tot_idx = [k for k, ln in enumerate(lines) if ln.replace(" ", "") in ("합계", "총계")]
    if not tot_idx:
        return None
    k = tot_idx[-1] + 1
    tokens = []
    while k < len(lines) and (NUM.match(lines[k]) or lines[k] == "-"):
        tokens.append(lines[k])
        k += 1
    nums = [t for t in tokens if t != "-"]
    if not nums:
        return None
    raw = nums[-1]
    quote = " ".join(lines[tot_idx[-1]: k])
    vals = [float(t.replace(",", "")) for t in nums]
    # 산식 검증: 수주총액 − 기납품액 = 수주잔고 (금액 열 3개일 때)
    validated = len(vals) == 3 and vals[0] > 0 and abs(vals[0] - vals[1] - vals[2]) <= max(1.0, vals[0] * 0.01)
    if not validated:
        return None
    return {"amount": vals[-1] * mult, "amount_text": raw, "unit": unit_txt, "quote": quote, "row_tokens": tokens,
            "order_total": vals[0] * mult, "delivered": vals[1] * mult,
            "check": f"수주총액 {nums[0]} − 기납품액 {nums[1]} = 수주잔고 {nums[2]} ({unit_txt})"}


def latest_periodic_report(dart: DartClient, corp_code: str) -> dict | None:
    try:
        data = dart.disclosures(corp_code, pblntf_ty="A")
    except DartNoData:
        return None
    items = [i for i in data.get("list", []) if re.search(r"사업보고서|반기보고서|분기보고서", i.get("report_nm", ""))]
    items.sort(key=lambda i: (i.get("rcept_dt", ""), i.get("rcept_no", "")), reverse=True)
    return items[0] if items else None


def official_backlog(db: Session, dart: DartClient, company: Company) -> dict | None:
    """최근 정기보고서의 수주잔고 (공시 원문 표 기준). 실패 시 None."""
    if not company.corp_code:
        return None
    try:
        rep = latest_periodic_report(dart, company.corp_code)
    except DartError:
        return None
    if not rep:
        return None
    rcept = rep["rcept_no"]
    doc = db.scalar(select(SourceDocument).where(SourceDocument.source == "DART", SourceDocument.source_id == rcept))
    if doc is None or not doc.extracted_text:
        try:
            section = extract_business_section(dart.document_markup(rcept))
        except DartError:
            return None
        if doc is None:
            doc = SourceDocument(source="DART", source_id=rcept)
            db.add(doc)
        doc.company_id = company.id
        doc.doc_type = "BUSINESS_REPORT" if "사업보고서" in rep["report_nm"] else "PERIODIC_REPORT"
        doc.title = rep["report_nm"].strip()
        doc.url = viewer_url(rcept)
        doc.filed_date = rep.get("rcept_dt")
        doc.is_correction = "정정" in doc.title
        doc.extracted_section = "II. 사업의 내용" if section else None
        doc.extracted_text = section
        doc.fetched_at = utcnow()
        db.commit()
    if not doc.extracted_text:
        return None
    b = parse_backlog(doc.extracted_text)
    if not b:
        return None
    m = re.search(r"\((\d{4})\.(\d{2})\)", doc.title)
    as_of = f"{m.group(1)}-{m.group(2)}" if m else doc.filed_date
    return {**b, "rcept_no": rcept, "title": doc.title, "url": doc.url, "as_of": as_of, "filed_date": doc.filed_date}
