"""분석 실행(Runner): 데이터 확인 → 신규·정정 수집 → 지표 → 가치평가 → 시장·업종 영향 → AI 보고서 → 검증 → 저장.

데이터 수집은 증분, 평가는 매 실행마다 전체 최신 데이터로 다시 계산하고 실행 ID별 Snapshot으로 저장한다.
"""
from __future__ import annotations

import logging
import traceback
from datetime import date, datetime, timedelta

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.models import Company, PeerGroupVersion, Project, utcnow
from app.models_analysis import (AnalysisEvidence, AnalysisReport, AnalysisRun, Disclosure, StockPrice, Valuation,
                                 ValuationScenario)
from app.services import collect_disclosures as cd
from app.services import collect_financials as cf
from app.services import engine as eng
from app.services import news as news_svc
from app.services.ai import AIProvider, NoAIProvider
from app.services.backlog import official_backlog
from app.services.budget import BudgetExceeded, CallBudget
from app.services.collect_prices import KrxClient, KrxError, collect_prices
from app.services.dart import DartClient, DartError
from app.services.engine_data import FinSeries, adjusted_returns, cumulative, load_prices, market_proxy, window_return
from app.services.monitor_source import MonitorSource
from app.services.normalize import forward_schedule, revenue_lumpiness
from app.services.report import generate_report

log = logging.getLogger(__name__)
STEPS = ["financials", "disclosures", "contracts", "prices", "valuation"]


def clean(o):
    """JSON-safe: NaN/inf → None, numpy scalars → float, timestamps → ISO strings."""
    import math

    import numpy as np
    if isinstance(o, dict):
        return {str(k): clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [clean(v) for v in o]
    if isinstance(o, (np.floating, float)):
        f = float(o)
        return None if math.isnan(f) or math.isinf(f) else f
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, (pd.Timestamp, datetime, date)):
        return o.isoformat()
    return o


class RunLog:
    def __init__(self, db: Session, run: AnalysisRun):
        self.db, self.run = db, run

    def msg(self, level: str, text: str):
        self.run.messages = [*self.run.messages, {"level": level, "message": text, "at": datetime.now().isoformat(timespec="seconds")}]
        self.db.commit()

    def step(self, company_id: int, step: str, status: str, detail: str | None = None):
        prog = dict(self.run.progress)
        comps = dict(prog.get("companies", {}))
        c = dict(comps.get(str(company_id), {}))
        steps = dict(c.get("steps", {}))
        steps[step] = {"status": status, "detail": detail}
        c["steps"] = steps
        comps[str(company_id)] = c
        prog["companies"] = comps
        self.run.progress = prog
        self.db.commit()

    def stage(self, name: str, detail: str | None = None):
        self.run.progress = {**self.run.progress, "stage": name, "stage_detail": detail}
        self.db.commit()


def create_run(db: Session, project: Project, ai: AIProvider, include_news: bool = True) -> AnalysisRun:
    group = project.peer_group
    if not group or not group.versions:
        raise ValueError("확정된 비교군이 없습니다. STEP 1에서 비교기업을 확정하세요.")
    version: PeerGroupVersion = group.versions[-1]
    running = db.scalar(select(AnalysisRun).where(AnalysisRun.project_id == project.id,
                                                  AnalysisRun.status.in_(["queued", "running"])))
    if running:
        raise RuntimeError(f"이미 실행 중인 분석이 있습니다: {running.id}")
    companies = [{"company_id": project.company_id, "name": project.company.name, "role": "target"}] + [
        {"company_id": m.company_id, "name": m.snapshot["company"]["name"], "role": m.use_type, "category": m.category}
        for m in version.members]
    run = AnalysisRun(project_id=project.id, peer_version_id=version.id, peer_version_no=version.version_no, status="queued",
                      model_version=eng.MODEL_VERSION, ai_provider=ai.name, ai_model=getattr(ai, "model", None),
                      progress={"stage": "대기", "options": {"include_news": include_news},
                                "companies": {str(c["company_id"]): {**c, "steps": {}} for c in companies}})
    db.add(run)
    db.commit()
    return run


