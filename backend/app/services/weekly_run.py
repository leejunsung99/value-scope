"""주간 주가 분석 실행: 시세·공시 최신화 → 해당 주 뉴스 검색 → 계산 → AI 해석(근거 검증) → 저장."""
from __future__ import annotations

import json
import logging
import traceback
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.models import Project, utcnow
from app.models_analysis import PriceFetch, WeeklyAnalysis
from app.services import engine as eng
from app.services import news as news_svc
from app.services import weekly as wk
from app.services.ai import AIError, AIProvider, AIUnavailable
from app.services.budget import BudgetExceeded, CallBudget
from app.services.report import INVEST_ADVICE, check_numbers, known_values

log = logging.getLogger(__name__)

SECTIONS = [("overview", "주간 요약"), ("comovement", "비교군 동조 여부"), ("idiosyncratic", "개별 요인·이벤트"),
            ("volatility", "변동성·거래량"), ("watch", "다음 주 확인할 점")]

WRITER_SYSTEM = """당신은 한국 상장사 주가 동향을 정리하는 애널리스트입니다. 1주일(월~일) 주가 움직임만 다룹니다(밸류에이션 언급 금지).
규칙:
- 제공된 근거(evidence)의 수치·사실만 쓰고, 수치는 근거의 display 값을 그대로 옮기십시오. 새로 계산하지 마십시오.
- 모든 문단에 근거 ID를 1개 이상 붙이십시오.
- 엔진 판정(pattern_label)을 바꾸지 마십시오.
- 공시·보도와 주가 변동의 시간적 근접을 인과로 단정하지 마십시오. 보도(kind=news)는 '보도에 따르면'으로 쓰고, 공시로 확인된 경우만 사실로 쓰십시오.
- 원인이 확인되지 않은 변동은 '확인된 공시·보도 없음'이라고 쓰십시오.
- 매수·매도·보유·목표주가 등 투자 권유 표현 금지. 한국어."""


def writer_schema() -> dict:
    para = {"type": "object", "properties": {"text": {"type": "string"}, "evidence_ids": {"type": "array", "items": {"type": "string"}}},
            "required": ["text", "evidence_ids"], "additionalProperties": False}
    sec = {"type": "object", "properties": {"key": {"type": "string", "enum": [k for k, _ in SECTIONS]},
                                            "paragraphs": {"type": "array", "items": para}},
           "required": ["key", "paragraphs"], "additionalProperties": False}
    return {"type": "object", "properties": {"headline": {"type": "string"}, "sections": {"type": "array", "items": sec}},
            "required": ["headline", "sections"], "additionalProperties": False}


def pct(v):
    return "N/A" if v is None else f"{v * 100:+.1f}%"


def pa(v):
    """부호 없는 퍼센트(변동성·변동폭)."""
    return "N/A" if v is None else f"{v * 100:.1f}%"


