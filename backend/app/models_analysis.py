"""PHASE 2-3 models.

Shared raw data (reused by every project): api_call_log, financial_statements, financial_fetches,
financial_metrics, stock_prices, price_fetches, disclosures, disclosure_scans, contract_orders.

Project analysis snapshots (immutable after a run finishes): analysis_runs, valuations,
valuation_scenarios, analysis_evidence, analysis_reports.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models import Company, new_uuid, utcnow


class ApiCallLog(Base):
    """외부 API 호출 기록 — 공시알람과 키를 공유하므로 일일 호출 상한 관리에 사용."""

    __tablename__ = "api_call_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider: Mapped[str] = mapped_column(String(10), index=True)  # DART | KRX
    endpoint: Mapped[str] = mapped_column(String(60))
    status: Mapped[str | None] = mapped_column(String(10))
    called_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class FinancialReport(Base):
    """OpenDART 단일회사 전체 재무제표 원본(fnlttSinglAcntAll). 정정 시 접수번호별로 이력 보존."""

    __tablename__ = "financial_statements"
    __table_args__ = (UniqueConstraint("company_id", "bsns_year", "reprt_code", "rcept_no", name="uq_fin_report"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    bsns_year: Mapped[int] = mapped_column(Integer)
    reprt_code: Mapped[str] = mapped_column(String(5))  # 11013 1Q, 11012 반기, 11014 3Q, 11011 사업
    fs_div: Mapped[str] = mapped_column(String(3))  # CFS | OFS
    rcept_no: Mapped[str] = mapped_column(String(14))
    filed_date: Mapped[str] = mapped_column(String(8))
    rows: Mapped[list] = mapped_column(JSON, default=list)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class FinancialFetch(Base):
    """재무제표 조회 시도 기록(데이터 없음 포함) — 불필요한 재호출 방지."""

    __tablename__ = "financial_fetches"
    __table_args__ = (UniqueConstraint("company_id", "bsns_year", "reprt_code", name="uq_fin_fetch"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"))
    bsns_year: Mapped[int] = mapped_column(Integer)
    reprt_code: Mapped[str] = mapped_column(String(5))
    status: Mapped[str] = mapped_column(String(10))  # found | nodata | error
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class FinancialMetric(Base):
    """정규화 재무지표 (분기 3개월·회계연도·시점 잔액). 원본 보고서 접수번호와 연결."""

    __tablename__ = "financial_metrics"
    __table_args__ = (UniqueConstraint("company_id", "period_end", "basis", "metric", name="uq_fin_metric"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    period_end: Mapped[str] = mapped_column(String(10))  # YYYY-MM-DD
    basis: Mapped[str] = mapped_column(String(10))  # Q | FY | POINT
    metric: Mapped[str] = mapped_column(String(40))
    value: Mapped[float | None] = mapped_column(Float)
    fs_div: Mapped[str] = mapped_column(String(3))
    rcept_no: Mapped[str] = mapped_column(String(14))
    filed_date: Mapped[str] = mapped_column(String(8))
    derivation: Mapped[str | None] = mapped_column(Text)


class StockPrice(Base):
    __tablename__ = "stock_prices"
    __table_args__ = (UniqueConstraint("stock_code", "trade_date", name="uq_stock_price"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    stock_code: Mapped[str] = mapped_column(String(6), index=True)
    trade_date: Mapped[str] = mapped_column(String(8), index=True)
    market: Mapped[str] = mapped_column(String(6))
    name: Mapped[str | None] = mapped_column(String(100))
    close: Mapped[float | None] = mapped_column(Float)
    open: Mapped[float | None] = mapped_column(Float)
    high: Mapped[float | None] = mapped_column(Float)
    low: Mapped[float | None] = mapped_column(Float)
    volume: Mapped[float | None] = mapped_column(Float)
    trade_value: Mapped[float | None] = mapped_column(Float)
    market_cap: Mapped[float | None] = mapped_column(Float)
    listed_shares: Mapped[float | None] = mapped_column(Float)


class PriceFetch(Base):
    __tablename__ = "price_fetches"
    __table_args__ = (UniqueConstraint("market", "trade_date", name="uq_price_fetch"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    market: Mapped[str] = mapped_column(String(6))  # stk | ksq
    trade_date: Mapped[str] = mapped_column(String(8))
    row_count: Mapped[int] = mapped_column(Integer)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Disclosure(Base):
    __tablename__ = "disclosures"
    __table_args__ = (UniqueConstraint("rcept_no", name="uq_disclosure_rcept"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    rcept_no: Mapped[str] = mapped_column(String(14))
    report_nm: Mapped[str] = mapped_column(String(300))
    rcept_dt: Mapped[str] = mapped_column(String(8), index=True)
    is_correction: Mapped[bool] = mapped_column(Boolean, default=False)
    source: Mapped[str] = mapped_column(String(20))  # DART_LIST | DART_MONITOR
    url: Mapped[str] = mapped_column(String(300))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DisclosureScan(Base):
    """기업별 공시목록 수집 범위 — 증분 수집용."""

    __tablename__ = "disclosure_scans"

    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), primary_key=True)
    covered_from: Mapped[str] = mapped_column(String(8))
    covered_to: Mapped[str] = mapped_column(String(8))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class ContractOrder(Base):
    """단일판매·공급계약 공시 1건(원공시 또는 정정). contract_key로 같은 계약을 묶어 중복 집계를 막는다."""

    __tablename__ = "contract_orders"
    __table_args__ = (UniqueConstraint("rcept_no", name="uq_contract_rcept"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    rcept_no: Mapped[str] = mapped_column(String(14))
    report_nm: Mapped[str] = mapped_column(String(300))
    filed_date: Mapped[str] = mapped_column(String(8))
    is_correction: Mapped[bool] = mapped_column(Boolean, default=False)
    is_cancellation: Mapped[bool] = mapped_column(Boolean, default=False)
    original_filed_date: Mapped[str | None] = mapped_column(String(10))
    correction_reason: Mapped[str | None] = mapped_column(Text)
    contract_key: Mapped[str] = mapped_column(String(300), index=True)
    description: Mapped[str | None] = mapped_column(Text)
    counterparty: Mapped[str | None] = mapped_column(String(300))
    amount: Mapped[float | None] = mapped_column(Float)
    amount_disclosed: Mapped[bool] = mapped_column(Boolean, default=False)
    recent_sales: Mapped[float | None] = mapped_column(Float)
    sales_ratio: Mapped[float | None] = mapped_column(Float)
    start_date: Mapped[str | None] = mapped_column(String(10))
    end_date: Mapped[str | None] = mapped_column(String(10))
    contract_date: Mapped[str | None] = mapped_column(String(10))
    region: Mapped[str | None] = mapped_column(String(200))
    completed: Mapped[bool] = mapped_column(Boolean, default=False)
    withheld_until: Mapped[str | None] = mapped_column(String(10))
    source: Mapped[str] = mapped_column(String(20))
    url: Mapped[str] = mapped_column(String(300))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class NewsSearch(Base):
    """뉴스 검색 1회(검색 단위 = 기업 또는 주제). 같은 날 재검색 방지·이어서 검색(증분)·일일 상한에 사용."""

    __tablename__ = "news_searches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    query_key: Mapped[str] = mapped_column(String(200), index=True)   # company:<id> | theme:<project_id>:<name>
    company_id: Mapped[int | None] = mapped_column(ForeignKey("companies.id"), nullable=True)
    query: Mapped[str] = mapped_column(Text)
    since: Mapped[str] = mapped_column(String(10))
    until: Mapped[str] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(String(20))                   # succeeded | failed
    item_count: Mapped[int] = mapped_column(Integer, default=0)
    model: Mapped[str | None] = mapped_column(String(60))
    error: Mapped[str | None] = mapped_column(Text)
    searched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class NewsItem(Base):
    """검색으로 찾은 기사 메타데이터(제목·언론사·날짜·링크·짧은 요약). 기사 본문은 저장하지 않는다."""

    __tablename__ = "news_items"
    __table_args__ = (UniqueConstraint("query_key", "url", name="uq_news_query_url"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    query_key: Mapped[str] = mapped_column(String(200), index=True)
    company_id: Mapped[int | None] = mapped_column(ForeignKey("companies.id"), nullable=True)
    pub_date: Mapped[str | None] = mapped_column(String(10), index=True)
    date_certain: Mapped[bool] = mapped_column(Boolean, default=False)
    outlet: Mapped[str | None] = mapped_column(String(100))
    title: Mapped[str] = mapped_column(String(400))
    url: Mapped[str] = mapped_column(String(600))
    summary: Mapped[str | None] = mapped_column(Text)
    event: Mapped[str | None] = mapped_column(String(200))
    sentiment: Mapped[str | None] = mapped_column(String(10))        # positive | negative | neutral
    about: Mapped[str | None] = mapped_column(String(200))           # 기사의 주 대상 기업·주제
    link_status: Mapped[str] = mapped_column(String(20), default="unchecked")  # ok | unverified | unchecked
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AnalysisRun(Base):
    __tablename__ = "analysis_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    peer_version_id: Mapped[int] = mapped_column(ForeignKey("peer_group_versions.id"))
    peer_version_no: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="queued")  # queued|running|succeeded|partial|failed
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    price_as_of: Mapped[str | None] = mapped_column(String(8))
    model_version: Mapped[str] = mapped_column(String(40))
    ai_provider: Mapped[str | None] = mapped_column(String(30))
    ai_model: Mapped[str | None] = mapped_column(String(100))
    progress: Mapped[dict] = mapped_column(JSON, default=dict)
    messages: Mapped[list] = mapped_column(JSON, default=list)
    assumptions: Mapped[dict] = mapped_column(JSON, default=dict)
    data_versions: Mapped[dict] = mapped_column(JSON, default=dict)
    summary: Mapped[dict] = mapped_column(JSON, default=dict)
    charts: Mapped[dict] = mapped_column(JSON, default=dict)


class Valuation(Base):
    __tablename__ = "valuations"
    __table_args__ = (UniqueConstraint("run_id", "company_id", name="uq_valuation_run_company"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("analysis_runs.id"), index=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"))
    is_target: Mapped[bool] = mapped_column(Boolean, default=False)
    category: Mapped[str | None] = mapped_column(String(1))
    use_type: Mapped[str | None] = mapped_column(String(30))
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    methods: Mapped[dict] = mapped_column(JSON, default=dict)
    fair_value: Mapped[dict] = mapped_column(JSON, default=dict)
    verdict: Mapped[str | None] = mapped_column(String(20))
    confidence: Mapped[float | None] = mapped_column(Float)
    confidence_breakdown: Mapped[list] = mapped_column(JSON, default=list)
    attribution: Mapped[dict] = mapped_column(JSON, default=dict)
    data_status: Mapped[dict] = mapped_column(JSON, default=dict)

    company: Mapped[Company] = relationship(lazy="joined")
    scenarios: Mapped[list[ValuationScenario]] = relationship(lazy="selectin", order_by="ValuationScenario.id")


class ValuationScenario(Base):
    __tablename__ = "valuation_scenarios"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    valuation_id: Mapped[int] = mapped_column(ForeignKey("valuations.id"), index=True)
    method: Mapped[str] = mapped_column(String(20))  # forward | peer | history | combined
    scenario: Mapped[str] = mapped_column(String(15))  # conservative | base | optimistic
    multiple_name: Mapped[str | None] = mapped_column(String(20))
    multiple: Mapped[float | None] = mapped_column(Float)
    base_metric: Mapped[float | None] = mapped_column(Float)
    enterprise_value: Mapped[float | None] = mapped_column(Float)
    equity_value: Mapped[float | None] = mapped_column(Float)
    per_share: Mapped[float | None] = mapped_column(Float)
    detail: Mapped[dict] = mapped_column(JSON, default=dict)


class AnalysisEvidence(Base):
    __tablename__ = "analysis_evidence"
    __table_args__ = (UniqueConstraint("run_id", "eid", name="uq_evidence_run_eid"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("analysis_runs.id"), index=True)
    eid: Mapped[str] = mapped_column(String(20))
    kind: Mapped[str] = mapped_column(String(10))  # fact | estimate | calc
    label: Mapped[str] = mapped_column(String(300))
    value: Mapped[float | None] = mapped_column(Float)
    unit: Mapped[str | None] = mapped_column(String(20))
    display: Mapped[str | None] = mapped_column(String(100))
    period: Mapped[str | None] = mapped_column(String(60))
    as_of: Mapped[str | None] = mapped_column(String(20))
    company_id: Mapped[int | None] = mapped_column(ForeignKey("companies.id"))
    company_name: Mapped[str | None] = mapped_column(String(200))
    method: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str | None] = mapped_column(String(100))
    url: Mapped[str | None] = mapped_column(String(300))
    rcept_no: Mapped[str | None] = mapped_column(String(14))
    impact: Mapped[str | None] = mapped_column(Text)
    detail: Mapped[dict] = mapped_column(JSON, default=dict)


class AnalysisReport(Base):
    __tablename__ = "analysis_reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("analysis_runs.id"), unique=True)
    status: Mapped[str] = mapped_column(String(20))  # generated | rule_based | failed
    ai_model: Mapped[str | None] = mapped_column(String(100))
    content: Mapped[dict] = mapped_column(JSON, default=dict)
    review: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
