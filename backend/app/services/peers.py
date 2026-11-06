"""동종업계 탐색 파이프라인.

1. 고객사 기업정보·최신 사업보고서(DART) 확보
2. 초기 후보(seed) 검증 — DART 기업목록 대조
3. 사업보고서 '사업의 내용' 본문에 언급된 상장사 탐지 (원문 발췌 근거)
4. 후보별 사업보고서 '사업의 개요' 발췌 확보
5. (AI 설정 시) 분류·루브릭 평가 — 인용문은 원문 대조 검증
6. 비교 적합성 점수 산출, 후보 저장, 작업본 비교군에 병합

어떤 단계든 인증정보·데이터가 없으면 추정하지 않고 메시지로 남긴다.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Company,
    CorpRegistry,
    PeerCandidate,
    PeerGroup,
    PeerMembership,
    PeerSearchSession,
    Project,
    SourceDocument,
    utcnow,
)
from app.services import companies as company_svc
from app.services.ai import AIError, AIProvider, AIUnavailable, NoAIProvider
from app.services.dart import DartClient, DartError, DartNoData, DartNotConfigured, extract_business_section, viewer_url
from app.services.peer_rubric import (
    CRITERIA,
    RUBRIC_VERSION,
    build_breakdown,
    compute_score,
    data_availability,
    default_use_type,
)
from app.services.peer_seeds import SEED_CANDIDATES

log = logging.getLogger(__name__)

MAX_MENTION_CANDIDATES = 20
# 상장사 이름이지만 사업보고서 본문에서 일반명사로 흔히 쓰이는 단어 (본문 언급 탐지에서 제외)
COMMON_WORD_NAMES = {
    "대상", "디바이스", "서비스", "시스템", "솔루션", "에너지", "케미칼", "테크", "코스모", "미래산업",
    "한국", "대한", "동방", "세원", "신원", "우리", "전방", "전기", "정상", "진원", "태양", "영원",
}
MAX_CANDIDATE_REPORTS = 25
OVERVIEW_EXCERPT_CHARS = 3000
AI_TARGET_TEXT_CHARS = 150_000

CATEGORY_LABELS = {
    "A": "직접 경쟁기업",
    "B": "유관 장비·부품기업",
    "C": "전방산업·주요 고객기업",
    "D": "소재·기술 관련 기업",
    "E": "기타 관련 기업",
}
USE_TYPE_LABELS = {
    "valuation": "적정가치 비교용",
    "industry_reference": "산업 참고용",
    "tech_competition": "기술 경쟁용",
}


@dataclass
class Draft:
    company: Company
    category: str
    origin: str
    sources: list[dict] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    cautions: list[str] = field(default_factory=list)
    verification_status: str = "unverified"
    verification_notes: list[str] = field(default_factory=list)
    main_business: str | None = None
    main_products: str | None = None
    similarity: str | None = None
    assessments: dict[str, dict] = field(default_factory=dict)
    mention_count: int = 0
    overview_excerpt: str | None = None


class SearchLog:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    def info(self, m: str) -> None:
        self.messages.append({"level": "info", "message": m})

    def warn(self, m: str) -> None:
        self.messages.append({"level": "warning", "message": m})

    def error(self, m: str) -> None:
        self.messages.append({"level": "error", "message": m})


# --------------------------------------------------------------------- helpers
def _norm_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def get_business_report(db: Session, dart: DartClient, company: Company, slog: SearchLog) -> SourceDocument | None:
    """최신 사업보고서의 '사업의 내용'을 확보. 이미 저장된 접수번호면 재다운로드하지 않는다."""
    if not company.corp_code:
        return None
    try:
        latest = dart.latest_business_report(company.corp_code)
    except DartError as e:
        slog.warn(f"{company.name}: 사업보고서 목록 조회 실패 — {e}")
        return None
    if not latest:
        slog.warn(f"{company.name}: 최근 2년 내 사업보고서 공시가 없습니다.")
        return None
    rcept_no = latest["rcept_no"]
    doc = db.scalar(select(SourceDocument).where(SourceDocument.source == "DART", SourceDocument.source_id == rcept_no))
    if doc and doc.extracted_text:
        return doc
    try:
        markup = dart.document_markup(rcept_no)
    except DartError as e:
        slog.warn(f"{company.name}: 사업보고서 원문 다운로드 실패 — {e}")
        return None
    section = extract_business_section(markup)
    if not section:
        slog.warn(f"{company.name}: 사업보고서에서 'II. 사업의 내용' 섹션을 찾지 못했습니다.")
    if doc is None:
        doc = SourceDocument(source="DART", source_id=rcept_no)
        db.add(doc)
    doc.company_id = company.id
    doc.doc_type = "BUSINESS_REPORT"
    doc.title = latest.get("report_nm", "사업보고서").strip()
    doc.url = viewer_url(rcept_no)
    doc.filed_date = latest.get("rcept_dt")
    doc.is_correction = "정정" in doc.title
    doc.extracted_section = "II. 사업의 내용" if section else None
    doc.extracted_text = section
    doc.content_hash = hashlib.sha256((section or "").encode("utf-8")).hexdigest()
    doc.fetched_at = utcnow()
    db.flush()
    return doc


def source_ref(doc: SourceDocument, note: str | None = None) -> dict:
    return {
        "type": "DART",
        "title": doc.title,
        "url": doc.url,
        "rcept_no": doc.source_id,
        "filed_date": doc.filed_date,
        "section": doc.extracted_section,
        "note": note,
    }


def overview_excerpt(text: str | None, limit: int = OVERVIEW_EXCERPT_CHARS) -> str | None:
    """'1. 사업의 개요' 첫 부분(원문 그대로) 발췌."""
    if not text:
        return None
    m = re.search(r"1\s*\.\s*사업의\s*개요", text)
    start = m.end() if m else 0
    return text[start: start + limit].strip() or None


def find_mentions(text: str, registry: list[CorpRegistry], exclude_codes: set[str]) -> dict[str, dict]:
    """본문에 언급된 상장사 탐지. 긴 이름 우선으로 겹치는 짧은 이름 매칭을 제거한다.

    Returns corp_code -> {"count": n, "snippet": str}
    """
    if not text:
        return {}
    hangul = r"[가-힣A-Za-z0-9]"
    particles = r"(?:은|는|이|가|을|를|의|와|과|에|로|으로|도|등|社|사)?"
    covered: list[tuple[int, int]] = []
    found: dict[str, dict] = {}
    entries = sorted(
        (r for r in registry if r.stock_code and r.corp_code not in exclude_codes and len(r.corp_name) >= 2
         and r.corp_name not in COMMON_WORD_NAMES),
        key=lambda r: len(r.corp_name),
        reverse=True,
    )
    for r in entries:
        name = r.corp_name
        if len(name) >= 4:
            pattern = re.escape(name)
        else:
            # 짧은 이름은 앞뒤가 다른 글자와 붙어있지 않을 때만(조사 허용)
            pattern = rf"(?<!{hangul}){re.escape(name)}(?={particles}(?!{hangul}))"
        hits = []
        for m in re.finditer(pattern, text):
            s, e = m.start(), m.start() + len(name)
            if any(cs <= s and e <= ce for cs, ce in covered):
                continue
            hits.append((s, e))
        if not hits:
            continue
        covered.extend(hits)
        s, e = hits[0]
        snippet = _norm_ws(text[max(0, s - 80): e + 80])
        found[r.corp_code] = {"count": len(hits), "snippet": snippet}
    return found


def verify_quote(quote: str, *texts: str | None) -> bool:
    q = _norm_ws(quote)
    if len(q) < 8:
        return False
    return any(t and q in _norm_ws(t) for t in texts)


# --------------------------------------------------------------------- AI step
AI_SYSTEM = (
    "당신은 한국 상장사 동종업계 분석을 돕는 금융 리서치 보조자입니다. "
    "제공된 DART 공시 원문 발췌에 근거해서만 판단하고, 근거가 없으면 해당 항목을 'unknown'으로 두십시오. "
    "evidence_quotes에는 제공된 원문에서 그대로 복사한 문장만 넣으십시오(요약·의역 금지). "
    "투자 추천이 아니라 고객사와의 사업 비교 적합성을 평가합니다."
)


def _ai_schema() -> dict:
    level = {"type": "string", "enum": ["1", "0.5", "0", "unknown"]}
    crit = {
        "type": "object",
        "properties": {"key": {"type": "string", "enum": [c.key for c in CRITERIA if c.key != "data"]},
                       "level": level, "rationale": {"type": "string"}},
        "required": ["key", "level", "rationale"],
        "additionalProperties": False,
    }
    cand = {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "stock_code": {"type": "string"},
            "category": {"type": "string", "enum": list(CATEGORY_LABELS)},
            "main_business": {"type": "string"},
            "main_products": {"type": "string"},
            "similarity": {"type": "string"},
            "selection_reason": {"type": "string"},
            "caution": {"type": "string"},
            "evidence_quotes": {"type": "array", "items": {"type": "string"}},
            "criteria": {"type": "array", "items": crit},
            "is_new_suggestion": {"type": "boolean"},
            "relevance": {"type": "string", "enum": ["related", "unrelated_mention"]},
        },
        "required": ["name", "stock_code", "category", "main_business", "main_products", "similarity",
                     "selection_reason", "caution", "evidence_quotes", "criteria", "is_new_suggestion", "relevance"],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {
            "target_summary": {
                "type": "object",
                "properties": {k: {"type": "string"} for k in ("main_business", "main_products", "customers", "revenue_structure")},
                "required": ["main_business", "main_products", "customers", "revenue_structure"],
                "additionalProperties": False,
            },
            "candidates": {"type": "array", "items": cand},
        },
        "required": ["target_summary", "candidates"],
        "additionalProperties": False,
    }


def build_ai_prompt(target: Company, target_text: str, drafts: list[Draft]) -> str:
    parts = [
        f"# 고객사: {target.name} ({target.stock_code or '비상장'})",
        "## 고객사 최신 사업보고서 'II. 사업의 내용' 원문",
        target_text[:AI_TARGET_TEXT_CHARS],
        "## 검토 대상 후보 기업",
    ]
    for d in drafts:
        parts.append(
            f"### {d.company.name} (종목코드 {d.company.stock_code or '없음'}, 시장 {d.company.market}, 출처 {d.origin})\n"
            f"사업의 개요 발췌:\n{d.overview_excerpt or '(공시 발췌 없음)'}"
        )
    parts.append(
        "## 지시\n"
        "1) 고객사의 주요 사업·제품·고객·매출구조를 원문 근거로 요약하십시오.\n"
        "2) 위 후보 각각을 A(직접 경쟁)/B(유관 장비·부품)/C(전방산업·고객)/D(소재·기술)/E(기타) 중 하나로 분류하고, "
        "루브릭 항목(product, process, customer, revenue, cycle)을 1/0.5/0/unknown으로 평가하십시오.\n"
        "3) 원문에 근거가 있는 추가 관련 기업이 있으면 is_new_suggestion=true로 추가하십시오(국내 상장사는 종목코드 포함). "
        "근거가 없으면 추가하지 마십시오.\n"
        "4) 비교 시 주의점(사업 비중 차이, 비상장, 지주사 구조 등)을 caution에 적으십시오.\n"
        "5) 출처가 '사업보고서 본문 언급'인 후보가 실제로는 일반명사이거나 사업과 무관한 단순 언급이면 "
        "relevance='unrelated_mention'으로 표시하고 selection_reason에 그 이유를 적으십시오. 그 외에는 'related'."
    )
    return "\n\n".join(parts)


def apply_ai(
    db: Session,
    ai: AIProvider,
    target: Company,
    target_doc: SourceDocument,
    drafts: list[Draft],
    slog: SearchLog,
    dart: DartClient,
    target_summary_out: dict,
) -> list[Draft]:
    try:
        result = ai.complete_json(AI_SYSTEM, build_ai_prompt(target, target_doc.extracted_text or "", drafts), _ai_schema())
    except AIUnavailable as e:
        slog.warn(f"AI 분류·평가 생략: {e}. 규칙 기반 결과만 표시합니다.")
        return drafts
    except AIError as e:
        slog.error(f"AI 분류·평가 실패: {e}. 규칙 기반 결과만 표시합니다.")
        return drafts
    slog.info(f"AI 분류·평가 완료 ({result.provider} / {result.model})")
    target_summary_out.update(result.data.get("target_summary") or {})

    by_code = {d.company.stock_code: d for d in drafts if d.company.stock_code}
    by_name = {company_svc.normalize_name(d.company.name): d for d in drafts}
    rejected_quotes = 0
    dropped: list[str] = []
    for item in result.data.get("candidates", []):
        code = (item.get("stock_code") or "").strip().zfill(6) if (item.get("stock_code") or "").strip().isdigit() else ""
        d = by_code.get(code) or by_name.get(company_svc.normalize_name(item.get("name", "")))
        if d is None:
            if not item.get("is_new_suggestion"):
                continue
            d = _resolve_ai_suggestion(db, dart, item, code, slog)
            if d is None:
                continue
            drafts.append(d)
            by_name[company_svc.normalize_name(d.company.name)] = d
        if item.get("relevance") == "unrelated_mention" and d.origin == "report_mention":
            dropped.append(f"{d.company.name}({item.get('selection_reason', '').strip()[:80]})")
            drafts.remove(d)
            continue
        d.category = item.get("category") or d.category
        d.main_business = item.get("main_business") or d.main_business
        d.main_products = item.get("main_products") or d.main_products
        d.similarity = item.get("similarity") or d.similarity
        if item.get("selection_reason"):
            d.reasons.append(f"[AI 판단] {item['selection_reason']}")
        if item.get("caution"):
            d.cautions.append(f"[AI 판단] {item['caution']}")
        verified_quotes = []
        for q in item.get("evidence_quotes") or []:
            if verify_quote(q, target_doc.extracted_text, d.overview_excerpt):
                verified_quotes.append(_norm_ws(q))
            else:
                rejected_quotes += 1
        for q in verified_quotes:
            d.sources.append({**source_ref(target_doc, "AI 인용 — 원문 대조 확인"), "quote": q})
        for c in item.get("criteria") or []:
            lv = c.get("level")
            if lv == "unknown" or c.get("key") not in {x.key for x in CRITERIA}:
                continue
            d.assessments[c["key"]] = {
                "level": float(lv),
                "rationale": c.get("rationale", ""),
                "origin": "ai",
                "evidence": verified_quotes[:2] or None,
            }
    if dropped:
        slog.info(f"AI 판단: 본문 단순 언급·무관 후보 {len(dropped)}개 제외 — " + "; ".join(dropped))
    if rejected_quotes:
        slog.warn(f"AI가 제시한 인용문 {rejected_quotes}건이 공시 원문에서 확인되지 않아 근거에서 제외했습니다.")
    return drafts


def _resolve_ai_suggestion(db: Session, dart: DartClient, item: dict, code: str, slog: SearchLog) -> Draft | None:
    reg = None
    if code:
        reg = db.scalar(select(CorpRegistry).where(CorpRegistry.stock_code == code))
    if reg is None:
        matches = company_svc.find_registry_by_name(db, item.get("name", ""))
        reg = next((m for m in matches if m.stock_code), matches[0] if matches else None)
    if reg is None:
        company = company_svc.get_or_create_manual_company(
            db, item.get("name", "").strip() or "이름 미상", note="AI 추천 — DART 기업목록에서 확인되지 않음"
        )
        d = Draft(company=company, category=item.get("category", "E"), origin="ai",
                  verification_status="unverified",
                  verification_notes=["DART 기업목록에서 확인되지 않음 — 해외기업이거나 명칭 불일치 가능"])
        return d
    try:
        company = company_svc.upsert_company_from_dart(db, dart, reg.corp_code)
    except DartError as e:
        slog.warn(f"AI 추천 {reg.corp_name}: 기업개황 조회 실패 — {e}")
        return None
    return Draft(company=company, category=item.get("category", "E"), origin="ai",
                 verification_status="verified", verification_notes=["DART 기업개황으로 상장 상태 확인"])


# --------------------------------------------------------------------- main
def run_peer_search(db: Session, project: Project, dart: DartClient, ai: AIProvider | None = None) -> PeerSearchSession:
    ai = ai or NoAIProvider()
    slog = SearchLog()
    session = PeerSearchSession(project_id=project.id, rubric_version=RUBRIC_VERSION, status="running",
                                ai_provider=ai.name, ai_model=getattr(ai, "model", None) or None)
    db.add(session)
    db.commit()
    try:
        _run(db, project, session, dart, ai, slog)
    except Exception as e:  # noqa: BLE001 - record any failure on the session
        log.exception("peer search failed")
        db.rollback()
        slog.error(f"동종업계 검색 중 오류: {type(e).__name__}: {e}")
        session = db.get(PeerSearchSession, session.id)
        session.status = "failed"
    session.messages = slog.messages
    session.finished_at = utcnow()
    db.commit()
    return session


def _run(db: Session, project: Project, session: PeerSearchSession, dart: DartClient, ai: AIProvider, slog: SearchLog) -> None:
    target = project.company
    seeds = SEED_CANDIDATES.get(target.stock_code or "", [])
    drafts: dict[int, Draft] = {}
    dart_ok = True

    try:
        company_svc.ensure_registry(db, dart)
    except DartNotConfigured:
        dart_ok = False
        slog.error("DART_API_KEY가 없어 공시 기반 검색·검증을 수행할 수 없습니다. 초기 후보 목록만 '미검증' 상태로 표시합니다.")
    except DartError as e:
        dart_ok = False
        slog.error(f"OpenDART 기업목록 조회 실패: {e}. 초기 후보 목록만 '미검증' 상태로 표시합니다.")

    # 1) target business report
    target_doc = None
    if dart_ok and target.corp_code:
        try:
            company_svc.upsert_company_from_dart(db, dart, target.corp_code)
        except DartError as e:
            slog.warn(f"고객사 기업개황 갱신 실패: {e}")
        target_doc = get_business_report(db, dart, target, slog)
        if target_doc:
            session.sources_used = [source_ref(target_doc, "고객사 사업보고서")]
            slog.info(f"고객사 사업보고서 확보: {target_doc.title} (접수번호 {target_doc.source_id})")
    elif dart_ok:
        slog.warn("고객사가 DART에 등록된 기업이 아니어서 사업보고서를 조회할 수 없습니다.")

    # 2) seeds
    for s in seeds:
        d = _seed_draft(db, dart if dart_ok else None, s, slog)
        if d and d.company.id != target.id:
            drafts.setdefault(d.company.id, d)
    if seeds:
        slog.info(f"초기 후보 {len(seeds)}개 검토 (사용자 제공 목록 — 고정 결과 아님)")

    # 3) mentions in target business report
    if target_doc and target_doc.extracted_text:
        registry = list(db.scalars(select(CorpRegistry).where(CorpRegistry.stock_code.is_not(None))))
        exclude = {target.corp_code} if target.corp_code else set()
        mentions = find_mentions(target_doc.extracted_text, registry, exclude)
        ranked = sorted(mentions.items(), key=lambda kv: kv[1]["count"], reverse=True)[:MAX_MENTION_CANDIDATES]
        new_found = 0
        for corp_code, m in ranked:
            try:
                company = company_svc.upsert_company_from_dart(db, dart, corp_code)
            except DartError as e:
                slog.warn(f"언급 기업 {corp_code} 기업개황 조회 실패: {e}")
                continue
            ref = {**source_ref(target_doc, f"고객사 사업보고서 본문 {m['count']}회 언급"), "quote": m["snippet"]}
            if company.id in drafts:
                drafts[company.id].sources.append(ref)
                drafts[company.id].mention_count = m["count"]
                drafts[company.id].reasons.append(f"고객사 사업보고서 본문에 {m['count']}회 언급됨")
                continue
            new_found += 1
            drafts[company.id] = Draft(
                company=company, category="E", origin="report_mention", sources=[ref],
                reasons=[f"고객사 사업보고서 '사업의 내용'에 {m['count']}회 언급됨 (검색으로 새로 발견)"],
                cautions=["단순 언급일 수 있음 — 언급 맥락(고객·경쟁·시장자료 인용 등)을 확인하고 카테고리를 지정하세요."],
                verification_status="verified", verification_notes=["DART 기업개황으로 상장 상태 확인"],
                mention_count=m["count"],
            )
        slog.info(f"사업보고서 본문에서 상장사 {len(mentions)}개 언급 탐지 — 신규 후보 {new_found}개 추가")

    # 4) candidate overviews
    if dart_ok:
        fetched = 0
        for d in drafts.values():
            if not d.company.corp_code or fetched >= MAX_CANDIDATE_REPORTS:
                continue
            doc = get_business_report(db, dart, d.company, slog)
            fetched += 1
            if doc and doc.extracted_text:
                d.overview_excerpt = overview_excerpt(doc.extracted_text)
                if not d.main_business and d.overview_excerpt:
                    d.main_business = "[공시 원문 발췌] " + _norm_ws(d.overview_excerpt[:400])
                d.sources.append(source_ref(doc, "후보 기업 사업보고서"))
        db.commit()

    # 5) AI classification
    draft_list = list(drafts.values())
    target_summary: dict = {}
    if target_doc and target_doc.extracted_text:
        draft_list = apply_ai(db, ai, target, target_doc, draft_list, slog, dart, target_summary)
    elif not isinstance(ai, NoAIProvider):
        slog.warn("고객사 사업보고서가 없어 AI 분석을 생략했습니다.")
    else:
        slog.info(f"AI 미사용: {getattr(ai, 'reason', '')}")
    if target_summary:
        session.sources_used = [*session.sources_used, {"type": "AI_SUMMARY", "summary": target_summary}]

    # 6) score + persist
    for d in draft_list:
        if d.company.id == target.id:
            continue
        d.assessments["data"] = data_availability(d.company.market, d.company.is_listed, d.company.is_verified)
        breakdown = build_breakdown(d.assessments)
        score, coverage = compute_score(breakdown)
        cand = PeerCandidate(
            session_id=session.id,
            company_id=d.company.id,
            category=d.category,
            use_type=default_use_type(d.category, d.company.is_listed),
            origin=d.origin,
            main_business=d.main_business,
            main_products=d.main_products,
            similarity=d.similarity,
            selection_reason="\n".join(dict.fromkeys(d.reasons)) or None,
            caution="\n".join(dict.fromkeys(d.cautions)) or None,
            score=score,
            score_coverage=coverage,
            score_breakdown=breakdown,
            sources=d.sources,
            verification_status=d.verification_status,
            verification_note="\n".join(d.verification_notes) or None,
        )
        db.add(cand)
        db.flush()
        merge_candidate_into_group(db, project, cand)

    # 사용자가 손대지 않은 자동 추천(proposed) 중 이번 검색에서 다시 발견되지 않은 기업은 제외 처리
    found_ids = {d.company.id for d in draft_list}
    group = get_or_create_group(db, project)
    stale = [m for m in group.memberships
             if m.status == "proposed" and m.added_by in ("report_mention", "ai") and m.company_id not in found_ids]
    for m in stale:
        m.status = "excluded"
        m.note = f"재검색(세션 {session.id})에서 다시 발견되지 않아 자동 제외 — 필요 시 복원 가능"
    if stale:
        slog.info(f"이전 검색의 자동 추천 중 이번에 발견되지 않은 {len(stale)}개를 제외 처리했습니다(복원 가능): "
                  + ", ".join(m.company.name for m in stale))

    session.status = "succeeded" if dart_ok and target_doc else ("partial" if draft_list else "failed")
    db.commit()


def _seed_draft(db: Session, dart: DartClient | None, s: dict, slog: SearchLog) -> Draft | None:
    hint = s.get("hint", "")
    if s.get("foreign_unlisted"):
        company = company_svc.get_or_create_manual_company(
            db, s["name"], market="FOREIGN", country=s.get("country", "JP"), is_listed=False,
            note="해외·비상장 기업으로 DART 대상이 아님 — 공식자료(회사 홈페이지 등) 확인 필요",
        )
        return Draft(
            company=company, category=s["category"], origin="seed",
            reasons=[f"초기 후보 목록: {hint}"],
            cautions=["상장 주가가 없어 주식가치 비교에는 사용하지 않고 기술 경쟁 분석에만 활용합니다."],
            verification_status="unverified", verification_notes=["DART 미등록 해외기업 — 상장 여부 공식 확인 필요"],
        )
    if dart is None:
        company = company_svc.get_or_create_manual_company(db, s["name"], note="DART 미연결로 검증 불가")
        return Draft(company=company, category=s["category"], origin="seed",
                     reasons=[f"초기 후보 목록: {hint}"], verification_status="unverified",
                     verification_notes=["DART_API_KEY 미설정으로 상장 상태·종목코드 검증 불가"])
    names = [s["name"], *s.get("aliases", [])]
    reg = None
    notes = []
    exp = s.get("expected_stock_code")
    if exp:
        reg = db.scalar(select(CorpRegistry).where(CorpRegistry.stock_code == exp))
        if reg and company_svc.normalize_name(reg.corp_name) not in {company_svc.normalize_name(n) for n in names}:
            notes.append(f"종목코드 {exp}의 DART 등록명은 '{reg.corp_name}' (후보명 '{s['name']}'과 다름 — 확인 필요)")
    if reg is None:
        for n in names:
            matches = [m for m in company_svc.find_registry_by_name(db, n) if m.stock_code]
            if matches:
                reg = matches[0]
                if exp and reg.stock_code != exp:
                    notes.append(f"기대 종목코드 {exp}와 DART 종목코드 {reg.stock_code} 불일치")
                break
    if reg is None:
        company = company_svc.get_or_create_manual_company(db, s["name"], note="DART 상장사 목록에서 찾지 못함")
        slog.warn(f"초기 후보 '{s['name']}'을 DART 상장사 목록에서 찾지 못했습니다 (상장폐지·사명변경 가능).")
        return Draft(company=company, category=s["category"], origin="seed", reasons=[f"초기 후보 목록: {hint}"],
                     verification_status="not_found", verification_notes=["DART 상장사 목록에서 찾지 못함"])
    try:
        company = company_svc.upsert_company_from_dart(db, dart, reg.corp_code)
    except DartError as e:
        slog.warn(f"초기 후보 '{s['name']}' 기업개황 조회 실패: {e}")
        return None
    cautions = []
    if not company.is_listed:
        notes.append(f"DART 법인구분상 현재 상장사가 아님({company.market}) — 상장폐지·이전 여부 확인 필요")
        cautions.append("현재 상장 주가가 확인되지 않아 주식가치 비교에는 사용할 수 없습니다.")
        slog.warn(f"초기 후보 '{s['name']}'({company.stock_code})은 DART 기준 현재 상장사가 아닙니다.")
    status = "mismatch" if notes else "verified"
    return Draft(company=company, category=s["category"], origin="seed", reasons=[f"초기 후보 목록: {hint}"],
                 cautions=cautions,
                 verification_status=status,
                 verification_notes=notes or [f"DART 확인: {company.name} / {company.stock_code} / {company.market}"])


# --------------------------------------------------------------------- draft group
def get_or_create_group(db: Session, project: Project) -> PeerGroup:
    group = db.scalar(select(PeerGroup).where(PeerGroup.project_id == project.id))
    if group is None:
        group = PeerGroup(project_id=project.id)
        db.add(group)
        db.flush()
    return group


def merge_candidate_into_group(db: Session, project: Project, cand: PeerCandidate) -> PeerMembership:
    """새 후보를 작업본에 반영. 이미 있는 기업은 사용자의 분류·채택 상태를 보존하고 근거만 최신화."""
    group = get_or_create_group(db, project)
    m = db.scalar(select(PeerMembership).where(PeerMembership.group_id == group.id,
                                               PeerMembership.company_id == cand.company_id))
    if m is None:
        m = PeerMembership(group_id=group.id, company_id=cand.company_id, candidate_id=cand.id,
                           category=cand.category, use_type=cand.use_type, status="proposed", added_by=cand.origin)
        db.add(m)
        group.draft_dirty = True
    else:
        m.candidate_id = cand.id
    db.flush()
    return m


def candidate_to_dict(c: PeerCandidate | None) -> dict | None:
    if c is None:
        return None
    return {
        "id": c.id,
        "session_id": c.session_id,
        "category": c.category,
        "use_type": c.use_type,
        "origin": c.origin,
        "main_business": c.main_business,
        "main_products": c.main_products,
        "similarity": c.similarity,
        "selection_reason": c.selection_reason,
        "caution": c.caution,
        "score": c.score,
        "score_coverage": c.score_coverage,
        "score_breakdown": c.score_breakdown,
        "sources": c.sources,
        "verification_status": c.verification_status,
        "verification_note": c.verification_note,
    }


def json_dumps(o) -> str:
    return json.dumps(o, ensure_ascii=False, default=str)
