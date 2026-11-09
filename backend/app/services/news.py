"""뉴스 검색 — Claude CLI 웹검색(가벼운 모델)으로 기사 메타데이터를 찾고 검증·묶음 처리한다.

원칙
- 기사 본문은 저장하지 않는다: 제목·언론사·날짜·링크·검색 결과 기반 1~2문장 요약만 저장(출처 인용 방식).
- AI가 찾은 기사는 링크를 직접 열어 확인한다. 열리지 않는(404·도메인 없음) 기사는 버리고, 접근 제한(403 등)은 '링크 미확인'으로 표시.
- 날짜를 검색 결과로 확인하지 못한 기사는 date_certain=False('날짜 추정')로 표시.
- 같은 날 같은 검색은 다시 하지 않는다(캐시). 다음 검색은 마지막 검색일 이후만 찾는다(증분).
- 하루 검색 횟수 상한(NEWS_DAILY_SEARCH_LIMIT)을 넘으면 뉴스만 건너뛰고 분석은 계속한다.
- 뉴스는 공시보다 신뢰도가 낮은 보조 근거다. 적정가 계산에는 쓰지 않는다.

검색 계획(역할별 깊이)
- 고객사: 최근 6개월, 최대 15건
- 비교기업(상장·해외): 최근 3개월, 주요 이슈 최대 6건
- 전방산업·고객 주제: 고객사 계약상대방·전방산업 기업을 묶어 최근 6개월 투자·증설·발주 이슈 최대 10건
"""
from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlparse

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Company, utcnow
from app.models_analysis import ContractOrder, Disclosure, NewsItem, NewsSearch

log = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))
OVERLAP_DAYS = 3          # 이어서 검색할 때 겹치게 볼 기간(늦게 색인된 기사 대비)
PARALLEL = 3
SEARCH_TIMEOUT = 300
LINK_TIMEOUT = 8
SENTIMENTS = {"positive", "negative", "neutral"}

NEWS_SYSTEM = (
    "당신은 한국 상장사 리서치 보조자입니다. 웹검색으로 기사를 찾아 메타데이터만 정리합니다. "
    "검색 결과에 실제로 나온 기사만 쓰고, URL은 검색 결과의 링크를 한 글자도 바꾸지 말고 그대로 쓰십시오. "
    "기사를 지어내지 마십시오. 찾지 못하면 빈 배열을 답하십시오. 요약은 검색 결과에 보이는 내용만으로 1~2문장, "
    "기사 문장을 길게 베끼지 마십시오. 투자 권유 표현은 쓰지 마십시오."
)


class NewsUnavailable(Exception):
    pass


@dataclass
class SearchSpec:
    query_key: str
    label: str                     # 화면 표시용 (기업명 또는 주제명)
    query: str                     # 검색 지시문 핵심
    months: int
    max_items: int
    company_id: int | None = None
    role: str = "target"           # target | peer | theme
    window: tuple[str, str] | None = None   # 고정 기간(YYYY-MM-DD, YYYY-MM-DD) — 주간 분석용. 없으면 최근 months개월


@dataclass
class SearchOutcome:
    spec: SearchSpec
    status: str                    # searched | cached | skipped_limit | failed
    new_items: int = 0
    error: str | None = None
    since: str | None = None


@dataclass
class NewsResult:
    outcomes: list[SearchOutcome] = field(default_factory=list)
    items: dict[str, list[NewsItem]] = field(default_factory=dict)   # query_key -> items in window


