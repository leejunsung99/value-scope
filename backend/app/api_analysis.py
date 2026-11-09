"""PHASE 2-4 API: 분석 실행·진행률·결과·근거·이력 비교."""
from __future__ import annotations

import re
import threading

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import db as db_module
from app.config import get_settings
from app.db import get_session
from app.models import Company, Project
from app.models_analysis import AnalysisEvidence, AnalysisReport, AnalysisRun, Valuation
from app.services.budget import usage_summary

router = APIRouter(prefix="/api")


def make_clients():
    """Factory (monkeypatched in tests)."""
    from app.api import make_ai
    from app.services.collect_prices import KrxClient
    from app.services.dart import DartClient
    from app.services.monitor_source import MonitorSource

    s = get_settings()
    return DartClient(s.dart_api_key), KrxClient(s.krx_api_key), make_ai(), MonitorSource(s.dart_monitor_database_url)


def make_news_searcher():
    """뉴스 검색기 (Claude CLI 웹검색). AI_PROVIDER=claude_cli가 아니거나 CLI가 없으면 None. (테스트에서 교체)"""
    from app.services.news import ClaudeNewsSearcher, NewsUnavailable

    s = get_settings()
    if (s.ai_provider or "").lower() != "claude_cli":
        return None
    try:
        return ClaudeNewsSearcher(s.news_search_model)
    except NewsUnavailable:
        return None


def _start_thread(run_id: str) -> None:
    from app.services.analysis import execute_run

    dart, krx, ai, monitor = make_clients()
    db_module.get_engine()
    execute_run(db_module._SessionLocal, run_id, dart, krx, ai, monitor, news_searcher=make_news_searcher())


RUN_IN_THREAD = True  # tests set False to run synchronously


def run_to_dict(r: AnalysisRun, full: bool = False) -> dict:
    d = {"id": r.id, "project_id": r.project_id, "status": r.status, "started_at": r.started_at, "finished_at": r.finished_at,
         "peer_version_no": r.peer_version_no, "price_as_of": r.price_as_of, "model_version": r.model_version,
         "ai_provider": r.ai_provider, "ai_model": r.ai_model, "summary": r.summary, "progress": r.progress}
    if full:
        d.update({"messages": r.messages, "assumptions": r.assumptions, "data_versions": r.data_versions})
    return d


def _project(db: Session, pid: str) -> Project:
    p = db.get(Project, pid)
    if not p:
        raise HTTPException(404, "프로젝트를 찾을 수 없습니다.")
    return p


@router.get("/system/api-usage")
def api_usage(db: Session = Depends(get_session)):
    s = get_settings()
    return usage_summary(db, {"DART": s.dart_daily_call_limit, "KRX": s.krx_daily_call_limit})


class StartBody(BaseModel):
    include_news: bool = True


@router.post("/projects/{project_id}/analysis-runs", status_code=202)
def start_analysis(project_id: str, body: StartBody | None = None, db: Session = Depends(get_session)):
    from app.services.analysis import create_run

    p = _project(db, project_id)
    _, _, ai, _ = make_clients()
    try:
        run = create_run(db, p, ai, include_news=(body.include_news if body else True))
    except ValueError as e:
        raise HTTPException(422, str(e))
    except RuntimeError as e:
        raise HTTPException(409, str(e))
    if RUN_IN_THREAD:
        threading.Thread(target=_start_thread, args=(run.id,), daemon=True, name=f"analysis-{run.id[:8]}").start()
    else:
        _start_thread(run.id)
    db.refresh(run)
    return run_to_dict(run, full=True)


@router.get("/projects/{project_id}/analysis-runs")
def list_runs(project_id: str, db: Session = Depends(get_session)):
    _project(db, project_id)
    runs = db.scalars(select(AnalysisRun).where(AnalysisRun.project_id == project_id).order_by(AnalysisRun.started_at.desc())).all()
    return [run_to_dict(r) for r in runs]


def _run(db: Session, project_id: str, run_id: str) -> AnalysisRun:
    r = db.get(AnalysisRun, run_id)
    if not r or r.project_id != project_id:
        raise HTTPException(404, "분석 실행을 찾을 수 없습니다.")
    return r


@router.get("/projects/{project_id}/analysis-runs/{run_id}")
def get_run(project_id: str, run_id: str, db: Session = Depends(get_session)):
    return run_to_dict(_run(db, project_id, run_id), full=True)


