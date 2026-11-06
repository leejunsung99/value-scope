"""ORM models.

Design notes
- `companies` and `corp_registry` / `source_documents` hold *shared* raw/master data
  that multiple client projects reuse.
- `projects`, `peer_*` hold *project-specific* work. Confirmed peer group versions are
  immutable: rows in `peer_version_members` are never updated after confirmation.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_uuid() -> str:
    return str(uuid.uuid4())


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


# ---------------------------------------------------------------- master data
class CorpRegistry(Base):
    """OpenDART 고유번호(corpCode.xml) 목록 캐시. 기업 검색·검증에 사용."""

    __tablename__ = "corp_registry"

    corp_code: Mapped[str] = mapped_column(String(8), primary_key=True)
    corp_name: Mapped[str] = mapped_column(String(200), index=True)
    corp_eng_name: Mapped[str | None] = mapped_column(String(300))
    stock_code: Mapped[str | None] = mapped_column(String(6), index=True)
    modify_date: Mapped[str | None] = mapped_column(String(8))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Company(TimestampMixin, Base):
    __tablename__ = "companies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # "dart:<corp_code>" for DART-registered companies, "manual:<normalized name>" otherwise.
    company_key: Mapped[str] = mapped_column(String(220), unique=True)
    corp_code: Mapped[str | None] = mapped_column(String(8), unique=True)
    stock_code: Mapped[str | None] = mapped_column(String(6), index=True)
    name: Mapped[str] = mapped_column(String(200))
    name_en: Mapped[str | None] = mapped_column(String(300))
    # KOSPI | KOSDAQ | KONEX | OTHER | UNLISTED | FOREIGN | UNKNOWN
    market: Mapped[str] = mapped_column(String(20), default="UNKNOWN")
    is_listed: Mapped[bool | None] = mapped_column(Boolean)
    country: Mapped[str] = mapped_column(String(2), default="KR")
    induty_code: Mapped[str | None] = mapped_column(String(10))
    ceo: Mapped[str | None] = mapped_column(String(200))
    homepage: Mapped[str | None] = mapped_column(String(300))
    # verified = 상장 상태와 기본정보를 DART 등 공식자료로 확인
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    verification_note: Mapped[str | None] = mapped_column(Text)
    info_source: Mapped[str | None] = mapped_column(String(100))
    info_fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SourceDocument(Base):
    """공식 원문 자료(공시 등) 메타데이터와 추출 텍스트. 원본 파일은 git 제외 data/ 폴더."""

    __tablename__ = "source_documents"
    __table_args__ = (UniqueConstraint("source", "source_id", name="uq_source_doc"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int | None] = mapped_column(ForeignKey("companies.id"), index=True)
    source: Mapped[str] = mapped_column(String(30))  # DART | KRX | IR | WEB | USER
    source_id: Mapped[str] = mapped_column(String(100))  # e.g. DART rcept_no
    doc_type: Mapped[str] = mapped_column(String(50))  # e.g. BUSINESS_REPORT
    title: Mapped[str] = mapped_column(String(500))
    url: Mapped[str | None] = mapped_column(String(500))
    filed_date: Mapped[str | None] = mapped_column(String(8))
    period: Mapped[str | None] = mapped_column(String(20))
    extracted_section: Mapped[str | None] = mapped_column(String(100))
    extracted_text: Mapped[str | None] = mapped_column(Text)
    content_hash: Mapped[str | None] = mapped_column(String(64))
    is_correction: Mapped[bool] = mapped_column(Boolean, default=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# ---------------------------------------------------------------- projects
class Project(TimestampMixin, Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), unique=True)
    title: Mapped[str] = mapped_column(String(200))
    current_step: Mapped[int] = mapped_column(Integer, default=1)
    last_analysis_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_analysis_status: Mapped[str | None] = mapped_column(String(20))

    company: Mapped[Company] = relationship(lazy="joined")
    peer_group: Mapped[PeerGroup | None] = relationship(
        back_populates="project", uselist=False, lazy="selectin"
    )


# ---------------------------------------------------------------- peer search
class PeerSearchSession(Base):
    __tablename__ = "peer_search_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    status: Mapped[str] = mapped_column(String(20), default="running")  # running|succeeded|partial|failed
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rubric_version: Mapped[str] = mapped_column(String(20))
    ai_provider: Mapped[str | None] = mapped_column(String(30))
    ai_model: Mapped[str | None] = mapped_column(String(100))
    # list of {"level": "info|warning|error", "message": str}
    messages: Mapped[list] = mapped_column(JSON, default=list)
    # list of source_documents ids / descriptions used
    sources_used: Mapped[list] = mapped_column(JSON, default=list)

    candidates: Mapped[list[PeerCandidate]] = relationship(
        back_populates="session", lazy="selectin", order_by="PeerCandidate.id"
    )


class PeerCandidate(Base):
    __tablename__ = "peer_candidates"
    __table_args__ = (UniqueConstraint("session_id", "company_id", name="uq_candidate_session_company"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("peer_search_sessions.id"), index=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"))
    category: Mapped[str] = mapped_column(String(1))  # A..E
    use_type: Mapped[str] = mapped_column(String(30))  # valuation|industry_reference|tech_competition
    # seed = 초기 후보 목록, report_mention = 사업보고서 본문 언급, ai = AI 추천
    origin: Mapped[str] = mapped_column(String(20))
    main_business: Mapped[str | None] = mapped_column(Text)
    main_products: Mapped[str | None] = mapped_column(Text)
    similarity: Mapped[str | None] = mapped_column(Text)
    selection_reason: Mapped[str | None] = mapped_column(Text)
    caution: Mapped[str | None] = mapped_column(Text)
    score: Mapped[float | None] = mapped_column(Float)
    score_coverage: Mapped[float | None] = mapped_column(Float)
    score_breakdown: Mapped[list] = mapped_column(JSON, default=list)
    sources: Mapped[list] = mapped_column(JSON, default=list)
    verification_status: Mapped[str] = mapped_column(String(20), default="unverified")
    verification_note: Mapped[str | None] = mapped_column(Text)

    session: Mapped[PeerSearchSession] = relationship(back_populates="candidates")
    company: Mapped[Company] = relationship(lazy="joined")


class PeerGroup(TimestampMixin, Base):
    """프로젝트별 비교군 '작업본'(임시 편집 상태). 확정 시 PeerGroupVersion으로 고정."""

    __tablename__ = "peer_groups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), unique=True)
    latest_version_no: Mapped[int] = mapped_column(Integer, default=0)
    draft_dirty: Mapped[bool] = mapped_column(Boolean, default=False)

    project: Mapped[Project] = relationship(back_populates="peer_group")
    memberships: Mapped[list[PeerMembership]] = relationship(
        back_populates="group", lazy="selectin", order_by="PeerMembership.id", cascade="all, delete-orphan"
    )
    versions: Mapped[list[PeerGroupVersion]] = relationship(
        back_populates="group", lazy="selectin", order_by="PeerGroupVersion.version_no"
    )


class PeerMembership(TimestampMixin, Base):
    """작업본 비교군의 기업 1건. status=excluded 로 제외/복원."""

    __tablename__ = "peer_memberships"
    __table_args__ = (UniqueConstraint("group_id", "company_id", name="uq_membership_group_company"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("peer_groups.id"), index=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"))
    candidate_id: Mapped[int | None] = mapped_column(ForeignKey("peer_candidates.id"))
    category: Mapped[str] = mapped_column(String(1))
    use_type: Mapped[str] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(20), default="proposed")  # proposed|included|excluded
    added_by: Mapped[str] = mapped_column(String(20))  # seed|report_mention|ai|user
    note: Mapped[str | None] = mapped_column(Text)

    group: Mapped[PeerGroup] = relationship(back_populates="memberships")
    company: Mapped[Company] = relationship(lazy="joined")
    candidate: Mapped[PeerCandidate | None] = relationship(lazy="joined")


class PeerGroupVersion(Base):
    __tablename__ = "peer_group_versions"
    __table_args__ = (UniqueConstraint("group_id", "version_no", name="uq_group_version"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("peer_groups.id"), index=True)
    version_no: Mapped[int] = mapped_column(Integer)
    label: Mapped[str] = mapped_column(String(200))
    note: Mapped[str | None] = mapped_column(Text)
    confirmed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    group: Mapped[PeerGroup] = relationship(back_populates="versions")
    members: Mapped[list[PeerVersionMember]] = relationship(
        back_populates="version", lazy="selectin", order_by="PeerVersionMember.id"
    )


class PeerVersionMember(Base):
    """확정 버전의 구성 기업 — 불변(immutable) 스냅샷."""

    __tablename__ = "peer_version_members"
    __table_args__ = (UniqueConstraint("version_id", "company_id", name="uq_version_company"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    version_id: Mapped[int] = mapped_column(ForeignKey("peer_group_versions.id"), index=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"))
    category: Mapped[str] = mapped_column(String(1))
    use_type: Mapped[str] = mapped_column(String(30))
    score: Mapped[float | None] = mapped_column(Float)
    snapshot: Mapped[dict] = mapped_column(JSON, default=dict)

    version: Mapped[PeerGroupVersion] = relationship(back_populates="members")
    company: Mapped[Company] = relationship(lazy="joined")