# ------------------------------------------------------------------ searcher (Claude CLI + WebSearch)
class ClaudeNewsSearcher:
    """`claude -p` 에 WebSearch 도구만 허용해 실행. 앱의 Claude 로그인을 그대로 쓴다(별도 키 없음)."""

    def __init__(self, model: str = "sonnet", timeout: int = SEARCH_TIMEOUT):
        exe = shutil.which("claude")
        if not exe:
            raise NewsUnavailable("claude CLI를 찾을 수 없어 뉴스 검색을 할 수 없습니다.")
        self.exe = exe
        self.model = model or "sonnet"
        self.timeout = timeout

    def search(self, spec: SearchSpec, since: str, until: str) -> list[dict]:
        prompt = (
            f"{spec.query}\n"
            f"기간: {since} ~ {until} 에 보도된 국내외 기사. 최대 {spec.max_items}건, 중요한 기사 우선.\n"
            "JSON 배열로만 답하십시오(설명·마크다운 금지). 각 항목 필드:\n"
            '{"date":"YYYY-MM-DD","date_certain":true/false(검색 결과에서 날짜를 직접 확인했으면 true),'
            '"outlet":"언론사","title":"기사 제목","url":"검색 결과 링크 그대로",'
            '"summary":"1~2문장 요약","event":"같은 사건을 다룬 기사끼리 같은 짧은 이름(예: OLEDoS 증착장비 추가 수주)",'
            '"sentiment":"positive|negative|neutral (해당 기업·산업 관점)","about":"기사의 주 대상 기업 또는 주제"}'
        )
        with tempfile.TemporaryDirectory(prefix="valuescope-news-") as cwd:
            try:
                proc = subprocess.run(
                    [self.exe, "-p", "--output-format", "json", "--model", self.model,
                     "--tools", "WebSearch", "--allowedTools", "WebSearch",
                     "--strict-mcp-config", "--no-session-persistence", "--system-prompt", NEWS_SYSTEM],
                    input=prompt, capture_output=True, text=True, encoding="utf-8", timeout=self.timeout, cwd=cwd)
            except subprocess.TimeoutExpired as e:
                raise RuntimeError("뉴스 검색 시간 초과") from e
        outer = _first_json_object(proc.stdout)
        if proc.returncode != 0 or not isinstance(outer, dict) or outer.get("is_error"):
            detail = (outer or {}).get("result") if isinstance(outer, dict) else (proc.stderr or proc.stdout)
            raise RuntimeError(f"claude CLI 뉴스 검색 실패: {str(detail or '').strip()[:200]}")
        return parse_items(str(outer.get("result") or ""))


def _first_json_object(text: str):
    """CLI 출력 뒤에 훅 메시지 등이 붙어도 첫 JSON 객체만 읽는다."""
    text = (text or "").strip()
    if not text:
        return None
    try:
        obj, _ = json.JSONDecoder().raw_decode(text[text.find("{"):])
        return obj
    except (json.JSONDecodeError, ValueError):
        return None


def parse_items(text: str) -> list[dict]:
    """모델 답변에서 JSON 배열을 꺼내 필드를 정리한다. 형식이 틀린 항목은 버린다."""
    m = re.search(r"\[.*\]", text, re.S)
    if not m:
        return []
    try:
        raw = json.loads(m.group(0))
    except json.JSONDecodeError:
        return []
    out = []
    for r in raw if isinstance(raw, list) else []:
        if not isinstance(r, dict):
            continue
        url = str(r.get("url") or "").strip()
        title = str(r.get("title") or "").strip()
        if not title or not url.startswith(("http://", "https://")):
            continue
        d = str(r.get("date") or "").strip()[:10]
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", d):
            d = None
        sent = str(r.get("sentiment") or "neutral").lower()
        out.append({"pub_date": d, "date_certain": bool(r.get("date_certain")) and d is not None,
                    "outlet": str(r.get("outlet") or urlparse(url).netloc)[:100], "title": title[:400], "url": url[:600],
                    "summary": str(r.get("summary") or "")[:500], "event": str(r.get("event") or title)[:200],
                    "sentiment": sent if sent in SENTIMENTS else "neutral", "about": str(r.get("about") or "")[:200]})
    return out


# ------------------------------------------------------------------ link verification
def check_link(url: str, client: httpx.Client) -> str:
    """ok = 열림, unverified = 접근 제한 등으로 확인 못함(유지), gone = 없는 기사(버림)."""
    try:
        r = client.get(url, follow_redirects=True, timeout=LINK_TIMEOUT)
        r.close()
    except httpx.HTTPError:
        return "gone" if not _resolvable(url) else "unverified"
    if r.status_code < 400:
        return "ok"
    if r.status_code in (404, 410):
        return "gone"
    return "unverified"