def execute_run(session_factory: sessionmaker, run_id: str, dart: DartClient, krx: KrxClient, ai: AIProvider,
                monitor: MonitorSource, today: date | None = None, news_searcher=None) -> None:
    today = today or date.today()
    db = session_factory()
    run = db.get(AnalysisRun, run_id)
    rl = RunLog(db, run)
    run.status = "running"
    db.commit()
    s = get_settings()
    dart.on_call = CallBudget(session_factory, "DART", s.dart_daily_call_limit)
    krx.on_call = CallBudget(session_factory, "KRX", s.krx_daily_call_limit)
    failures = 0
    try:
        project = db.get(Project, run.project_id)
        version = db.get(PeerGroupVersion, run.peer_version_id)
        target = project.company
        members = {m.company_id: m for m in version.members}
        companies: list[Company] = [target] + [m.company for m in version.members]
        rl.msg("info", f"분석 시작 — {target.name} + 비교군 v{run.peer_version_no} {len(members)}개 기업"
                       f" (공시알람 DB {'연동' if monitor.enabled else '미연동'})")

        # ---------------------------------------------------------- 1~2. collect (incremental)
        texts_by_company: dict[int, dict] = {}
        data_versions: dict = {}
        for c in companies:
            cid = c.id
            if not c.corp_code:
                for st in ("financials", "disclosures", "contracts"):
                    rl.step(cid, st, "skipped", "DART 미등록(해외·비상장) — 공시 데이터 없음")
                continue
            rl.stage("데이터 수집", c.name)
            try:
                rl.step(cid, "financials", "running")
                st = cf.fetch_financials(db, dart, c, today)
                metrics = cf.normalize(db, c)
                rl.step(cid, "financials", "done", f"신규 {st['fetched']} · 재사용 {st['reused']} · 데이터없음 {st['nodata']} · 지표 {len(metrics)}건")
            except BudgetExceeded as e:
                rl.step(cid, "financials", "failed", str(e))
                rl.msg("error", str(e))
                failures += 1
                raise
            except DartError as e:
                rl.step(cid, "financials", "failed", str(e))
                failures += 1
            try:
                rl.step(cid, "disclosures", "running")
                st = cd.collect_disclosure_list(db, dart, monitor, c, today)
                texts_by_company[cid] = st.pop("texts", {})
                rl.step(cid, "disclosures", "done", f"신규 {st['new']}건 (공시알람 DB {st['monitor_rows']}건 대조, DART 범위조회 {st['dart_calls_ranges']}회)")
            except BudgetExceeded as e:
                rl.step(cid, "disclosures", "failed", str(e))
                raise
            except Exception as e:  # noqa: BLE001
                rl.step(cid, "disclosures", "failed", f"{type(e).__name__}: {e}")
                failures += 1
            try:
                rl.step(cid, "contracts", "running")
                st = cd.collect_contracts(db, dart, c, texts_by_company.get(cid, {}))
                rl.step(cid, "contracts", "done", f"신규 {st['new']}건 (공시알람 원문 재사용 {st['from_monitor_text']} · DART 원문 {st['dart_docs']} · 실패 {st['errors']})")
            except BudgetExceeded as e:
                rl.step(cid, "contracts", "failed", str(e))
                raise
            except Exception as e:  # noqa: BLE001
                rl.step(cid, "contracts", "failed", f"{type(e).__name__}: {e}")
                failures += 1

        rl.stage("주가 수집", "KRX 일별매매정보")
        for c in companies:
            rl.step(c.id, "prices", "running")
        price_ok = True
        try:
            st = collect_prices(db, krx, today, progress=lambda m: rl.stage("주가 수집", m))
            rl.msg("info", f"KRX 시세: 신규 {st['requested']}일 요청 · {st['reused']}일 재사용 · {st['rows']:,}행 저장")
        except BudgetExceeded as e:
            rl.msg("error", str(e))
            price_ok = False
        except KrxError as e:
            rl.msg("error", f"KRX 시세 수집 실패: {e}")
            price_ok = False
        if not price_ok:
            rl.msg("warning", "주가 최신화 실패 — 이미 저장된 시세로 분석을 계속합니다.")

        # ---------------------------------------------------------- 2b. news (Claude 웹검색, 보조 근거)
        news_res, news_specs = None, []
        include_news = (run.progress or {}).get("options", {}).get("include_news", True)
        if not include_news:
            rl.msg("info", "뉴스 검색 제외(사용자 선택)")
        elif news_searcher is None:
            rl.msg("info", "뉴스 검색 사용 불가(AI_PROVIDER=claude_cli 필요) — 공시 기반으로만 분석합니다.")
        else:
            news_specs = news_svc.build_plan(db, project.id, target, list(version.members))
            rl.stage("뉴스 검색", f"{len(news_specs)}건 (고객사·비교기업·전방산업, {getattr(news_searcher, 'model', '')})")
            for sp in news_specs:
                if sp.company_id:
                    rl.step(sp.company_id, "news", "running")
            done: list[str] = []

            def _news_progress(label: str) -> None:
                done.append(label)
                rl.stage("뉴스 검색", f"{len(done)}/{len(news_specs)} · {label}")

            news_res = news_svc.run_searches(db, news_searcher, news_specs, today, s.news_daily_search_limit, progress=_news_progress)
            olabel = {"searched": "검색", "cached": "오늘 검색 결과 재사용", "skipped_limit": "일일 검색 상한 초과로 건너뜀", "failed": "실패"}
            for o in news_res.outcomes:
                n = len(news_res.items.get(o.spec.query_key, []))
                detail = (f"{olabel[o.status]} · 기사 {n}건" + (f" (신규 {o.new_items})" if o.status == "searched" else "")
                          + (f" — {o.error[:120]}" if o.error else ""))
                if o.spec.company_id:
                    st_ = "failed" if o.status == "failed" else ("skipped" if o.status == "skipped_limit" else "done")
                    rl.step(o.spec.company_id, "news", st_, detail)
                if o.status in ("failed", "skipped_limit"):
                    rl.msg("warning", f"뉴스 {o.spec.label}: {detail}")
            counts = {k: sum(1 for o in news_res.outcomes if o.status == k) for k in olabel}
            rl.msg("info", f"뉴스 검색: 검색 {counts['searched']} · 재사용 {counts['cached']} · 상한 초과 {counts['skipped_limit']} · 실패 {counts['failed']}")

        # ---------------------------------------------------------- 3~5. compute
        rl.stage("지표·가치평가 계산")
        ev = eng.Evidence()
        codes = [c.stock_code for c in companies if c.stock_code]
        prices = load_prices(db, codes)
        latest_date = prices["date"].max() if not prices.empty else None
        run.price_as_of = latest_date.strftime("%Y%m%d") if latest_date is not None else None
        proxies = {mk: market_proxy(db, mk) for mk in ("KOSDAQ", "KOSPI")}

        fs_map: dict[int, FinSeries] = {}
        cm: dict[int, eng.CompanyMetrics] = {}
        series: dict[int, dict] = {}
        for c in companies:
            fs = FinSeries.load(db, c)
            fs_map[c.id] = fs
            px = {}
            if c.stock_code and not prices.empty:
                d = prices[prices["code"] == c.stock_code]
                if not d.empty and (latest_date - d["date"].max()).days <= 7:
                    last = d.iloc[-1]
                    px = {"price": float(last["close"]), "mcap": float(last["mcap"]), "listed_shares": float(last["shares"]),
                          "date": last["date"].strftime("%Y-%m-%d")}
                if not d.empty:
                    rets, split_events = adjusted_returns(d)
                    series[c.id] = {"rets": rets, "level": cumulative(rets), "splits": split_events, "frame": d}
            shares = cf.fetch_shares(dart, c, today) if (c.corp_code and px) else None
            cm[c.id] = eng.compute_metrics(fs, px, shares, ev, run.price_as_of or "")
            status = "done" if px and fs.latest_quarter() else ("partial" if (px or fs.latest_quarter()) else "skipped")
            detail = []
            if not px:
                detail.append(cm[c.id].na.get("price", "시세 없음"))
            if not fs.latest_quarter():
                detail.append(cm[c.id].na.get("financials", "재무 없음"))
            rl.step(c.id, "prices", "done" if px else "skipped", detail[0] if not px else f"기준일 {px['date']}")
            rl.step(c.id, "valuation", "running" if status != "skipped" else "skipped", "; ".join(detail) or None)

        # ---- valuation for target + listed valuation peers
        val_peer_ids = [cid for cid, m in members.items() if m.use_type == "valuation" and cm[cid].values.get("market_cap")]
        # 비교군 가중치 = STEP 1 적합성 점수(없으면 50) × 활용 구분(적정가치 비교용 1.0, 산업 참고용 0.5). 기술 경쟁용(비상장)은 제외.
        USE_W = {"valuation": 1.0, "industry_reference": 0.5}
        peer_universe = []
        for cid, m in members.items():
            if m.use_type in USE_W and cm[cid].values.get("market_cap"):
                cm[cid].weight = round(((m.score if m.score is not None else 50.0) / 100.0) * USE_W[m.use_type], 4)
                peer_universe.append(cid)
        cm[target.id].weight = 1.0
        valuations: dict[int, dict] = {}
        backlog_info: dict[int, dict] = {}
        for c in companies:
            if c.id != target.id and c.id not in val_peer_ids:
                if cm[c.id].values.get("market_cap"):
                    rl.step(c.id, "valuation", "done", "지표만 산출(적정가치 비교용 아님)")
                continue
            if not cm[c.id].values.get("market_cap") or not fs_map[c.id].latest_quarter():
                rl.step(c.id, "valuation", "skipped", "시세 또는 재무 부족")
                continue
            comps = [cm[i] for i in peer_universe if i != c.id]
            if c.id != target.id and cm[target.id].values.get("market_cap"):
                comps.append(cm[target.id])
            # backlog: official periodic report table → disclosure-based estimate
            book = cd.contract_book(db, c.id, today)
            ob = official_backlog(db, dart, c) if c.corp_code else None
            if ob:
                bl = {"amount": ob["amount"], "source_label": f"{ob['title']} 수주상황 표", "as_of": ob["as_of"],
                      "undisclosed_active": 0, "official": ob}
                ob_eid = ev.add("fact", f"{c.name} 수주잔고(정기보고서)", ob["amount"], "KRW", eng.fmt_krw(ob["amount"]), as_of=ob["as_of"],
                                company=c, method=f"사업의 내용 > 수주상황 표 합계 ({ob['check']})", source="OpenDART 정기보고서",
                                url=ob["url"], rcept_no=ob["rcept_no"], impact="미래이익(수주) 기반 평가의 기초",
                                detail={"quote": ob["quote"], "order_total": ob["order_total"], "delivered": ob["delivered"]})
                bl["eid"] = ob_eid
            else:
                bl = {"amount": book["backlog_estimate"] or None, "source_label": "단일판매·공급계약 공시 기반 잔여금액 추정(기간 비례)",
                      "as_of": today.isoformat(), "undisclosed_active": book["undisclosed_active"]}
                if book["backlog_estimate"]:
                    bl["eid"] = ev.add("estimate", f"{c.name} 공시 기반 추정 수주잔고", book["backlog_estimate"], "KRW",
                                       eng.fmt_krw(book["backlog_estimate"]), as_of=today.isoformat(), company=c,
                                       method="진행 중 계약금액 × 잔여기간 비율(정정 반영, 이행완료·해지 제외)", source="OpenDART 수주공시",
                                       impact="미래이익(수주) 기반 평가의 기초(추정)")
            # 3) 납기 기반 향후 12개월 매출 (정기보고서 수주잔고 + 계약 공시 납기)
            sched = forward_schedule(book, ob, today) if ob else {"available": False,
                                                                 "reason": "산식 검증된 정기보고서 수주잔고가 없어 납기 기반 추정 불가"}
            if sched.get("available"):
                lump = revenue_lumpiness(fs_map[c.id], fs_map[c.id].latest_quarter() or "")
                sched["lumpiness"] = lump
                sb = sched["scenarios"]["base"]["revenue"]
                sched["eid"] = ev.add(
                    "estimate", f"{c.name} 향후 12개월 납기 도래 수주잔고(기준)", sb, "KRW", eng.fmt_krw(sb),
                    period=f"{sched['horizon'][0]} ~ {sched['horizon'][1]}", as_of=sched["as_of"], company=c,
                    method=(f"공식 수주잔고 {eng.fmt_krw(sched['official_backlog'])}({sched['as_of']}) 중 납기가 향후 12개월에 드는 금액. "
                            f"공시 계약 잔여액 {eng.fmt_krw(sched['disclosed_remaining'])}, 비공개 잔고 {eng.fmt_krw(sched['undisclosed_pool'])}"
                            f"(비공개 계약 {sched['undisclosed_contracts']}건에 계약기간 비례 배분: "
                            + ", ".join(f"{p['label'][:24]} {eng.fmt_krw(p['amount'])}·납기 {p['end']}" for p in sched["pieces"] if not p["disclosed"])
                            + f"). {sched['rule']}. "
                            f"시나리오: 보수 {eng.fmt_krw(sched['scenarios']['conservative']['revenue'])} · 낙관 {eng.fmt_krw(sched['scenarios']['optimistic']['revenue'])}"
                            + (f". 납품 시점 인식 근거: 최근 8개 분기 매출 변동계수 {lump:.2f}" if lump is not None else "")
                            + "".join(f". 납기 미정 계약(향후 매출 제외): {u['label'][:30]} {eng.fmt_krw(u['amount']) if u['amount'] else '금액 비공개'}"
                                      f", 납기 변경 {' → '.join(u['end_history'])} → 미정(연기 {u['delays']}회)" for u in sched.get("undetermined", []))),
                    source="OpenDART 정기보고서·수주공시", impact="미래이익(수주) 기반 평가의 매출 가정",
                    detail={"scenarios": sched["scenarios"], "pieces": sched["pieces"][:30], "lumpiness": lump,
                            "undetermined": sched.get("undetermined", [])})
            backlog_info[c.id] = {"book": book, "backlog": bl, "schedule": sched}
            peer_res = eng.method_peer(cm[c.id], comps, ev)
            hist_res = {"method": "history", "label": eng.METHOD_LABEL["history"], "available": False, "notes": ["시세 이력 없음"], "scenarios": {}}
            if c.id in series:
                fr = series[c.id]["frame"].set_index("date")
                month_ends = fr["mcap"].groupby(fr.index.to_period("M")).last()
                month_ends.index = month_ends.index.to_timestamp("M")
                month_ends = month_ends[month_ends.index < latest_date - pd.Timedelta(days=20)]
                hist_res = eng.method_history(cm[c.id], fs_map[c.id], month_ends, ev)
            fwd_res = eng.method_forward(cm[c.id], fs_map[c.id], sched, peer_res, hist_res, ev)
            recent_q = fs_map[c.id].quarters("operating_income")[-4:]
            unstable = sum(1 for q in recent_q if (fs_map[c.id].qval("operating_income", q) or 0) < 0) >= 2
            comb = eng.combine([fwd_res, peer_res, hist_res], cm[c.id].values.get("price"), peer_res.get("peer_count", 0),
                               unstable, sched["undisclosed_contracts"] if sched.get("available") else book["undisclosed_active"],
                               ev, c.name, official_backlog=bool(ob))
            valuations[c.id] = {"forward": fwd_res, "peer": peer_res, "history": hist_res, "combined": comb, "unstable": unstable}
            rl.step(c.id, "valuation", "done", f"{comb['verdict']} · 신뢰도 {comb['confidence']:.0f}")

        # ---- attribution (listed companies with daily series)
        attributions: dict[int, dict] = {}
        for c in companies:
            if c.id not in series or latest_date is None:
                continue
            mk = "KOSPI" if c.market == "KOSPI" else "KOSDAQ"
            peers_r = [series[i]["rets"] for i in val_peer_ids if i != c.id and i in series]
            discs = [{"rcept_no": d.rcept_no, "report_nm": d.report_nm, "rcept_dt": d.rcept_dt, "url": d.url}
                     for d in db.scalars(select(Disclosure).where(Disclosure.company_id == c.id))]
            attributions[c.id] = eng.attribution(series[c.id]["rets"], proxies[mk], peers_r, discs, latest_date)
            attributions[c.id]["market_label"] = f"{mk} 시가총액가중 대용지수"
        ta = attributions.get(target.id, {})
        attr_eid = None
        if ta.get("available"):
            mdl = ta["model"]
            model_eid = ev.add("calc", f"{target.name} 주가 회귀모형(시장·산업 베타)", mdl["r2"], "R2",
                               f"β시장 {mdl['beta_market']:.2f}(t={mdl['t_market']:.1f}) · β산업 "
                               f"{(mdl['beta_industry'] or 0):.2f}(t={(mdl['t_industry'] or 0):.1f}) · R² {mdl['r2']:.2f}",
                               period=" ~ ".join(ta["period"]), as_of=run.price_as_of, company=target, method=mdl["method"],
                               source="ValueScope 회귀분석(KRX 시세)", detail={"model": mdl, "n": ta["n"]})
            for wk, w in ta["windows"].items():
                eid = ev.add("calc", f"{target.name} 주가 변동 분해({wk})", w["total"], "ratio",
                             f"합계 {eng.fmt_pct(w['total'])} = 시장 {eng.fmt_pct(w['market'])} + 산업 {eng.fmt_pct(w['industry'])}"
                             f" + 개별 {eng.fmt_pct(w['individual'])}",
                             period=f"최근 {wk} (일별 수익률 합산 {w['days']}일)", as_of=run.price_as_of, company=target,
                             method=mdl["method"] + " — 기여도 = β × 요인수익률 합계, 개별 = 나머지", source="ValueScope 회귀분석(KRX 시세)",
                             impact="주가 움직임의 시장·산업·개별 요인 구분",
                             detail={"market": w["market"], "industry": w["industry"], "individual": w["individual"], "model_eid": model_eid})
                if wk == "3M" or attr_eid is None:
                    attr_eid = eid
            for d in ta.get("abnormal_days", [])[:8]:
                ev.add("calc", f"{target.name} 개별요인 이상 변동일 {d['date']}", d["return"], "ratio",
                       f"{d['date']} 수익률 {eng.fmt_pct(d['return'])} (개별 {eng.fmt_pct(d['idiosyncratic'])})",
                       as_of=d["date"], company=target, method="회귀 잔차가 2.5σ를 넘는 날 · 당일~2일 전 공시 대조",
                       source="ValueScope 회귀분석 + OpenDART 공시목록",
                       url=d["disclosures"][0]["url"] if d["disclosures"] else None,
                       rcept_no=d["disclosures"][0]["rcept_no"] if d["disclosures"] else None,
                       impact=d["classification"] + (" — 근접 공시: " + "; ".join(f"{x['rcept_dt']} {x['report_nm']}" for x in d["disclosures"]) if d["disclosures"] else ""),
                       detail={"disclosures": d["disclosures"], "idiosyncratic": d["idiosyncratic"]})
        # ---- news events -> evidence, 주가 급변일 연결, 이슈 타임라인
        news_events: list[dict] = []
        news_eids: list[str] = []
        by_cid = {c.id: c for c in companies}
        if news_res is not None:
            for sp in news_specs:
                evs = news_svc.group_events(news_res.items.get(sp.query_key, []))
                news_svc.match_disclosures(db, evs)
                for e in evs:
                    e["scope"], e["scope_label"], e["role"] = sp.query_key, sp.label, sp.role
                    comp = by_cid.get(e["company_id"]) if e.get("company_id") else None
                    who = comp.name if comp else (e.get("about") or sp.label)
                    arts = e["articles"]
                    disc = e.get("disclosure")
                    e["eid"] = ev.add(
                        "news", f"{who} 뉴스: {e['event']}"[:200], None, None,
                        f"{e['date'] or '날짜 미상'}{'' if e['date_certain'] else '(추정)'} {e['event']}"[:100],
                        as_of=e["date"], company=comp,
                        method=(f"{e['summary']} | 보도 {len(arts)}건: " + ", ".join(e["outlets"][:5])
                                + f" | {news_svc.VERIFICATION_LABEL[e['verification']]}"
                                + (f"({disc['rcept_dt']} {disc['report_nm']})" if disc else "")
                                + ("" if e["date_certain"] else " | 보도일 추정")),
                        source="언론 보도(Claude 웹검색, 링크 확인)", url=arts[0]["url"],
                        rcept_no=(disc or {}).get("rcept_no"),
                        impact=f"{news_svc.SENTIMENT_LABEL.get(e['sentiment'], '중립')} · {news_svc.VERIFICATION_LABEL[e['verification']]}",
                        detail={"articles": arts, "disclosure": disc, "sentiment": e["sentiment"],
                                "verification": e["verification"], "date_certain": e["date_certain"], "scope": sp.label})
                    news_eids.append(e["eid"])
                    news_events.append(e)
            # 공시가 없는 주가 급변일에 같은 시기(당일~2일 전) 기사 연결
            if ta.get("available"):
                tnews = [e for e in news_events if e.get("company_id") == target.id and e.get("date")]
                for d in ta.get("abnormal_days", []):
                    dd = date.fromisoformat(d["date"])
                    near = [e for e in tnews if 0 <= (dd - date.fromisoformat(e["date"])).days <= 2]
                    d["news"] = [{"event": e["event"], "date": e["date"], "url": e["articles"][0]["url"],
                                  "outlets": e["outlets"][:3], "eid": e["eid"]} for e in near]
                    if near and not d["disclosures"]:
                        d["classification"] = "언론 보도와 시간적 근접(공시 없음, 인과관계 미확정)"
                attributions[target.id] = ta
        issues: list[dict] = []
        for c in companies:
            if not c.corp_code:
                continue
            days_back = 183 if c.id == target.id else 92
            for row in news_svc.disclosure_timeline(db, c, (today - timedelta(days=days_back)).isoformat(),
                                                    limit=40 if c.id == target.id else 10):
                issues.append({**row, "role": "target" if c.id == target.id else "peer"})
        for e in news_events:
            if e.get("date"):
                who = by_cid[e["company_id"]].name if e.get("company_id") in by_cid else (e.get("about") or e["scope_label"])
                issues.append({"date": e["date"], "date_certain": e["date_certain"], "company": who,
                               "company_id": e.get("company_id"), "type": "news", "title": e["event"], "url": e["articles"][0]["url"],
                               "outlets": e["outlets"][:4], "articles": len(e["articles"]), "sentiment": e["sentiment"],
                               "verification": e["verification"], "disclosure": e.get("disclosure"), "summary": e["summary"],
                               "eid": e["eid"], "scope": e["scope_label"], "role": e["role"]})
        issues.sort(key=lambda x: x["date"], reverse=True)

        for c in companies:
            if c.id in series:
                for k in ("1M", "3M", "6M", "1Y", "3Y"):
                    days = {"1M": 31, "3M": 92, "6M": 183, "1Y": 366, "3Y": 1096}[k]
                    cm[c.id].values[f"ret_{k}"] = window_return(series[c.id]["level"], latest_date, days)

        # ---------------------------------------------------------- 6~7. report + review
        tv = valuations.get(target.id)
        tm = cm[target.id]
        payload = {"name": target.name, "verdict": tv["combined"]["verdict"] if tv else "판단 유보",
                   "verdict_reason": tv["combined"].get("verdict_reason") if tv else "가치평가 불가",
                   "fair_value": tv["combined"].get("scenarios") if tv else None,
                   "confidence": tv["combined"]["confidence"] if tv else 0,
                   "price": tm.values.get("price"), "price_date": tm.values.get("price_date"),
                   "methods": {k: {"available": tv[k]["available"], "notes": tv[k].get("notes"),
                                   "base": tv[k]["scenarios"].get("base", {}).get("per_share") if tv[k].get("scenarios") else None}
                               for k in ("forward", "peer", "history")} if tv else {},
                   "earnings_adjustment": {k: tm.values.get(k) for k in ("core_ni_ttm", "non_recurring_gap", "per_adj", "ebit_norm",
                                                                         "ni_core_norm", "ev_ebit_norm", "pe_norm", "margin_norm")},
                   "peers": [{"name": cm[i].name, "per": cm[i].values.get("per"), "ev_ebit": cm[i].values.get("ev_ebit"),
                              "per_adj": cm[i].values.get("per_adj"), "ev_ebit_norm": cm[i].values.get("ev_ebit_norm"),
                              "pe_norm": cm[i].values.get("pe_norm"),
                              "op_margin": cm[i].values.get("op_margin"), "revenue_growth": cm[i].values.get("revenue_growth")}
                             for i in val_peer_ids],
                   "attribution": {k: v for k, v in ta.items() if k in ("windows", "model", "abnormal_days")} if ta.get("available") else None,
                   "price_eid": tm.eids.get("price"), "mcap_eid": tm.eids.get("market_cap"),
                   "op_eid": tm.eids.get("operating_income_ttm"), "opm_eid": tm.eids.get("op_margin"),
                   "growth_eid": tm.eids.get("revenue_growth"),
                   "peer_eid": tv["peer"].get("eid") if tv else None, "fair_value_eid": tv["combined"].get("eid") if tv else None,
                   "confidence_eid": tv["combined"].get("confidence_eid") if tv else None, "attr_eid": attr_eid,
                   "news_eids": news_eids, "news_searched": news_res is not None}
        rl.stage("AI 분석 보고서 작성", f"{ai.name}")
        rep = generate_report(ai, payload, ev.items)
        rl.msg("info" if rep["status"] == "generated" else "warning",
               f"보고서: {'AI 생성' if rep['status'] == 'generated' else '규칙 기반'} · 문단 {rep['review']['paragraphs']}개 중 검증 실패 {rep['review']['unverified']}개")
        for e in rep["review"]["errors"]:
            rl.msg("warning", e)

        # ---------------------------------------------------------- 8. persist snapshot
        rl.stage("결과 저장")
        eid_map = {}
        for item in ev.items:
            item = clean(item)
            item["display"] = (item.get("display") or "")[:100]
            row = AnalysisEvidence(run_id=run.id, **item)
            db.add(row)
            eid_map[item["eid"]] = row
        for c in companies:
            v = valuations.get(c.id)
            m = members.get(c.id)
            val = Valuation(run_id=run.id, company_id=c.id, is_target=c.id == target.id,
                            category=m.category if m else None, use_type=m.use_type if m else "target",
                            metrics=clean({"values": cm[c.id].values, "na": cm[c.id].na, "eids": cm[c.id].eids,
                                     "quarters": eng.quarter_table(fs_map[c.id]),
                                     "annual": {k: fs_map[c.id].annual(k) for k in ("revenue", "operating_income", "net_income_owners")},
                                     "splits": series.get(c.id, {}).get("splits", [])}),
                            methods=clean({k: v[k] for k in ("forward", "peer", "history")}) if v else {},
                            fair_value=clean(v["combined"]) if v else {}, verdict=v["combined"]["verdict"] if v else None,
                            confidence=v["combined"]["confidence"] if v else None,
                            confidence_breakdown=v["combined"]["confidence_breakdown"] if v else [],
                            attribution=clean(attributions.get(c.id, {})),
                            data_status=clean({"backlog": backlog_info.get(c.id, {}).get("backlog"),
                                         "schedule": backlog_info.get(c.id, {}).get("schedule"),
                                         "contracts": backlog_info.get(c.id, {}).get("book") or cd.contract_book(db, c.id, today),
                                         "progress": run.progress.get("companies", {}).get(str(c.id), {})}))
            db.add(val)
            db.flush()
            if v:
                for mk in ("forward", "peer", "history"):
                    for sc, r in (v[mk].get("scenarios") or {}).items():
                        comp = r.get("components") or {}
                        first = next(iter(comp.values()), r)
                        db.add(ValuationScenario(valuation_id=val.id, method=mk, scenario=sc,
                                                 multiple_name=next(iter(comp), None) or ("EV/EBIT" if mk == "forward" else None),
                                                 multiple=first.get("multiple"), base_metric=first.get("base_metric") or r.get("forward_ebit"),
                                                 enterprise_value=first.get("enterprise_value"), equity_value=first.get("equity_value"),
                                                 per_share=r.get("per_share"), detail=clean(r)))
                for sc, ps in (v["combined"].get("scenarios") or {}).items():
                    db.add(ValuationScenario(valuation_id=val.id, method="combined", scenario=sc, per_share=ps,
                                             detail={"weights": v["combined"]["weights"]}))
        db.add(AnalysisReport(run_id=run.id, status=rep["status"], ai_model=rep["ai_model"], content=clean(rep["content"]), review=clean(rep["review"])))
        run.charts = clean({**build_charts(companies, target, cm, series, proxies, latest_date, valuations, backlog_info, members),
                            "issues": issues, "news_enabled": news_res is not None})
        run.summary = clean({"verdict": payload["verdict"], "verdict_reason": payload["verdict_reason"], "fair_value": payload["fair_value"],
                       "confidence": payload["confidence"], "price": payload["price"], "price_date": payload["price_date"],
                       "market_cap": tm.values.get("market_cap"), "upside_base": tv["combined"].get("upside_base") if tv else None,
                       "report_summary": rep["content"].get("summary"), "fair_value_eid": payload["fair_value_eid"],
                       "valuation_peers": [cm[i].name for i in val_peer_ids]})
        run.assumptions = {"backlog_conversion": eng.BACKLOG_CONVERSION, "method_weights": eng.METHOD_WEIGHT,
                           "verdict_rule": "현재가 < 보수적 → 저평가, > 낙관적 → 고평가, 그 사이 → 적정. 방법 2개 미만·신뢰도 40 미만 → 판단 유보",
                           "market_index": "KRX 일별매매정보 기반 시가총액가중 대용지수 (공식 지수 API 미승인)",
                           "price_adjustment": "KRX 수정주가 미제공 — 상장주식수 변동으로 분할·병합 보정",
                           "fiscal_year": "12월 결산 가정",
                           "news": ("Claude 웹검색(가벼운 모델)으로 기사 제목·언론사·날짜·링크만 수집, 링크 확인 후 사건별로 묶음. "
                                    "공시 대조로 '공시로 확인/기사로만 확인' 구분. 보조 근거이며 적정가 계산에는 미사용")
                                   if news_res is not None else "뉴스 미사용"}
        run.data_versions = clean({"price_as_of": run.price_as_of,
                             "financials": {cm[c.id].name: fs_map[c.id].latest_quarter() for c in companies},
                             "peer_version": run.peer_version_no})
        run.status = "partial" if failures else "succeeded"
        run.finished_at = utcnow()
        project.last_analysis_at = run.finished_at
        project.last_analysis_status = run.status
        project.current_step = max(project.current_step, 3)
        rl.stage("완료")
        db.commit()
    except Exception as e:  # noqa: BLE001
        log.exception("analysis run failed")
        db.rollback()
        run = db.get(AnalysisRun, run_id)
        run.status = "failed"
        run.finished_at = utcnow()
        run.messages = [*run.messages, {"level": "error", "message": f"분석 실패: {type(e).__name__}: {e}",
                                        "trace": traceback.format_exc()[-1500:]}]
        project = db.get(Project, run.project_id)
        project.last_analysis_status = "failed"
        db.commit()
    finally:
        db.close()


