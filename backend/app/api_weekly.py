"""주간 주가 분석 API (통합 분석과 별도 분기)."""
from __future__ import annotations

import threading
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import db as db_module
from app.db import get_session
from app.models import Project
from app.models_analysis import WeeklyAnalysis
from app.services import weekly as wk
from app.services.weekly_run import create_weekly, execute_weekly, to_dict

router = APIRouter(prefix="/api")
RUN_IN_THREAD = True  # tests set False


class WeeklyBody(BaseModel):
    week_start: str
    include_news: bool = True


def _project(db: Session, pid: str) -> Project:
    p = db.get(Project, pid)
    if not p:
        raise HTTPException(404, "프로젝트를 찾을 수 없습니다.")
    return p


def _start(wid: str) -> None:
    from app.api_analysis import make_clients, make_news_searcher

    dart, krx, ai, monitor = make_clients()
    db_module.get_engine()
    execute_weekly(db_module._SessionLocal, wid, krx, dart, monitor, ai, news_searcher=make_news_searcher())


@router.get("/projects/{project_id}/weekly/range")
def weekly_range(project_id: str, db: Session = Depends(get_session)):
    """선택 가능한 주: 일별 시세 보관 범위(최근 370일) 안에서, 기대수익률 모형 학습(최소 60거래일 ≈ 13주) 여유를 둔 가장 이른 월요일 ~ 이번 주."""
    from app.services.collect_prices import sample_dates  # noqa: F401  (일별 보관 기간 정의: 370일)

    _project(db, project_id)
    today = date.today()
    this_mon = today - timedelta(days=today.weekday())
    first_daily = today - timedelta(days=370)
    earliest = first_daily - timedelta(days=first_daily.weekday()) + timedelta(weeks=14)
    return {"earliest": str(earliest), "latest": str(this_mon), "today": str(today)}


@router.post("/projects/{project_id}/weekly", status_code=202)
def start_weekly(project_id: str, body: WeeklyBody, db: Session = Depends(get_session)):
    p = _project(db, project_id)
    try:
        w = create_weekly(db, p, body.week_start, body.include_news)
    except wk.WeeklyError as e:
        raise HTTPException(422, str(e))
    except ValueError as e:
        raise HTTPException(422, str(e))
    except RuntimeError as e:
        raise HTTPException(409, str(e))
    if RUN_IN_THREAD:
        threading.Thread(target=_start, args=(w.id,), daemon=True, name=f"weekly-{w.id[:8]}").start()
    else:
        _start(w.id)
    db.refresh(w)
    return to_dict(w, full=True)


@router.get("/projects/{project_id}/weekly")
def list_weekly(project_id: str, db: Session = Depends(get_session)):
    _project(db, project_id)
    rows = db.scalars(select(WeeklyAnalysis).where(WeeklyAnalysis.project_id == project_id)
                      .order_by(WeeklyAnalysis.week_start.desc(), WeeklyAnalysis.started_at.desc()))
    return [to_dict(w) for w in rows]


@router.get("/projects/{project_id}/weekly/{weekly_id}/report.pdf")
def weekly_pdf(project_id: str, weekly_id: str, db: Session = Depends(get_session)):
    """주간 리포트 PDF: 인쇄용 화면을 로컬 Edge/Chrome(headless)으로 변환. 파일명 = 회사명_주간리포트_주시작일.pdf"""
    import re
    from urllib.parse import quote

    from fastapi.responses import Response

    from app.config import get_settings
    from app.main import DIST
    from app.services.pdf_export import PdfUnavailable, render_pdf

    w = db.get(WeeklyAnalysis, weekly_id)
    if not w or w.project_id != project_id:
        raise HTTPException(404, "주간 분석을 찾을 수 없습니다.")
    if w.status != "succeeded":
        raise HTTPException(409, "완료된 주간 분석만 PDF로 출력할 수 있습니다.")
    if not (DIST / "index.html").exists():
        raise HTTPException(409, "빌드된 화면이 없어 서버에서 PDF를 만들 수 없습니다(개발 모드). '브라우저 인쇄로 저장'을 이용하세요.")
    s = get_settings()
    host = s.app_host if s.app_host in ("127.0.0.1", "localhost") else "127.0.0.1"
    project = _project(db, project_id)
    name = project.company.name if project.company else "report"
    code = project.company.stock_code or "" if project.company else ""
    wk_tag = w.week_start.replace("-", "")
    try:
        pdf = render_pdf(f"http://{host}:{s.app_port}/print/projects/{project_id}/weekly/{weekly_id}", s.pdf_browser_path or None,
                         footer=f"ValueScope Weekly  |  {code}  |  week of {w.week_start}  |  Not investment advice")
    except PdfUnavailable as e:
        raise HTTPException(503, str(e)) from e
    safe = re.sub(r'[\\/:*?"<>|]', "", name).strip() or "report"
    fname = f"{safe}_주간리포트_{wk_tag}.pdf"
    return Response(pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f"attachment; filename=\"{code or 'report'}_weekly_{wk_tag}.pdf\"; filename*=UTF-8''{quote(fname)}",
                             "Cache-Control": "no-store"})


@router.get("/projects/{project_id}/weekly/{weekly_id}")
def get_weekly(project_id: str, weekly_id: str, db: Session = Depends(get_session)):
    w = db.get(WeeklyAnalysis, weekly_id)
    if not w or w.project_id != project_id:
        raise HTTPException(404, "주간 분석을 찾을 수 없습니다.")
    return to_dict(w, full=True)
