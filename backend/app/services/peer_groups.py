"""비교군 작업본 편집 및 확정(버전) 관리."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Company, PeerGroup, PeerGroupVersion, PeerMembership, PeerVersionMember, Project, utcnow
from app.services.peers import CATEGORY_LABELS, USE_TYPE_LABELS, candidate_to_dict, get_or_create_group

VALID_STATUS = {"proposed", "included", "excluded"}


class PeerGroupError(ValueError):
    def __init__(self, message: str, code: int = 422):
        super().__init__(message)
        self.code = code


def validate_fields(company: Company, category: str | None, use_type: str | None, status: str | None) -> None:
    if category is not None and category not in CATEGORY_LABELS:
        raise PeerGroupError(f"알 수 없는 카테고리: {category}")
    if use_type is not None and use_type not in USE_TYPE_LABELS:
        raise PeerGroupError(f"알 수 없는 활용 구분: {use_type}")
    if use_type == "valuation" and not company.is_listed:
        raise PeerGroupError(f"{company.name}은(는) 상장 주가가 확인되지 않아 '적정가치 비교용'으로 지정할 수 없습니다.")
    if status is not None and status not in VALID_STATUS:
        raise PeerGroupError(f"알 수 없는 상태: {status}")


def add_member(db: Session, project: Project, company: Company, category: str, use_type: str | None,
               note: str | None = None) -> PeerMembership:
    if company.id == project.company_id:
        raise PeerGroupError("고객사 자신은 비교기업으로 추가할 수 없습니다.")
    from app.services.peer_rubric import default_use_type

    use_type = use_type or default_use_type(category, company.is_listed)
    validate_fields(company, category, use_type, None)
    group = get_or_create_group(db, project)
    m = db.scalar(select(PeerMembership).where(PeerMembership.group_id == group.id,
                                               PeerMembership.company_id == company.id))
    if m is not None:
        if m.status == "excluded":
            m.status = "included"
            m.category, m.use_type = category, use_type
            group.draft_dirty = True
            db.flush()
            return m
        raise PeerGroupError(f"{company.name}은(는) 이미 비교군 작업본에 있습니다.", 409)
    m = PeerMembership(group_id=group.id, company_id=company.id, category=category, use_type=use_type,
                       status="included", added_by="user", note=note)
    db.add(m)
    group.draft_dirty = True
    db.flush()
    return m


def update_member(db: Session, project: Project, membership_id: int, **changes) -> PeerMembership:
    group = get_or_create_group(db, project)
    m = db.get(PeerMembership, membership_id)
    if m is None or m.group_id != group.id:
        raise PeerGroupError("비교군 구성원을 찾을 수 없습니다.", 404)
    validate_fields(m.company, changes.get("category"), changes.get("use_type"), changes.get("status"))
    changed = False
    for k in ("category", "use_type", "status", "note"):
        v = changes.get(k)
        if v is not None and getattr(m, k) != v:
            setattr(m, k, v)
            changed = True
    if changed:
        group.draft_dirty = True
        group.updated_at = utcnow()
    db.flush()
    return m


def _current_signature(group: PeerGroup) -> set[tuple]:
    return {(m.company_id, m.category, m.use_type) for m in group.memberships if m.status == "included"}


def _version_signature(v: PeerGroupVersion) -> set[tuple]:
    return {(m.company_id, m.category, m.use_type) for m in v.members}


def is_dirty(group: PeerGroup) -> bool:
    if not group.versions:
        return bool(_current_signature(group))
    return _current_signature(group) != _version_signature(group.versions[-1])


def confirm(db: Session, project: Project, note: str | None = None) -> PeerGroupVersion:
    group = get_or_create_group(db, project)
    db.refresh(group)
    included = [m for m in group.memberships if m.status == "included"]
    if not included:
        raise PeerGroupError("채택된 비교기업이 없습니다. 최소 1개 기업을 채택한 뒤 확정하세요.")
    if group.versions and not is_dirty(group):
        raise PeerGroupError(f"마지막 확정본(v{group.latest_version_no})과 달라진 내용이 없습니다.", 409)
    no = group.latest_version_no + 1
    v = PeerGroupVersion(group_id=group.id, version_no=no, label=f"{project.company.name} 비교군 v{no}", note=note)
    db.add(v)
    db.flush()
    for m in included:
        cand = candidate_to_dict(m.candidate)
        db.add(PeerVersionMember(
            version_id=v.id, company_id=m.company_id, category=m.category, use_type=m.use_type,
            score=cand["score"] if cand else None,
            snapshot={
                "company": {"name": m.company.name, "stock_code": m.company.stock_code, "corp_code": m.company.corp_code,
                            "market": m.company.market, "is_listed": m.company.is_listed,
                            "is_verified": m.company.is_verified},
                "added_by": m.added_by,
                "note": m.note,
                "candidate": cand,
            },
        ))
    group.latest_version_no = no
    group.draft_dirty = False
    if project.current_step < 2:
        project.current_step = 2
    db.flush()
    db.refresh(group)
    return v


def version_to_dict(v: PeerGroupVersion, include_members: bool = True) -> dict:
    d = {
        "id": v.id,
        "version_no": v.version_no,
        "label": v.label,
        "note": v.note,
        "confirmed_at": v.confirmed_at,
        "member_count": len(v.members),
    }
    if include_members:
        d["members"] = [
            {"company_id": m.company_id, "category": m.category, "use_type": m.use_type, "score": m.score,
             "snapshot": m.snapshot}
            for m in v.members
        ]
    return d
