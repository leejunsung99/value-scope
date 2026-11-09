"""비교기업 후보 확장 탐색 — 공시 근거가 남는 두 가지 방법.

1) 업종코드 탐색: OpenDART 기업개황(company.json)의 업종코드(induty_code)가 고객사·확정 비교기업과 같은 상장사.
   업종코드는 corpCode.xml에 없으므로 상장사별 기업개황을 한 번씩 조회해 corp_registry에 색인한다.
   호출 수가 많으므로 1회 검색당 상한(INDEX_MAX_PER_RUN)과 일일 예산 여유분(INDEX_RESERVE)을 지키며
   나머지는 다음 검색 때 이어서 색인한다. 시가총액이 고객사와 가까운 기업부터 색인한다.
2) 고객사 중복 계약: 고객사의 단일판매·공급계약 상대방(삼성디스플레이·BOE 등)과 계약을 공시한 다른 상장사.
   공시알람 DB(읽기 전용)의 구조화된 계약 공시와 ValueScope가 수집한 계약 공시를 대조한다.

두 방법 모두 '후보'일 뿐이며, 이후 AI 분류 단계에서 후보 자신의 사업보고서 원문으로 관련성을 판단한다.
"""
from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass, field
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Company, CorpRegistry, utcnow
from app.models_analysis import ContractOrder
from app.services.dart import DartClient, DartError, viewer_url

log = logging.getLogger(__name__)

INDEX_MAX_PER_RUN = 1500       # 1회 검색에서 업종코드 색인에 쓰는 DART 호출 상한
INDEX_RESERVE = 800            # 일일 DART 예산 중 분석용으로 남겨둘 호출 수
INDEX_REFRESH_DAYS = 180
MAX_INDUSTRY_CANDIDATES = 12
MAX_CUSTOMER_CANDIDATES = 10
LISTED_CLS = {"Y", "K"}        # KOSPI, KOSDAQ


# --------------------------------------------------------------------- customer name keys
_SUFFIX_RE = re.compile(r"\(주\)|㈜|주식회사|유한회사|\bco\b\.?|\bltd\b\.?|\binc\b\.?|\bcorp\b\.?|\bcorporation\b|\blimited\b|\bl\.?l\.?c\b",
                        re.IGNORECASE)
STOP_TOKENS = {
    "co", "ltd", "inc", "corp", "corporation", "limited", "company", "the", "of", "and", "technology", "technologies", "tech",
    "display", "displays", "optoelectronics", "optoelectronic", "electronics", "electronic", "trading", "group", "international",
    "materials", "material", "specialty", "solutions", "performance", "semiconductor", "manufacturing", "industrial", "industries",
    "science", "sciences", "holdings", "holding", "global", "new", "development", "investment", "import", "export", "branch",
    # 지명 (중국 패널사 법인명 앞에 붙는 도시·성 이름)
    "beijing", "shanghai", "shenzhen", "chengdu", "chongqing", "hefei", "wuhan", "wuxi", "suzhou", "kunshan", "nanjing", "jiangsu",
    "anhui", "shaanxi", "jinan", "fuzhou", "mianyang", "ordos", "guangzhou", "xiamen", "xian", "tianjin", "hangzhou", "zhejiang",
    "guangdong", "fujian", "sichuan", "hubei", "shandong", "henan", "hunan", "china", "korea", "japan", "vietnam", "taiwan", "hong", "kong",
    "usa", "america", "europe", "india",
    "중국판매법인", "판매법인", "법인", "지점", "중국", "베트남",
}
CONGLOMERATES = {"삼성", "엘지", "에스케이", "현대", "samsung", "lg", "sk", "hyundai", "롯데", "lotte", "한화", "hanwha"}


def customer_key(name: str | None) -> str | None:
    """계약상대방 이름 → 비교용 키. 'Chengdu BOE Display Technology Co.,Ltd' → 'boe',
    '삼성디스플레이 주식회사' → '삼성디스플레이', 'SAMSUNG SDI WUXI CO.,LTD' → 'samsung sdi'."""
    if not name:
        return None
    s = _SUFFIX_RE.sub(" ", name).lower()
    s = re.sub(r"(?<![a-z])lg(?![a-z])|엘지", " 엘지 ", s)
    s = re.sub(r"(?<![a-z])sk(?![a-z])", " 에스케이 ", s)
    s = s.replace("삼성", " 삼성 ")
    toks = [t for t in re.split(r"[^0-9a-z가-힣]+", s) if t and t not in STOP_TOKENS and len(t) >= 2]
    if not toks:
        return None
    # 한글 단일 법인명(예: 삼성 디스플레이 → 삼성디스플레이)은 붙여서 비교
    if all(re.fullmatch(r"[가-힣]+", t) for t in toks):
        return "".join(toks)
    if toks[0] in CONGLOMERATES and len(toks) > 1:
        return f"{toks[0]} {toks[1]}"
    return toks[0]