def build_evidence(target_name: str, res: dict, discs: list[dict], news_events: list[dict]) -> eng.Evidence:
    ev = eng.Evidence()
    s, w = res["summary"], res["week"]
    period = f"{w['start']}~{w['end']} ({w['trading_days']}거래일, 기준 {w['base_date']} 종가)"
    s["eids"] = {
        "target": ev.add("calc", f"{target_name} 주간 수익률", s["target_return"], "ratio", pct(s["target_return"]), period=period,
                         method="해당 주 일별 수익률 복리 합산(분할·병합 보정)", source="KRX 일별매매정보"),
        "peers": ev.add("calc", "비교군 가중평균 주간 수익률", s["peer_return"], "ratio", pct(s["peer_return"]), period=period,
                        method="비교기업 일별 수익률을 STEP 1 적합성 점수×활용구분 가중치로 평균 후 복리", source="KRX 일별매매정보"),
        "market": ev.add("calc", f"{res['model']['market_label']} 주간 수익률", s["market_return"], "ratio", pct(s["market_return"]),
                         period=period, method="시가총액가중 대용지수(공식 지수 아님)", source="KRX 일별매매정보"),
        "excess": ev.add("calc", f"{target_name} 비교군 대비 초과수익률", s["excess_vs_peers"], "ratio",
                         f"{pct(s['excess_vs_peers'])} ({s['excess_vs_peers'] * 100:+.1f}%p)" if s["excess_vs_peers"] is not None else "N/A",
                         period=period, method="고객사 주간 수익률 − 비교군 주간 수익률", source="ValueScope"),
        "excess_mkt": ev.add("calc", f"{target_name} 시장 대비 초과수익률", s["excess_vs_market"], "ratio",
                             f"{s['excess_vs_market'] * 100:+.1f}%p", period=period, method="고객사 주간 수익률 − 시장 대용지수 주간 수익률",
                             source="ValueScope"),
        "pattern": ev.add("calc", f"{target_name} 주간 개별 요인 판정", s["z_week"], "z",
                          f"{s['pattern_label']} · 개별 요인 합계 {pct(s['residual_sum'])} (z={s['z_week']:.2f})" if s["z_week"] is not None else s["pattern_label"],
                          period=period, method=(f"기대수익률 = α + β시장({res['model']['beta_market']:.2f})×시장 + β산업"
                                                 f"({(res['model']['beta_industry'] or 0):.2f})×비교군(시장 제거분), 직전 {res['model']['n']}거래일로 적합. "
                                                 f"개별 요인 = 실제 − 기대. |z주간| ≥ {wk.Z_WEEK} → 개별 요인 우세"),
                          source="ValueScope 회귀분석(KRX)"),
        "comove": ev.add("calc", "고객사·비교군 같은 방향 일수", s["same_dir_days"], "일", f"{s['same_dir_days']}/{s['judged_days']}일 같은 방향",
                         period=period, method="일별 수익률 부호 비교(고객사 vs 비교군 가중평균)", source="ValueScope"),
    }
    if w.get("base_close") and w.get("last_close"):
        s["eids"]["price"] = ev.add("fact", f"{target_name} 주간 종가 변화", w["last_close"], "KRW/주",
                                    f"{w['base_close']:,.0f}원({w['base_date']}) → {w['last_close']:,.0f}원", period=period,
                                    source="KRX 일별매매정보", detail={"base_close": w["base_close"], "last_close": w["last_close"]})
    v = res.get("volatility") or {}
    s["eids"]["volatility"] = ev.add(
        "calc", f"{target_name} 변동성·거래량(직전 대비)", v.get("std_ratio"), "x",
        f"수익률 변동성 {v['std_ratio']:.1f}배 · 일중 변동폭 {(v.get('range_ratio') or 0):.1f}배 · 거래량 {(v.get('volume_ratio') or 0):.1f}배"
        if v.get("std_ratio") else "산출 불가", period=period,
        method=(f"주중 일별 수익률 표준편차 {pa(v.get('ret_std_week'))} vs 직전 60거래일 {pa(v.get('ret_std_60d'))}; "
                f"평균 (고가−저가)/전일종가 {pa(v.get('range_week'))} vs {pa(v.get('range_60d'))}; 평균 거래량 vs 직전 20거래일"),
        source="KRX 일별매매정보", detail={k: v.get(k) for k in ("ret_std_week", "ret_std_60d", "range_week", "range_60d",
                                                              "volume_week", "volume_20d", "std_ratio", "range_ratio", "volume_ratio")})
    for d in res["days"]:
        d["eid"] = ev.add("calc", f"{target_name} {d['date']}({d['weekday']}) 일간 변동", d["ret"], "ratio",
                          f"{d['date']} 수익률 {pct(d['ret'])} · 비교군 {pct(d['peer_avg'])} · 시장 {pct(d['market'])} · 개별 요인 {pct(d['residual'])}"
                          + (f" (z={d['z']:.1f}{', 이상 변동' if d['abnormal'] else ''})" if d.get("z") is not None else ""),
                          as_of=d["date"], method="개별 요인 = 실제 − (α + β시장×시장 + β산업×비교군 시장제거분)", source="KRX, ValueScope")
    for p in res["peers"]:
        if p.get("available"):
            p["eid"] = ev.add("calc", f"{p['name']} 주간 수익률", p["week_return"], "ratio", pct(p["week_return"]), period=period,
                              method=f"고객사와 같은 방향 {p['same_dir_days']}일, 직전 60거래일 상관계수 "
                                     + (f"{p['corr_60d']:.2f}" if p.get("corr_60d") is not None else "N/A"), source="KRX 일별매매정보")
    for x in discs:
        x["eid"] = ev.add("fact", f"{x['company']} 공시: {x['title']}", None, None, f"{x['date']} {x['title']}"[:100], as_of=x["date"],
                          source="OpenDART", url=x["url"], rcept_no=x["rcept_no"])
    for e in news_events:
        e["eid"] = ev.add("news", f"{e.get('about') or ''} 보도: {e['event']}"[:200], None, None,
                          f"{e['date'] or '날짜 미상'}{'' if e['date_certain'] else '(추정)'} {e['event']}"[:100], as_of=e["date"],
                          method=f"{e['summary']} | 보도 {len(e['articles'])}건: {', '.join(e['outlets'][:4])} | "
                                 f"{news_svc.VERIFICATION_LABEL.get(e.get('verification', 'news_only'))}",
                          source="언론 보도(Claude 웹검색, 링크 확인)", url=e["articles"][0]["url"],
                          detail={"articles": e["articles"]})
    return ev