def _resolvable(url: str) -> bool:
    import socket

    try:
        socket.getaddrinfo(urlparse(url).hostname or "", 443)
        return True
    except OSError:
        return False


# ------------------------------------------------------------------ plan
def build_plan(db: Session, project_id: str, target: Company, members: list) -> list[SearchSpec]:
    """members: PeerGroupVersion.members (company, use_type, category)."""
    specs = [SearchSpec(f"company:{target.id}", target.name,
                        f"'{target.name}'(종목코드 {target.stock_code or '-'}) 관련 뉴스를 웹검색으로 찾으십시오. "
                        "수주·실적·증설·고객사·경쟁·주가 급변 사유·자금조달·지배구조 이슈를 포함하십시오.",
                        months=6, max_items=15, company_id=target.id, role="target")]
    theme_names: list[str] = []
    for m in members:
        c = m.company
        if m.use_type in ("valuation", "industry_reference", "tech_competition"):
            if m.category == "C":
                theme_names.append(c.name)
            specs.append(SearchSpec(f"company:{c.id}", c.name,
                                    f"'{c.name}'{f'(종목코드 {c.stock_code})' if c.stock_code else ''} 관련 주요 뉴스만 웹검색으로 찾으십시오. "
                                    "수주·실적·증설·투자·사업 변화 위주.",
                                    months=3, max_items=6, company_id=c.id, role="peer"))
    # 고객사 계약상대방(공시 기준) 상위 3곳을 전방산업 주제에 포함
    from app.services.peer_discovery import customer_key

    counts: dict[str, list] = {}
    for (cp,) in db.execute(select(ContractOrder.counterparty).where(ContractOrder.company_id == target.id,
                                                                     ContractOrder.counterparty.is_not(None))).all():
        k = customer_key(cp)
        if k:
            counts.setdefault(k, []).append(cp)
    top = sorted(counts.values(), key=len, reverse=True)[:3]
    theme_names += [min(v, key=len) for v in top]
    theme_names = list(dict.fromkeys(theme_names))
    if theme_names:
        specs.append(SearchSpec(f"theme:{project_id}:customers", "전방산업·고객사",
                                f"{target.name}의 고객·전방산업({', '.join(theme_names)})의 투자·증설·장비 발주·패널 생산 계획 관련 뉴스를 "
                                "웹검색으로 찾으십시오. 각 기사의 about에는 기사 주 대상 기업명을 쓰십시오.",
                                months=6, max_items=10, role="theme"))
    return specs


# ------------------------------------------------------------------ run searches (cache · incremental · limit)
def searches_today(db: Session) -> int:
    start = datetime.now(KST).replace(hour=0, minute=0, second=0, microsecond=0)
    return db.scalar(select(func.count()).select_from(NewsSearch).where(NewsSearch.searched_at >= start)) or 0


