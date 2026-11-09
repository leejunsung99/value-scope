from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select, text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_session
from app.models import Company, CorpRegistry, PeerGroup, PeerSearchSession, Project
from app.services import companies as company_svc
from app.services import peer_groups as pg
from app.services.ai import get_provider
from app.services.dart import DartClient, DartError, DartNotConfigured
from app.services.peer_rubric import rubric_definition
from app.services.peers import CATEGORY_LABELS, USE_TYPE_LABELS, candidate_to_dict, get_or_create_group, run_peer_search

router = APIRouter(prefix="/api")

STEP_LABELS = {1: "동종업계 탐색·확정", 2: "데이터 수집·분석", 3: "분석 결과", 4: "이력·재분석"}


def make_dart() -> DartClient:
    return DartClient(get_settings().dart_api_key)


def make_ai():
    return get_provider(get_settings())


def make_monitor():
    """공시알람 DB(읽기 전용). 설정이 없으면 None. (테스트에서 교체)"""
    from app.services.monitor_source import MonitorSource

    m = MonitorSource(get_settings().dart_monitor_database_url)
    return m if m.enabled else None


def get_dart() -> DartClient:
    return make_dart()


# --------------------------------------------------------------------- system
@router.get("/system/status")
def system_status(db: Session = Depends(get_session)):
    s = get_settings()
    db_ok = True
    try:
        db.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001
        db_ok = False
    return {
        "integrations": s.integration_status(),
        "database_ok": db_ok,
        "corp_registry_count": company_svc.registry_count(db) if db_ok else 0,
        "labels": {"categories": CATEGORY_LABELS, "use_types": USE_TYPE_LABELS, "steps": STEP_LABELS},
    }


# --------------------------------------------------------------------- companies
@router.get("/companies/search")
def search_companies(q: str = Query(min_length=1), listed_only: bool = False,
                     db: Session = Depends(get_session), dart: DartClient = Depends(get_dart)):
    try:
        company_svc.ensure_registry(db, dart)
    except DartNotConfigured as e:
        raise HTTPException(503, str(e))
    except DartError as e:
        raise HTTPException(502, f"OpenDART 기업목록 조회 실패: {e}")
    rows = company_svc.search_registry(db, q, listed_only=listed_only)
    codes = [r.corp_code for r in rows]
    proj = dict(db.execute(
        select(Company.corp_code, Project.id).join(Project, Project.company_id == Company.id)
        .where(Company.corp_code.in_(codes))
    ).all()) if codes else {}
    return [
        {"corp_code": r.corp_code, "corp_name": r.corp_name, "corp_eng_name": r.corp_eng_name,
         "stock_code": r.stock_code, "is_listed_hint": r.stock_code is not None,
         "existing_project_id": proj.get(r.corp_code)}
        for r in rows
    ]


@router.post("/companies/registry/refresh")
def refresh_registry(db: Session = Depends(get_session), dart: DartClient = Depends(get_dart)):
    try:
        n = company_svc.sync_registry(db, dart)
    except DartNotConfigured as e:
        raise HTTPException(503, str(e))
    except DartError as e:
        raise HTTPException(502, f"OpenDART 기업목록 조회 실패: {e}")
    return {"count": n}


def company_to_dict(c: Company) -> dict:
    return {
        "id": c.id, "name": c.name, "name_en": c.name_en, "corp_code": c.corp_code, "stock_code": c.stock_code,
        "market": c.market, "is_listed": c.is_listed, "country": c.country, "induty_code": c.induty_code,
        "ceo": c.ceo, "homepage": c.homepage, "is_verified": c.is_verified,
        "verification_note": c.verification_note, "info_source": c.info_source, "info_fetched_at": c.info_fetched_at,
    }