def rule_based(target_name: str, res: dict) -> dict:
    s, ids = res["summary"], res["summary"]["eids"]
    paras = {
        "overview": [{"text": f"{target_name} 주간 수익률은 {pct(s['target_return'])}, 비교군 {pct(s['peer_return'])}, 시장 {pct(s['market_return'])}입니다. "
                              f"판정은 '{s['pattern_label']}'입니다.", "evidence_ids": [ids["target"], ids["peers"], ids["market"], ids["pattern"]]}],
        "comovement": [{"text": f"고객사와 비교군은 {s['same_dir_days']}/{s['judged_days']}일 같은 방향으로 움직였습니다.", "evidence_ids": [ids["comove"]]}],
        "idiosyncratic": [{"text": ("이상 변동일: " + ", ".join(s["abnormal_days"])) if s["abnormal_days"] else "개별 요인 이상 변동일은 없었습니다.",
                           "evidence_ids": [ids["pattern"]]}],
        "volatility": [{"text": f"변동성 판정: {s['volatility_label']}.", "evidence_ids": [ids["volatility"]]}],
        "watch": [],
    }
    return {"headline": f"{target_name} 주간 {pct(s['target_return'])} — {s['pattern_label']}",
            "sections": [{"key": k, "paragraphs": v} for k, v in paras.items()]}


def write_report(ai: AIProvider, target_name: str, res: dict, ev: eng.Evidence) -> dict:
    errors, status, model = [], "generated", None
    payload = {"target": target_name, "week": res["week"], "summary": {k: v for k, v in res["summary"].items() if k != "eids"},
               "days": [{k: d.get(k) for k in ("date", "weekday", "ret", "peer_avg", "market", "residual", "z", "abnormal", "eid")} for d in res["days"]],
               "peers": [{k: p.get(k) for k in ("name", "week_return", "same_dir_days", "corr_60d", "eid")} for p in res["peers"]]}
    try:
        r = ai.complete_json(WRITER_SYSTEM,
                             "## 엔진 결과\n" + json.dumps(payload, ensure_ascii=False, default=str)
                             + "\n\n## 근거(evidence)\n" + json.dumps([{k: e[k] for k in ("eid", "kind", "label", "display", "as_of", "method", "source")}
                                                                    for e in ev.items], ensure_ascii=False)
                             + "\n\n## 작성 지시\n섹션: " + ", ".join(f"{k}={t}" for k, t in SECTIONS)
                             + "\n각 섹션 1~2개 문단, 간결하게. headline은 이번 주를 한 문장으로(판정 포함).",
                             writer_schema())
        content, model = r.data, r.model
    except (AIUnavailable, AIError) as e:
        errors.append(f"AI 해석 생성 불가: {e} — 규칙 기반 요약")
        status, content = "rule_based", rule_based(target_name, res)
    values = known_values(ev.items)
    ids = {e["eid"] for e in ev.items}
    titles = dict(SECTIONS)
    out, held = [], []
    for sec in content.get("sections", []):
        paras = []
        for i, p in enumerate(sec.get("paragraphs", [])):
            issues = []
            if not [x for x in p.get("evidence_ids", []) if x in ids]:
                issues.append("유효한 근거 ID 없음")
            if INVEST_ADVICE.search(p.get("text", "")):
                issues.append(f"투자 권유 표현: {INVEST_ADVICE.search(p['text']).group(0)}")
            bad = check_numbers(p.get("text", ""), values)
            if bad:
                issues.append("근거와 다른 수치: " + ", ".join(bad[:4]))
            item = {"ref": f"{sec.get('key')}.{i + 1}", "text": p.get("text", ""), "evidence_ids": [x for x in p.get("evidence_ids", []) if x in ids]}
            if issues:
                held.append({**item, "section": titles.get(sec.get("key"), sec.get("key")), "issues": issues})
            else:
                paras.append(item)
        out.append({"key": sec.get("key"), "title": titles.get(sec.get("key"), sec.get("key")), "paragraphs": paras})
    headline = content.get("headline") or ""
    if not headline or check_numbers(headline, values) or INVEST_ADVICE.search(headline):
        headline = rule_based(target_name, res)["headline"]
    order = {k: i for i, (k, _) in enumerate(SECTIONS)}
    out.sort(key=lambda x: order.get(x["key"], 99))
    return {"status": status, "model": model, "headline": headline, "sections": out, "held": held, "errors": errors,
            "verification": "수치·근거 ID·투자권유 표현 대조(결정적 검증)"}