def run_searches(db: Session, searcher, specs: list[SearchSpec], today: date, daily_limit: int,
                 link_client: httpx.Client | None = None, progress=None) -> NewsResult:
    res = NewsResult()
    todo: list[tuple[SearchSpec, str, str]] = []
    used = searches_today(db)
    for spec in specs:
        last = db.scalar(select(NewsSearch).where(NewsSearch.query_key == spec.query_key, NewsSearch.status == "succeeded")
                         .order_by(NewsSearch.searched_at.desc()))
        if spec.window:
            # 고정 기간: 기간이 끝난 뒤 한 번 검색했으면 다시 검색하지 않음
            until = min(spec.window[1], today.isoformat())
            if last and last.until >= until:
                res.outcomes.append(SearchOutcome(spec, "cached"))
                continue
            since = spec.window[0]
        else:
            until = today.isoformat()
            if last and last.until >= until:
                res.outcomes.append(SearchOutcome(spec, "cached"))
                continue
            since = (today - timedelta(days=spec.months * 31)).isoformat()
            if last:
                since = max(since, (date.fromisoformat(last.until) - timedelta(days=OVERLAP_DAYS)).isoformat())
        if used >= daily_limit:
            res.outcomes.append(SearchOutcome(spec, "skipped_limit", since=since))
            continue
        used += 1
        todo.append((spec, since, until))

    def work(item):
        spec, since, until = item
        try:
            return spec, since, until, searcher.search(spec, since, until), None
        except Exception as e:  # noqa: BLE001 — 한 검색 실패가 나머지를 막지 않도록
            return spec, since, until, [], f"{type(e).__name__}: {e}"

    results = []
    if todo:
        with ThreadPoolExecutor(max_workers=PARALLEL) as pool:
            for r in pool.map(work, todo):
                results.append(r)
                if progress:
                    progress(r[0].label)
    own_client = link_client is None
    client = link_client or httpx.Client(headers={"User-Agent": "Mozilla/5.0 (ValueScope link check)"})
    try:
        for spec, since, until, items, err in results:
            if err:
                db.add(NewsSearch(query_key=spec.query_key, company_id=spec.company_id, query=spec.query, since=since,
                                  until=until, status="failed", error=err[:500], model=getattr(searcher, "model", None)))
                res.outcomes.append(SearchOutcome(spec, "failed", error=err, since=since))
                continue
            existing = {u for (u,) in db.execute(select(NewsItem.url).where(NewsItem.query_key == spec.query_key)).all()}
            new = 0
            for it in items:
                if it["url"] in existing:
                    continue
                if it["pub_date"] and not (since <= it["pub_date"] <= until):
                    continue   # 기간 밖 기사
                status = check_link(it["url"], client)
                if status == "gone":
                    continue
                db.add(NewsItem(query_key=spec.query_key, company_id=spec.company_id, link_status=status, **it))
                existing.add(it["url"])
                new += 1
            db.add(NewsSearch(query_key=spec.query_key, company_id=spec.company_id, query=spec.query, since=since,
                              until=until, status="succeeded", item_count=new, model=getattr(searcher, "model", None)))
            res.outcomes.append(SearchOutcome(spec, "searched", new_items=new, since=since))
        db.commit()
    finally:
        if own_client:
            client.close()
    for spec in specs:
        q = select(NewsItem).where(NewsItem.query_key == spec.query_key)
        if spec.window:
            q = q.where(NewsItem.pub_date >= spec.window[0], NewsItem.pub_date <= spec.window[1])
        else:
            start = (today - timedelta(days=spec.months * 31)).isoformat()
            q = q.where((NewsItem.pub_date >= start) | (NewsItem.pub_date.is_(None)))
        res.items[spec.query_key] = list(db.scalars(q.order_by(NewsItem.pub_date.desc())))
    return res


# ------------------------------------------------------------------ grouping · disclosure matching
CONTRACT_WORDS = ("수주", "공급계약", "계약", "납품")
DISCLOSURE_HINTS = [
    (("수주", "공급계약", "계약", "납품"), ("단일판매", "공급계약")),
    (("실적", "영업이익", "매출", "흑자", "적자"), ("잠정", "영업(잠정)실적", "매출액또는손익구조", "손익구조")),
    (("유상증자", "증자"), ("유상증자",)),
    (("전환사채", "CB", "사채"), ("전환사채", "신주인수권부사채", "교환사채")),
    (("자사주", "자기주식"), ("자기주식",)),
    (("최대주주", "지분", "매각", "인수"), ("최대주주", "주식등의대량보유", "타법인주식")),
    (("시설", "증설", "공장", "투자"), ("신규시설투자", "유형자산")),
]