def valuation_to_dict(v: Valuation, full: bool = True) -> dict:
    d = {"company_id": v.company_id, "name": v.company.name, "stock_code": v.company.stock_code, "market": v.company.market,
         "is_target": v.is_target, "category": v.category, "use_type": v.use_type, "verdict": v.verdict,
         "confidence": v.confidence, "fair_value": v.fair_value, "metrics": v.metrics}
    if full:
        d.update({"methods": v.methods, "confidence_breakdown": v.confidence_breakdown, "attribution": v.attribution,
                  "data_status": v.data_status,
                  "scenarios": [{"method": s.method, "scenario": s.scenario, "multiple_name": s.multiple_name, "multiple": s.multiple,
                                 "per_share": s.per_share, "equity_value": s.equity_value, "enterprise_value": s.enterprise_value}
                                for s in v.scenarios]})
    return d


@router.get("/projects/{project_id}/analysis-runs/{run_id}/results")
def get_results(project_id: str, run_id: str, db: Session = Depends(get_session)):
    r = _run(db, project_id, run_id)
    if r.status in ("queued", "running"):
        raise HTTPException(409, "분석이 아직 진행 중입니다.")
    vals = db.scalars(select(Valuation).where(Valuation.run_id == run_id)).all()
    rep = db.scalar(select(AnalysisReport).where(AnalysisReport.run_id == run_id))
    target = next((v for v in vals if v.is_target), None)
    history = []
    for prev in db.scalars(select(AnalysisRun).where(AnalysisRun.project_id == project_id,
                                                     AnalysisRun.status.in_(["succeeded", "partial"]))
                           .order_by(AnalysisRun.started_at)).all():
        fv = (prev.summary or {}).get("fair_value") or {}
        history.append({"run_id": prev.id, "date": prev.finished_at, "price_as_of": prev.price_as_of,
                        "price": (prev.summary or {}).get("price"), "verdict": (prev.summary or {}).get("verdict"), **fv})
    return {"run": run_to_dict(r, full=True),
            "target": valuation_to_dict(target) if target else None,
            "companies": [valuation_to_dict(v, full=False) for v in vals],
            "report": {"status": rep.status, "ai_model": rep.ai_model, "content": rep.content, "review": rep.review} if rep else None,
            "charts": {**(r.charts or {}), "history": history}}


@router.get("/projects/{project_id}/analysis-runs/{run_id}/companies/{company_id}")
def get_company_result(project_id: str, run_id: str, company_id: int, db: Session = Depends(get_session)):
    _run(db, project_id, run_id)
    v = db.scalar(select(Valuation).where(Valuation.run_id == run_id, Valuation.company_id == company_id))
    if not v:
        raise HTTPException(404, "해당 기업의 분석 결과가 없습니다.")
    target = db.scalar(select(Valuation).where(Valuation.run_id == run_id, Valuation.is_target.is_(True)))
    c = db.get(Company, company_id)
    return {"valuation": valuation_to_dict(v), "target": valuation_to_dict(target, full=False) if target else None,
            "company": {"name": c.name, "stock_code": c.stock_code, "market": c.market, "corp_code": c.corp_code,
                        "homepage": c.homepage, "ceo": c.ceo, "induty_code": c.induty_code, "verification_note": c.verification_note}}


@router.get("/projects/{project_id}/analysis-runs/{run_id}/evidence")
def list_evidence(project_id: str, run_id: str, ids: str | None = None, db: Session = Depends(get_session)):
    _run(db, project_id, run_id)
    stmt = select(AnalysisEvidence).where(AnalysisEvidence.run_id == run_id)
    if ids:
        stmt = stmt.where(AnalysisEvidence.eid.in_([i.strip() for i in ids.split(",") if i.strip()]))
    rows = db.scalars(stmt.order_by(AnalysisEvidence.id)).all()
    return [{k: getattr(e, k) for k in ("eid", "kind", "label", "value", "unit", "display", "period", "as_of", "company_id",
                                         "company_name", "method", "source", "url", "rcept_no", "impact", "detail")} for e in rows]