def _msg(db: Session, w: WeeklyAnalysis, level: str, text: str) -> None:
    w.messages = [*w.messages, {"level": level, "message": text, "at": datetime.now().isoformat(timespec="seconds")}]
    db.commit()


def _stage(db: Session, w: WeeklyAnalysis, text: str) -> None:
    w.stage = text
    db.commit()


def execute_weekly(session_factory: sessionmaker, wid: str, krx, dart, monitor, ai: AIProvider, news_searcher=None,
                   today: date | None = None) -> None:
    from app.services import collect_disclosures as cd
    from app.services.collect_prices import KrxError, collect_prices

    today = today or date.today()
    db = session_factory()
    w = db.get(WeeklyAnalysis, wid)
    w.status = "running"
    db.commit()
    s = get_settings()
    try:
        project = db.get(Project, w.project_id)
        target = project.company
        version = project.peer_group.versions[-1]
        peers = wk.peer_infos(version)
        mon, sun = wk.week_bounds(w.week_start)
        if mon > today:
            raise wk.WeeklyError("아직 시작하지 않은 주는 분석할 수 없습니다.")
        _msg(db, w, "info", f"{target.name} 주간 주가 분석 {mon}~{sun} · 비교군 v{version.version_no} 상장 비교기업 {len(peers)}개")

        # 1) 시세 최신화 (해당 주 거래일이 저장돼 있지 않을 때만)
        have = db.scalar(select(PriceFetch.trade_date).where(PriceFetch.trade_date >= mon.strftime("%Y%m%d"),
                                                            PriceFetch.trade_date <= sun.strftime("%Y%m%d")))
        if krx is not None and (not have or sun >= today):
            _stage(db, w, "주가 수집")
            krx.on_call = CallBudget(session_factory, "KRX", s.krx_daily_call_limit)
            try:
                st = collect_prices(db, krx, today)
                _msg(db, w, "info", f"KRX 시세: 신규 {st['requested']}일 · 재사용 {st['reused']}일")
            except (BudgetExceeded, KrxError) as e:
                _msg(db, w, "warning", f"시세 최신화 실패 — 저장된 시세로 분석: {e}")
        # 2) 공시 최신화 (고객사 + 비교기업)
        _stage(db, w, "공시 확인")
        if dart is not None:
            dart.on_call = CallBudget(session_factory, "DART", s.dart_daily_call_limit)
        companies = [target] + [p.company for p in peers]
        for c in companies:
            if c.corp_code and dart is not None:
                try:
                    cd.collect_disclosure_list(db, dart, monitor, c, today)
                except Exception as e:  # noqa: BLE001
                    _msg(db, w, "warning", f"{c.name} 공시 최신화 실패: {type(e).__name__}: {e}")
        discs = wk.week_disclosures(db, companies, mon, sun)

        # 3) 해당 주 뉴스
        news_events: list[dict] = []
        if w.options.get("include_news", True) and news_searcher is not None:
            _stage(db, w, "뉴스 검색")
            win = (mon.isoformat(), sun.isoformat())
            peer_names = ", ".join(p.company.name for p in peers)
            specs = [news_svc.SearchSpec(f"week:{target.id}:{mon}", target.name,
                                         f"'{target.name}'(종목코드 {target.stock_code}) 관련 뉴스를 웹검색으로 찾으십시오. 주가 변동 사유, 수주, 실적, 공시, 업황 포함.",
                                         months=1, max_items=10, company_id=target.id, role="target", window=win)]
            if peer_names:
                specs.append(news_svc.SearchSpec(f"week-peers:{project.id}:{mon}", "비교기업",
                                                 f"다음 기업들의 주요 뉴스를 웹검색으로 찾으십시오: {peer_names}. about에는 기사 주 대상 기업명을 쓰십시오.",
                                                 months=1, max_items=10, role="peer", window=win))
            nres = news_svc.run_searches(db, news_searcher, specs, today, s.news_daily_search_limit)
            by_name = {c.name: c.id for c in companies}
            for sp in specs:
                evs = news_svc.group_events(nres.items.get(sp.query_key, []))
                for e in evs:
                    if not e.get("company_id"):
                        e["company_id"] = next((cid for nm, cid in by_name.items() if nm and nm in (e.get("about") or "")), None)
                news_svc.match_disclosures(db, evs)
                news_events += evs
            for o in nres.outcomes:
                _msg(db, w, "warning" if o.status in ("failed", "skipped_limit") else "info",
                     f"뉴스 {o.spec.label}: {o.status}" + (f" — {o.error[:120]}" if o.error else "")
                     + f" · 기사 {len(nres.items.get(o.spec.query_key, []))}건")
        elif news_searcher is None and w.options.get("include_news", True):
            _msg(db, w, "info", "뉴스 검색 사용 불가(AI_PROVIDER=claude_cli 필요) — 공시만 연결")

        # 4) 계산
        _stage(db, w, "주가 동조·개별 요인 계산")
        res = wk.compute(db, target, peers, mon, sun)
        wk.attach_events(res, target.id, discs, news_events)
        res["disclosures"] = discs
        res["news"] = [{"date": e["date"], "date_certain": e["date_certain"], "company_id": e.get("company_id"),
                        "company": next((c.name for c in companies if c.id == e.get("company_id")), e.get("about") or ""),
                        "event": e["event"], "url": e["articles"][0]["url"], "outlets": e["outlets"][:4], "articles": len(e["articles"]),
                        "sentiment": e["sentiment"], "verification": e.get("verification"), "summary": e.get("summary")} for e in news_events]
        res["news_enabled"] = news_searcher is not None and w.options.get("include_news", True)
        res["peer_list"] = [{"company_id": p.company.id, "name": p.company.name, "weight": p.weight} for p in peers]

        # 5) 해석
        _stage(db, w, "AI 해석 작성")
        ev = build_evidence(target.name, res, discs, news_events)
        rep = write_report(ai, target.name, res, ev)
        rep["evidence"] = ev.items
        for e in rep["errors"]:
            _msg(db, w, "warning", e)
        w.result = json.loads(json.dumps(res, default=str))
        w.report = json.loads(json.dumps(rep, default=str))
        w.status = "succeeded"
        w.stage = "완료"
        w.finished_at = utcnow()
        db.commit()
    except Exception as e:  # noqa: BLE001
        log.exception("weekly analysis failed")
        db.rollback()
        w = db.get(WeeklyAnalysis, wid)
        w.status = "failed"
        w.stage = "실패"
        w.finished_at = utcnow()
        msg = str(e) if isinstance(e, wk.WeeklyError) else f"{type(e).__name__}: {e}"
        w.messages = [*w.messages, {"level": "error", "message": f"주간 분석 실패: {msg}",
                                    **({} if isinstance(e, wk.WeeklyError) else {"trace": traceback.format_exc()[-1200:]})}]
        db.commit()
    finally:
        db.close()