# --------------------------------------------------------------------- projects
class ManualCompany(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    stock_code: str | None = Field(default=None, pattern=r"^\d{6}$")
    market: Literal["KOSPI", "KOSDAQ", "KONEX", "UNLISTED", "FOREIGN", "UNKNOWN"] = "UNKNOWN"
    country: str = Field(default="KR", min_length=2, max_length=2)


class ProjectCreate(BaseModel):
    corp_code: str | None = Field(default=None, pattern=r"^\d{8}$")
    manual: ManualCompany | None = None


def _peer_state(db: Session, project: Project) -> dict:
    group = db.scalar(select(PeerGroup).where(PeerGroup.project_id == project.id))
    last_search = db.scalar(select(PeerSearchSession).where(PeerSearchSession.project_id == project.id)
                            .order_by(PeerSearchSession.id.desc()).limit(1))
    from app.models_analysis import AnalysisRun

    last_run = db.scalar(select(AnalysisRun).where(AnalysisRun.project_id == project.id)
                         .order_by(AnalysisRun.started_at.desc()).limit(1))
    done_run = db.scalar(select(AnalysisRun).where(AnalysisRun.project_id == project.id,
                                                   AnalysisRun.status.in_(["succeeded", "partial"]))
                         .order_by(AnalysisRun.started_at.desc()).limit(1))
    latest_no = group.latest_version_no if group else 0
    return {
        "last_run": {"id": last_run.id, "status": last_run.status, "started_at": last_run.started_at,
                     "peer_version_no": last_run.peer_version_no} if last_run else None,
        "latest_result_run_id": done_run.id if done_run else None,
        "needs_reanalysis": bool(latest_no) and (done_run is None or done_run.peer_version_no < latest_no),
        "latest_version_no": latest_no,
        "draft_dirty": pg.is_dirty(group) if group else False,
        "included_count": sum(1 for m in group.memberships if m.status == "included") if group else 0,
        "last_search": {"id": last_search.id, "status": last_search.status, "started_at": last_search.started_at,
                        "finished_at": last_search.finished_at} if last_search else None,
    }


def project_to_dict(db: Session, p: Project) -> dict:
    return {
        "id": p.id, "title": p.title, "current_step": p.current_step, "step_label": STEP_LABELS.get(p.current_step),
        "created_at": p.created_at, "updated_at": p.updated_at,
        "last_analysis_at": p.last_analysis_at, "last_analysis_status": p.last_analysis_status,
        "company": company_to_dict(p.company), "peer_state": _peer_state(db, p),
    }


def load_project(db: Session, project_id: str) -> Project:
    p = db.get(Project, project_id)
    if p is None:
        raise HTTPException(404, "프로젝트를 찾을 수 없습니다.")
    return p


@router.get("/projects")
def list_projects(q: str | None = None, db: Session = Depends(get_session)):
    stmt = select(Project).join(Company)
    if q:
        stmt = stmt.where(or_(Company.name.ilike(f"%{q}%"), Company.stock_code == q.strip(), Project.title.ilike(f"%{q}%")))
    rows = db.scalars(stmt.order_by(Project.updated_at.desc())).unique().all()
    return [project_to_dict(db, p) for p in rows]


@router.post("/projects", status_code=201)
def create_project(body: ProjectCreate, db: Session = Depends(get_session), dart: DartClient = Depends(get_dart)):
    if bool(body.corp_code) == bool(body.manual):
        raise HTTPException(422, "corp_code 또는 manual 중 하나만 지정하세요.")
    if body.corp_code:
        existing = db.scalar(select(Project).join(Company).where(Company.corp_code == body.corp_code))
        if existing:
            raise HTTPException(409, {"message": f"이미 등록된 기업입니다: {existing.company.name}",
                                      "existing_project_id": existing.id})
        try:
            company = company_svc.upsert_company_from_dart(db, dart, body.corp_code)
        except DartNotConfigured as e:
            raise HTTPException(503, str(e))
        except DartError as e:
            raise HTTPException(502, f"OpenDART 기업개황 조회 실패: {e}")
    else:
        m = body.manual
        if m.stock_code:
            dup = db.scalar(select(Project).join(Company).where(Company.stock_code == m.stock_code))
            if dup:
                raise HTTPException(409, {"message": f"이미 등록된 종목코드입니다: {dup.company.name}",
                                          "existing_project_id": dup.id})
        company = company_svc.get_or_create_manual_company(
            db, m.name, market=m.market, country=m.country.upper(),
            is_listed=m.market in ("KOSPI", "KOSDAQ", "KONEX") or None,
            note="사용자 수동 등록 — DART로 검증되지 않음",
        )
        if m.stock_code and not company.stock_code:
            company.stock_code = m.stock_code
        existing = db.scalar(select(Project).where(Project.company_id == company.id))
        if existing:
            raise HTTPException(409, {"message": f"이미 등록된 기업입니다: {company.name}",
                                      "existing_project_id": existing.id})
    p = Project(company_id=company.id, title=company.name)
    db.add(p)
    db.flush()
    get_or_create_group(db, p)
    db.commit()
    return project_to_dict(db, p)


@router.get("/projects/{project_id}")
def get_project(project_id: str, db: Session = Depends(get_session)):
    return project_to_dict(db, load_project(db, project_id))


class ProjectPatch(BaseModel):
    current_step: int | None = Field(default=None, ge=1, le=4)
    title: str | None = Field(default=None, min_length=1, max_length=200)


@router.patch("/projects/{project_id}")
def patch_project(project_id: str, body: ProjectPatch, db: Session = Depends(get_session)):
    p = load_project(db, project_id)
    if body.current_step is not None:
        p.current_step = body.current_step
    if body.title is not None:
        p.title = body.title
    db.commit()
    return project_to_dict(db, p)


# --------------------------------------------------------------------- peer search
def _run_search_task(project_id: str) -> None:
    from app.db import get_session as _gs

    gen = _gs()
    db = next(gen)
    try:
        from app import db as db_module
        from app.services.budget import CallBudget
        s = get_settings()
        project = db.get(Project, project_id)
        dart = make_dart()
        budget = CallBudget(db_module._SessionLocal, "DART", s.dart_daily_call_limit)
        dart.on_call = budget
        run_peer_search(db, project, dart, make_ai(), monitor=make_monitor(),
                        budget_remaining=budget.remaining())
    finally:
        gen.close()


@router.post("/projects/{project_id}/peer-search", status_code=202)
def start_peer_search(project_id: str, background: BackgroundTasks, db: Session = Depends(get_session)):
    load_project(db, project_id)
    running = db.scalar(select(PeerSearchSession).where(PeerSearchSession.project_id == project_id,
                                                        PeerSearchSession.status == "running"))
    if running:
        raise HTTPException(409, {"message": "이미 동종업계 검색이 진행 중입니다.", "session_id": running.id})
    background.add_task(_run_search_task, project_id)
    return {"status": "accepted"}


def session_to_dict(s: PeerSearchSession, with_candidates: bool = False) -> dict:
    d = {
        "id": s.id, "status": s.status, "started_at": s.started_at, "finished_at": s.finished_at,
        "rubric_version": s.rubric_version, "ai_provider": s.ai_provider, "ai_model": s.ai_model,
        "messages": s.messages, "sources_used": s.sources_used, "candidate_count": len(s.candidates),
    }
    if with_candidates:
        d["candidates"] = [{**candidate_to_dict(c), "company": company_to_dict(c.company)} for c in s.candidates]
    return d


@router.get("/projects/{project_id}/peer-search/sessions")
def list_search_sessions(project_id: str, db: Session = Depends(get_session)):
    load_project(db, project_id)
    rows = db.scalars(select(PeerSearchSession).where(PeerSearchSession.project_id == project_id)
                      .order_by(PeerSearchSession.id.desc())).all()
    return [session_to_dict(s) for s in rows]


@router.get("/projects/{project_id}/peer-search/sessions/{session_id}")
def get_search_session(project_id: str, session_id: int, db: Session = Depends(get_session)):
    s = db.get(PeerSearchSession, session_id)
    if s is None or s.project_id != project_id:
        raise HTTPException(404, "검색 세션을 찾을 수 없습니다.")
    return session_to_dict(s, with_candidates=True)


# --------------------------------------------------------------------- peer group
@router.get("/peers/rubric")
def get_rubric():
    return rubric_definition()


def group_to_dict(db: Session, project: Project) -> dict:
    group = get_or_create_group(db, project)
    db.commit()
    db.refresh(group)
    return {
        "group_id": group.id,
        "latest_version_no": group.latest_version_no,
        "draft_dirty": pg.is_dirty(group),
        "updated_at": group.updated_at,
        "members": [
            {"id": m.id, "category": m.category, "use_type": m.use_type, "status": m.status, "added_by": m.added_by,
             "note": m.note, "updated_at": m.updated_at, "company": company_to_dict(m.company),
             "candidate": candidate_to_dict(m.candidate)}
            for m in group.memberships
        ],
        "versions": [pg.version_to_dict(v, include_members=False) for v in group.versions],
    }


@router.get("/projects/{project_id}/peers")
def get_peers(project_id: str, db: Session = Depends(get_session)):
    return group_to_dict(db, load_project(db, project_id))


class PeerAdd(BaseModel):
    corp_code: str | None = Field(default=None, pattern=r"^\d{8}$")
    manual_name: str | None = Field(default=None, min_length=1, max_length=200)
    manual_country: str = Field(default="KR", min_length=2, max_length=2)
    category: Literal["A", "B", "C", "D", "E"]
    use_type: Literal["valuation", "industry_reference", "tech_competition"] | None = None
    note: str | None = None


@router.post("/projects/{project_id}/peers", status_code=201)
def add_peer(project_id: str, body: PeerAdd, db: Session = Depends(get_session), dart: DartClient = Depends(get_dart)):
    p = load_project(db, project_id)
    if bool(body.corp_code) == bool(body.manual_name):
        raise HTTPException(422, "corp_code 또는 manual_name 중 하나만 지정하세요.")
    if body.corp_code:
        try:
            company = company_svc.upsert_company_from_dart(db, dart, body.corp_code)
        except DartNotConfigured as e:
            raise HTTPException(503, str(e))
        except DartError as e:
            raise HTTPException(502, f"OpenDART 기업개황 조회 실패: {e}")
    else:
        company = company_svc.get_or_create_manual_company(
            db, body.manual_name, country=body.manual_country.upper(),
            market="FOREIGN" if body.manual_country.upper() != "KR" else "UNKNOWN",
            is_listed=None, note="사용자 직접 추가 — DART로 검증되지 않음")
    try:
        pg.add_member(db, p, company, body.category, body.use_type, body.note)
    except pg.PeerGroupError as e:
        db.rollback()
        raise HTTPException(e.code, str(e))
    db.commit()
    return group_to_dict(db, p)


class PeerPatch(BaseModel):
    category: Literal["A", "B", "C", "D", "E"] | None = None
    use_type: Literal["valuation", "industry_reference", "tech_competition"] | None = None
    status: Literal["proposed", "included", "excluded"] | None = None
    note: str | None = None


@router.patch("/projects/{project_id}/peers/{membership_id}")
def patch_peer(project_id: str, membership_id: int, body: PeerPatch, db: Session = Depends(get_session)):
    p = load_project(db, project_id)
    try:
        pg.update_member(db, p, membership_id, **body.model_dump())
    except pg.PeerGroupError as e:
        db.rollback()
        raise HTTPException(e.code, str(e))
    db.commit()
    return group_to_dict(db, p)


class BulkStatus(BaseModel):
    membership_ids: list[int]
    status: Literal["proposed", "included", "excluded"]


@router.post("/projects/{project_id}/peers/bulk-status")
def bulk_status(project_id: str, body: BulkStatus, db: Session = Depends(get_session)):
    p = load_project(db, project_id)
    try:
        for mid in body.membership_ids:
            pg.update_member(db, p, mid, status=body.status)
    except pg.PeerGroupError as e:
        db.rollback()
        raise HTTPException(e.code, str(e))
    db.commit()
    return group_to_dict(db, p)


class ConfirmBody(BaseModel):
    note: str | None = None


@router.post("/projects/{project_id}/peers/confirm", status_code=201)
def confirm_peers(project_id: str, body: ConfirmBody, db: Session = Depends(get_session)):
    p = load_project(db, project_id)
    try:
        v = pg.confirm(db, p, body.note)
    except pg.PeerGroupError as e:
        db.rollback()
        raise HTTPException(e.code, str(e))
    db.commit()
    return pg.version_to_dict(v)


@router.get("/projects/{project_id}/peers/versions/{version_no}")
def get_version(project_id: str, version_no: int, db: Session = Depends(get_session)):
    p = load_project(db, project_id)
    group = get_or_create_group(db, p)
    v = next((v for v in group.versions if v.version_no == version_no), None)
    if v is None:
        raise HTTPException(404, "해당 버전을 찾을 수 없습니다.")
    return pg.version_to_dict(v)


@router.get("/companies/registry/stats")
def registry_stats(db: Session = Depends(get_session)):
    return {"count": db.scalar(select(func.count()).select_from(CorpRegistry)) or 0,
            "stale": company_svc.registry_is_stale(db)}