@router.get("/projects/{project_id}/analysis-runs/compare/{old_id}/{new_id}")
def compare_runs(project_id: str, old_id: str, new_id: str, db: Session = Depends(get_session)):
    a, b = _run(db, project_id, old_id), _run(db, project_id, new_id)
    va = db.scalar(select(Valuation).where(Valuation.run_id == a.id, Valuation.is_target.is_(True)))
    vb = db.scalar(select(Valuation).where(Valuation.run_id == b.id, Valuation.is_target.is_(True)))
    if not va or not vb:
        raise HTTPException(422, "두 실행 모두 고객사 평가 결과가 있어야 비교할 수 있습니다.")

    def g(v, *path):
        cur = v
        for p in path:
            cur = (cur or {}).get(p) if isinstance(cur, dict) else None
        return cur

    rows = []
    for label, path in [("적정주가(기준)", ("fair_value", "scenarios", "base")), ("적정주가(보수)", ("fair_value", "scenarios", "conservative")),
                        ("적정주가(낙관)", ("fair_value", "scenarios", "optimistic")), ("현재 주가", ("metrics", "values", "price")),
                        ("영업이익(TTM)", ("metrics", "values", "operating_income_ttm")), ("매출액(TTM)", ("metrics", "values", "revenue_ttm")),
                        ("EV/EBIT", ("metrics", "values", "ev_ebit")), ("PER", ("metrics", "values", "per")),
                        ("수주잔고", ("data_status", "backlog", "amount")), ("분석 신뢰도", ("confidence",))]:
        da = {"fair_value": va.fair_value, "metrics": va.metrics, "data_status": va.data_status, "confidence": va.confidence}
        dbb = {"fair_value": vb.fair_value, "metrics": vb.metrics, "data_status": vb.data_status, "confidence": vb.confidence}
        x, y = g(da, *path), g(dbb, *path)
        rows.append({"label": label, "old": x, "new": y,
                     "change": (y / x - 1) if isinstance(x, (int, float)) and isinstance(y, (int, float)) and x else None})
    peer_mult = {}
    for key in ("peer", "history"):
        ia = next((u for u in (va.methods or {}).get(key, {}).get("inputs", []) if u["key"] in ("ev_ebit", "per")), None)
        ib = next((u for u in (vb.methods or {}).get(key, {}).get("inputs", []) if u["key"] in ("ev_ebit", "per")), None)
        peer_mult[key] = {"old": ia and ia["quantiles"]["base"], "new": ib and ib["quantiles"]["base"], "multiple": (ib or ia or {}).get("multiple")}
    return {"old": run_to_dict(a), "new": run_to_dict(b), "rows": rows, "applied_multiples": peer_mult,
            "verdict": {"old": va.verdict, "new": vb.verdict},
            "peer_version": {"old": a.peer_version_no, "new": b.peer_version_no}}


# --------------------------------------------------------------------- PDF export
@router.get("/projects/{project_id}/analysis-runs/{run_id}/report.pdf")
def export_pdf(project_id: str, run_id: str, kind: str = "detail", db: Session = Depends(get_session)):
    """인쇄용 보고서 화면을 로컬 Edge/Chrome(headless)으로 PDF 변환해 내려준다.
    kind=detail: 상세 분석 보고서(장별 서술+그림·표+근거 부록) · kind=brief: 보고용 리포트(4쪽 내외)."""
    if kind not in ("detail", "brief"):
        raise HTTPException(422, "kind는 detail 또는 brief만 가능합니다.")
    from datetime import datetime
    from urllib.parse import quote

    from fastapi.responses import Response

    from app.main import DIST
    from app.services.pdf_export import PdfUnavailable, render_pdf

    r = _run(db, project_id, run_id)
    if r.status not in ("succeeded", "partial"):
        raise HTTPException(409, "완료된 분석만 PDF로 출력할 수 있습니다.")
    if not (DIST / "index.html").exists():
        raise HTTPException(409, "빌드된 화면이 없어 서버에서 PDF를 만들 수 없습니다(개발 모드). '브라우저 인쇄로 저장'을 이용하거나 start.ps1 -Rebuild로 실행하세요.")
    s = get_settings()
    host = s.app_host if s.app_host in ("127.0.0.1", "localhost") else "127.0.0.1"
    url = f"http://{host}:{s.app_port}/print/projects/{project_id}/runs/{run_id}" + ("/brief" if kind == "brief" else "")
    project = db.get(Project, project_id)
    name = project.company.name if project and project.company else "report"
    code = (project.company.stock_code if project and project.company else None) or ""
    day = (r.finished_at or datetime.now()).strftime("%Y%m%d")
    try:
        pdf = render_pdf(url, s.pdf_browser_path or None, footer=f"ValueScope  |  {code}  |  {day}  |  Not investment advice")
    except PdfUnavailable as e:
        raise HTTPException(503, str(e)) from e
    # 파일명: 회사명_분석리포트.pdf / 회사명_상세분석.pdf (파일명에 쓸 수 없는 문자는 제거)
    safe_name = re.sub(r'[\\/:*?"<>|]', "", name).strip() or "report"
    fname = f"{safe_name}_{'분석리포트' if kind == 'brief' else '상세분석'}.pdf"
    ascii_fallback = f"{code or 'report'}_{'report' if kind == 'brief' else 'detail'}.pdf"
    return Response(pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f"attachment; filename=\"{ascii_fallback}\"; filename*=UTF-8''{quote(fname)}",
                             "Cache-Control": "no-store"})