def _series_points(level: pd.Series, since: pd.Timestamp) -> list[list]:
    s = level[level.index >= since]
    if s.empty:
        return []
    base = s.iloc[0]
    return [[d.strftime("%Y-%m-%d"), round(float(v / base * 100), 2)] for d, v in s.items()]


def build_charts(companies, target, cm, series, proxies, latest_date, valuations, backlog_info, members) -> dict:
    charts: dict = {"companies": [{"id": c.id, "name": c.name, "is_target": c.id == target.id,
                                   "role": members[c.id].use_type if c.id in members else "target"} for c in companies]}
    if latest_date is not None:
        since = latest_date - pd.Timedelta(days=1100)
        charts["price_index"] = {str(c.id): _series_points(series[c.id]["level"], since) for c in companies if c.id in series}
        mk = "KOSPI" if target.market == "KOSPI" else "KOSDAQ"
        charts["market_index"] = {"label": f"{mk} 시가총액가중 대용지수", "points": _series_points(cumulative(proxies[mk]), since)}
        if target.id in series:
            t = series[target.id]["level"]
            m = cumulative(proxies[mk]).reindex(t.index).ffill()
            ex = (t / t[t.index >= since].iloc[0]) / (m / m[m.index >= since].iloc[0]) * 100 - 100
            charts["excess_return"] = [[d.strftime("%Y-%m-%d"), round(float(v), 2)] for d, v in ex[ex.index >= since].items() if pd.notna(v)]
    charts["market_caps"] = [{"id": c.id, "name": c.name, "value": cm[c.id].values.get("market_cap")} for c in companies]
    keys = ("per", "pbr", "psr", "ev_ebit", "ev_ebitda", "per_adj", "ev_ebit_norm", "pe_norm")
    charts["multiples"] = [{"id": c.id, "name": c.name, **{k: cm[c.id].values.get(k) for k in keys},
                            "na": {k: cm[c.id].na.get(k) for k in keys}} for c in companies]
    tv = valuations.get(target.id)
    if tv:
        charts["scenarios"] = {mk: {s: (tv[mk]["scenarios"].get(s) or {}).get("per_share") for s in eng.SCENARIOS}
                               for mk in ("forward", "peer", "history") if tv[mk].get("available")}
        charts["scenarios"]["combined"] = tv["combined"].get("scenarios")
        fwd = tv["forward"]
        if fwd.get("available"):
            charts["forward"] = {s: {k: fwd["scenarios"][s][k] for k in ("forward_revenue", "forward_ebit")} for s in eng.SCENARIOS}
    bi = backlog_info.get(target.id)
    if bi:
        monthly: dict[str, float] = {}
        for ct in bi["book"]["contracts"]:
            if ct["amount"] and ct["contract_date"]:
                monthly[ct["contract_date"][:7]] = monthly.get(ct["contract_date"][:7], 0) + ct["amount"]
        charts["contracts_monthly"] = sorted(monthly.items())
    return charts