# --------------------------------------------------------------------- industry index
@dataclass
class DiscoveryResult:
    industry: list[dict] = field(default_factory=list)   # {"corp_code","name","induty_code","level","basis"}
    customer: list[dict] = field(default_factory=list)   # {"corp_code","name","customers","refs"}
    messages: list[tuple[str, str]] = field(default_factory=list)

    def info(self, m: str) -> None:
        self.messages.append(("info", m))

    def warn(self, m: str) -> None:
        self.messages.append(("warning", m))


def listed_universe(db: Session, monitor=None) -> list[CorpRegistry]:
    rows = list(db.scalars(select(CorpRegistry).where(CorpRegistry.stock_code.is_not(None))))
    cls = monitor_corp_cls(monitor)
    if cls:
        rows = [r for r in rows if (r.corp_cls or cls.get(r.corp_code)) in LISTED_CLS]
    else:
        rows = [r for r in rows if r.corp_cls is None or r.corp_cls in LISTED_CLS]
    return rows


def monitor_corp_cls(monitor) -> dict[str, str]:
    if monitor is None or not getattr(monitor, "enabled", False):
        return {}
    try:
        return monitor.corp_classes()
    except Exception as e:  # noqa: BLE001 — 공시알람 DB 장애가 검색 전체를 막지 않도록
        log.warning("monitor corp_cls query failed: %s", e)
        return {}


def monitor_market_caps(monitor) -> dict[str, float]:
    if monitor is None or not getattr(monitor, "enabled", False):
        return {}
    try:
        return monitor.latest_market_caps()
    except Exception as e:  # noqa: BLE001
        log.warning("monitor market cap query failed: %s", e)
        return {}


def update_industry_index(db: Session, dart: DartClient, universe: list[CorpRegistry], mcaps: dict[str, float],
                          target_mcap: float | None, budget_remaining: int | None, res: DiscoveryResult) -> None:
    now = utcnow()
    stale_before = now - timedelta(days=INDEX_REFRESH_DAYS)
    # 이미 기업개황을 받아둔 기업은 호출 없이 색인
    known = {c.corp_code: c for c in db.scalars(select(Company).where(Company.is_verified.is_(True), Company.corp_code.is_not(None)))}
    pending = []
    for r in universe:
        fresh = r.induty_checked_at is not None and _aware(r.induty_checked_at) >= stale_before
        if fresh:
            continue
        c = known.get(r.corp_code)
        if c is not None and c.induty_code and c.info_fetched_at and _aware(c.info_fetched_at) >= stale_before:
            r.induty_code, r.induty_checked_at = c.induty_code, c.info_fetched_at
            continue
        pending.append(r)
    db.commit()
    if not pending:
        return
    # 시가총액이 고객사와 가까운 기업부터
    def dist(r):
        m = mcaps.get(r.stock_code or "")
        if not m or not target_mcap:
            return 99.0
        return abs(math.log(m / target_mcap))
    pending.sort(key=dist)
    cap = INDEX_MAX_PER_RUN
    if budget_remaining is not None:
        cap = min(cap, max(0, budget_remaining - INDEX_RESERVE))
    if cap <= 0:
        res.warn(f"DART 일일 호출 여유가 부족해 업종코드 색인을 건너뜀 (미색인 {len(pending)}개 — 내일 다시 검색하면 이어서 색인)")
        return
    done = 0
    for r in pending[:cap]:
        try:
            info = dart.company(r.corp_code)
        except DartError as e:
            log.warning("company.json %s failed: %s", r.corp_code, e)
            if "상한" in str(e) or "한도" in str(e):
                break
            continue
        except Exception as e:  # BudgetExceeded 등
            res.warn(f"업종코드 색인 중단: {e}")
            break
        r.induty_code = (info.get("induty_code") or "").strip() or None
        r.corp_cls = (info.get("corp_cls") or "").strip() or None
        r.induty_checked_at = now
        done += 1
        if done % 50 == 0:
            db.commit()
    db.commit()
    left = len(pending) - done
    res.info(f"업종코드 색인: 이번에 {done}개 조회(OpenDART 기업개황)" + (f", 미색인 {left}개는 다음 검색 때 이어서 색인" if left else ", 색인 완료"))