def group_events(items: list[NewsItem]) -> list[dict]:
    """같은 사건(event 이름 + 날짜 3일 이내)을 하나로 묶는다."""
    events: list[dict] = []
    for it in sorted(items, key=lambda x: (x.pub_date or "0000"), reverse=True):
        key = re.sub(r"\s+", "", (it.event or it.title)).lower()
        hit = None
        for ev in events:
            if ev["key"] == key and _near(ev["date"], it.pub_date, 3):
                hit = ev
                break
        if hit is None:
            hit = {"key": key, "event": it.event or it.title, "date": it.pub_date, "date_certain": it.date_certain,
                   "sentiment": it.sentiment, "about": it.about, "company_id": it.company_id, "articles": []}
            events.append(hit)
        hit["articles"].append({"title": it.title, "outlet": it.outlet, "url": it.url, "date": it.pub_date,
                                "date_certain": it.date_certain, "summary": it.summary, "link_status": it.link_status})
        if it.date_certain and not hit["date_certain"]:
            hit["date"], hit["date_certain"] = it.pub_date, True
    for ev in events:
        ev["summary"] = next((a["summary"] for a in ev["articles"] if a["summary"]), "")
        ev["outlets"] = list(dict.fromkeys(a["outlet"] for a in ev["articles"] if a["outlet"]))
    return events


def _near(a: str | None, b: str | None, days: int) -> bool:
    if not a or not b:
        return a == b
    return abs((date.fromisoformat(a) - date.fromisoformat(b)).days) <= days


def match_disclosures(db: Session, events: list[dict]) -> None:
    """기사 사건을 같은 기업의 공시(±3일)와 대조: 공시 확인 / 인근 공시 있음 / 기사로만 확인."""
    for ev in events:
        ev["disclosure"], ev["verification"] = None, "news_only"
        if not ev.get("company_id") or not ev.get("date"):
            continue
        d = date.fromisoformat(ev["date"])
        lo, hi = (d - timedelta(days=3)).strftime("%Y%m%d"), (d + timedelta(days=3)).strftime("%Y%m%d")
        discs = list(db.scalars(select(Disclosure).where(Disclosure.company_id == ev["company_id"],
                                                         Disclosure.rcept_dt >= lo, Disclosure.rcept_dt <= hi)))
        if not discs:
            continue
        text = ev["event"] + " " + " ".join(a["title"] for a in ev["articles"])
        best = None
        for words, report_words in DISCLOSURE_HINTS:
            if any(w in text for w in words):
                best = next((x for x in discs if any(r in x.report_nm.replace(" ", "") for r in report_words)), None)
                if best:
                    break
        if best:
            ev["verification"] = "confirmed"
            ev["disclosure"] = {"rcept_no": best.rcept_no, "report_nm": best.report_nm, "rcept_dt": best.rcept_dt, "url": best.url}
        else:
            ev["verification"] = "nearby"
            x = discs[0]
            ev["disclosure"] = {"rcept_no": x.rcept_no, "report_nm": x.report_nm, "rcept_dt": x.rcept_dt, "url": x.url}


VERIFICATION_LABEL = {"confirmed": "공시로 확인", "nearby": "인근 공시 있음(내용 대조 필요)", "news_only": "기사로만 확인"}
SENTIMENT_LABEL = {"positive": "긍정", "negative": "부정", "neutral": "중립"}


# ------------------------------------------------------------------ issue timeline (공시 + 뉴스)
KEY_DISCLOSURE = re.compile(r"단일판매|공급계약|잠정|손익구조|유상증자|무상증자|전환사채|신주인수권|교환사채|자기주식|최대주주|"
                            r"주식등의대량보유|신규시설투자|타법인주식|소송|횡령|배임|조회공시|감사보고서|거래정지|불성실|합병|분할|영업양수|영업양도")


def disclosure_timeline(db: Session, company: Company, since: str, limit: int = 40) -> list[dict]:
    rows = db.scalars(select(Disclosure).where(Disclosure.company_id == company.id, Disclosure.rcept_dt >= since.replace("-", ""))
                      .order_by(Disclosure.rcept_dt.desc()))
    out = []
    for d in rows:
        name = d.report_nm.replace(" ", "")
        if not KEY_DISCLOSURE.search(name):
            continue
        out.append({"date": f"{d.rcept_dt[:4]}-{d.rcept_dt[4:6]}-{d.rcept_dt[6:]}", "company": company.name, "company_id": company.id,
                    "type": "disclosure", "title": d.report_nm.strip(), "url": d.url, "rcept_no": d.rcept_no})
        if len(out) >= limit:
            break
    return out


def utc_now_iso() -> str:
    return utcnow().isoformat(timespec="seconds")