def create_weekly(db: Session, project: Project, week_start: str, include_news: bool = True) -> WeeklyAnalysis:
    group = project.peer_group
    if not group or not group.versions:
        raise ValueError("확정된 비교군이 없습니다. STEP 1에서 비교기업을 확정하세요.")
    mon, sun = wk.week_bounds(week_start)
    running = db.scalar(select(WeeklyAnalysis).where(WeeklyAnalysis.project_id == project.id,
                                                     WeeklyAnalysis.status.in_(["queued", "running"])))
    if running:
        raise RuntimeError(f"이미 실행 중인 주간 분석이 있습니다: {running.week_start}")
    w = WeeklyAnalysis(project_id=project.id, peer_version_no=group.versions[-1].version_no, week_start=str(mon), week_end=str(sun),
                       status="queued", options={"include_news": include_news}, messages=[], result={}, report={})
    db.add(w)
    db.commit()
    return w


def to_dict(w: WeeklyAnalysis, full: bool = False) -> dict:
    d = {"id": w.id, "project_id": w.project_id, "week_start": w.week_start, "week_end": w.week_end, "status": w.status,
         "stage": w.stage, "peer_version_no": w.peer_version_no, "started_at": w.started_at, "finished_at": w.finished_at,
         "summary": (w.result or {}).get("summary"), "headline": (w.report or {}).get("headline")}
    if full:
        d.update({"messages": w.messages, "result": w.result, "report": w.report, "options": w.options})
    project = w.project if hasattr(w, "project") else None
    if project is None:
        from sqlalchemy.orm import object_session

        sess = object_session(w)
        project = sess.get(Project, w.project_id) if sess else None
    d["company_name"] = project.company.name if project and project.company else None
    return d