def _aware(dt):
    from datetime import timezone

    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def industry_candidates(target: Company, peer_codes: dict[str, str], universe: list[CorpRegistry], mcaps: dict[str, float],
                        target_mcap: float | None, exclude: set[str]) -> list[dict]:
    """업종코드 일치 후보. level 2 = 확정 비교기업의 세부 업종코드와 일치, level 1 = 고객사 업종코드(접두) 일치."""
    tcode = (target.induty_code or "").strip()
    if not tcode:
        return []
    # 확정 비교기업 중 고객사 업종코드로 시작하는 더 세부적인 코드 (예: 고객사 292 → 비교기업 29272)
    specific = {code: name for name, code in peer_codes.items() if code and code.startswith(tcode[:3]) and len(code) > len(tcode)}
    out = []
    for r in universe:
        code = (r.induty_code or "").strip()
        if not code or r.corp_code in exclude or r.corp_code == target.corp_code:
            continue
        hit = next((c for c in specific if code.startswith(c)), None)
        if hit:
            level, basis = 2, f"업종코드 {code} — 확정 비교기업 {specific[hit]}의 세부 업종({hit})과 일치"
        elif code.startswith(tcode):
            level, basis = 1, f"업종코드 {code} — 고객사 업종코드 {tcode}에 포함"
        else:
            continue
        m = mcaps.get(r.stock_code or "")
        size = abs(math.log(m / target_mcap)) if m and target_mcap else 99.0
        out.append({"corp_code": r.corp_code, "name": r.corp_name, "stock_code": r.stock_code, "induty_code": code,
                    "level": level, "basis": basis, "size_gap": size, "market_cap": m})
    out.sort(key=lambda x: (-x["level"], x["size_gap"]))
    return out[:MAX_INDUSTRY_CANDIDATES]


# --------------------------------------------------------------------- customer overlap
def customer_overlap_candidates(db: Session, monitor, target: Company, exclude: set[str]) -> tuple[list[dict], dict]:
    """고객사 계약상대방과 같은 상대방에 공급계약을 공시한 다른 상장사."""
    rows = db.execute(select(ContractOrder.counterparty).where(ContractOrder.company_id == target.id,
                                                               ContractOrder.counterparty.is_not(None))).all()
    keys: dict[str, str] = {}
    for (cp,) in rows:
        k = customer_key(cp)
        if k:
            keys.setdefault(k, cp)
    meta = {"target_customers": sorted(set(keys.values())), "contracts_scanned": 0, "monitor_since": None}
    if not keys:
        return [], meta
    contracts: list[dict] = []
    if monitor is not None and getattr(monitor, "enabled", False):
        try:
            contracts.extend(monitor.contract_counterparties())
            meta["monitor_since"] = monitor.contracts_since()
        except Exception as e:  # noqa: BLE001
            log.warning("monitor contract query failed: %s", e)
    q = (select(ContractOrder, Company).join(Company, Company.id == ContractOrder.company_id)
         .where(ContractOrder.company_id != target.id, ContractOrder.counterparty.is_not(None), Company.corp_code.is_not(None)))
    for co, c in db.execute(q).all():
        contracts.append({"corp_code": c.corp_code, "stock_code": c.stock_code, "corp_name": c.name, "rcept_no": co.rcept_no,
                          "rcept_date": co.filed_date, "counterparty": co.counterparty, "description": co.description,
                          "report_name": co.report_nm})
    meta["contracts_scanned"] = len(contracts)
    by_corp: dict[str, dict] = {}
    seen_rcept = set()
    for ct in contracts:
        cc = ct.get("corp_code")
        if not cc or not ct.get("stock_code") or cc == target.corp_code or cc in exclude or ct["rcept_no"] in seen_rcept:
            continue
        k = customer_key(ct.get("counterparty"))
        if not k or k not in keys:
            continue
        seen_rcept.add(ct["rcept_no"])
        e = by_corp.setdefault(cc, {"corp_code": cc, "name": ct["corp_name"], "stock_code": ct["stock_code"], "customers": set(), "refs": []})
        e["customers"].add(keys[k])
        e["refs"].append({
            "type": "DART", "title": (ct.get("report_name") or "단일판매ㆍ공급계약체결").strip(), "url": viewer_url(ct["rcept_no"]),
            "rcept_no": ct["rcept_no"], "filed_date": ct.get("rcept_date"), "section": None,
            "note": f"계약상대방 '{ct['counterparty']}' — 고객사 계약상대방 '{keys[k]}'와 동일",
            "quote": (ct.get("description") or None),
        })
    out = sorted(by_corp.values(), key=lambda e: (-len(e["customers"]), -len(e["refs"])))[:MAX_CUSTOMER_CANDIDATES]
    for e in out:
        e["customers"] = sorted(e["customers"])
    return out, meta
