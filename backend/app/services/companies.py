"""Company master data: DART corp registry sync, search, and verified upsert."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from app.models import Company, CorpRegistry, utcnow
from app.services.dart import CORP_CLS_MARKET, DartClient, DartError, DartNoData

REGISTRY_MAX_AGE = timedelta(days=7)


def normalize_name(name: str) -> str:
    n = re.sub(r"\(주\)|㈜|주식회사|\s+", "", name or "")
    return n.lower()


def registry_count(db: Session) -> int:
    return db.scalar(select(func.count()).select_from(CorpRegistry)) or 0


def registry_is_stale(db: Session) -> bool:
    newest = db.scalar(select(func.max(CorpRegistry.fetched_at)))
    if newest is None:
        return True
    if newest.tzinfo is None:
        newest = newest.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - newest > REGISTRY_MAX_AGE


def sync_registry(db: Session, dart: DartClient) -> int:
    """Replace the cached corp registry with the latest OpenDART corpCode.xml."""
    entries = dart.corp_codes()
    now = utcnow()
    db.execute(delete(CorpRegistry))
    db.bulk_insert_mappings(
        CorpRegistry,
        [
            {
                "corp_code": e.corp_code,
                "corp_name": e.corp_name,
                "corp_eng_name": e.corp_eng_name,
                "stock_code": e.stock_code,
                "modify_date": e.modify_date,
                "fetched_at": now,
            }
            for e in entries
        ],
    )
    db.commit()
    return len(entries)


def ensure_registry(db: Session, dart: DartClient) -> None:
    """Sync registry when empty. (Refresh when stale is a user-triggered action.)"""
    if registry_count(db) == 0:
        sync_registry(db, dart)


def search_registry(db: Session, query: str, limit: int = 20, listed_only: bool = False) -> list[CorpRegistry]:
    q = query.strip()
    if not q:
        return []
    conds = [CorpRegistry.corp_name.ilike(f"%{q}%")]
    if q.isdigit():
        conds.append(CorpRegistry.stock_code == q.zfill(6))
        conds.append(CorpRegistry.corp_code == q.zfill(8))
    stmt = select(CorpRegistry).where(or_(*conds))
    if listed_only:
        stmt = stmt.where(CorpRegistry.stock_code.is_not(None))
    rows = list(db.scalars(stmt.limit(500)))

    def rank(r: CorpRegistry) -> tuple:
        exact = r.corp_name == q or r.stock_code == q.zfill(6)
        return (not exact, r.stock_code is None, len(r.corp_name), r.corp_name)

    rows.sort(key=rank)
    return rows[:limit]


def find_registry_by_name(db: Session, name: str) -> list[CorpRegistry]:
    """Exact normalized-name match (used to verify seed candidates)."""
    target = normalize_name(name)
    rows = db.scalars(select(CorpRegistry).where(CorpRegistry.corp_name.ilike(f"%{name.strip()}%"))).all()
    return [r for r in rows if normalize_name(r.corp_name) == target]


def upsert_company_from_dart(db: Session, dart: DartClient, corp_code: str, refresh: bool = False) -> Company:
    """Fetch company.json and store as a verified company (shared master record)."""
    company = db.scalar(select(Company).where(Company.corp_code == corp_code))
    if company and company.is_verified and not refresh:
        return company
    info = dart.company(corp_code)
    stock_code = (info.get("stock_code") or "").strip() or None
    market = CORP_CLS_MARKET.get(info.get("corp_cls", ""), "UNKNOWN")
    if not stock_code:
        market = "UNLISTED"
    if company is None:
        company = Company(company_key=f"dart:{corp_code}", corp_code=corp_code, name=info.get("corp_name", ""))
        db.add(company)
    company.name = info.get("corp_name") or company.name
    company.name_en = info.get("corp_name_eng") or None
    company.stock_code = stock_code
    company.market = market
    company.is_listed = market in ("KOSPI", "KOSDAQ", "KONEX")
    company.country = "KR"
    company.induty_code = info.get("induty_code") or None
    company.ceo = info.get("ceo_nm") or None
    hm = (info.get("hm_url") or "").strip()
    company.homepage = hm or None
    company.is_verified = True
    company.verification_note = f"OpenDART 기업개황(company.json) 확인 — corp_cls={info.get('corp_cls')}"
    company.info_source = "OpenDART company.json"
    company.info_fetched_at = utcnow()
    db.flush()
    return company


def get_or_create_manual_company(
    db: Session,
    name: str,
    *,
    market: str = "UNKNOWN",
    country: str = "KR",
    is_listed: bool | None = None,
    note: str | None = None,
) -> Company:
    """Company not verifiable in DART (foreign/unlisted or DART unavailable). Flagged unverified."""
    key = f"manual:{country.lower()}:{normalize_name(name)}"
    company = db.scalar(select(Company).where(Company.company_key == key))
    if company:
        return company
    company = Company(
        company_key=key,
        name=name.strip(),
        market=market,
        country=country,
        is_listed=is_listed,
        is_verified=False,
        verification_note=note or "DART 미확인 — 공식자료 확인 필요",
        info_source="manual",
    )
    db.add(company)
    db.flush()
    return company


__all__ = [
    "DartError",
    "DartNoData",
    "ensure_registry",
    "find_registry_by_name",
    "get_or_create_manual_company",
    "normalize_name",
    "registry_count",
    "registry_is_stale",
    "search_registry",
    "sync_registry",
    "upsert_company_from_dart",
]
