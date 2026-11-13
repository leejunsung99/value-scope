"""주간 주가 분석 실행: 시세·공시 최신화 → 해당 주 뉴스 검색 → 계산 → AI 해석(근거 검증) → 저장."""
from __future__ import annotations

import json
import logging
import re
import traceback
from datetime import date, datetime, timedelta

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

SECTIONS = [("overview", "주간 요약"), ("comovement", "비교기업과의 동조"), ("drivers", "비교기업 대비 흐름의 배경"),
            ("idiosyncratic", "회사 고유 이슈"), ("volatility", "변동성·거래량")]


def drivers_title(res: dict) -> str:
    gap = (res.get("summary") or {}).get("excess_vs_peers")
    if gap is None:
        return "비교기업 대비 흐름의 배경"
    return "비교기업 대비 약세 배경" if gap < 0 else "비교기업 대비 강세 배경"

# 'AI 티'가 나는 얼버무림·면책성 문장: 문단에서 해당 문장만 뺀다 (인과 단정 금지 원칙은 보고서 하단 고지 한 줄로 대신)
HEDGE = re.compile(r"단정(하기|할 수|짓기)|인과\s*관계|확인된 자료만으로|관계는 확인되지|확인되지 않(는다|습니다|았다|았습니다)|"
                   r"판단하기 어렵|알 수 없|보기 어렵|단언하기|명확하지 않|추가 확인이 필요|해석에 주의|신중할 필요|"
                   r"기 어렵(다|습니다|고)|영향을 (줬|주었|미쳤)는지|얼마나 영향|가능성을 배제")
SENT_SPLIT = re.compile(r"(?<=[다요음]\.)\s+")


def strip_hedges(text: str) -> str:
    keep = [x for x in SENT_SPLIT.split(text.strip()) if x and not HEDGE.search(x)]
    return " ".join(keep).strip()


# 보고서에 쓰지 않는 통계·모형 용어 (쓰면 해당 문단을 게시하지 않음)
JARGON = re.compile(r"z\s*[=값]|z값|표준편차|회귀|β|베타|R²|R2|잔차|통계적|유의(?:성|미|수준|하지)|기대수익률|기대치|σ|시그마|개별\s*요인|이상치|abnormal|엔진|대용\s*지수", re.I)

WRITER_SYSTEM = """당신은 증권사 리서치센터의 주간 주가 동향 리포트를 쓰는 애널리스트입니다. 1주일(월~일) 주가 흐름만 다룹니다(밸류에이션 언급 금지).
문체:
- 고객사 경영진·IR 담당자가 바로 읽을 수 있는 리포트 문체(평서형 '~했다', '~이다')로, 짧고 명확하게 쓰십시오.
- 통계·모형 용어를 쓰지 마십시오: z값, 표준편차, 회귀, 베타(β), R², 잔차, 통계적 유의성, 기대수익률, 개별 요인, 이상치 등은 금지.
  대신 '비교기업보다 X%p 더 내렸다', '시장·업종 흐름과 다르게 움직였다', '평소보다 변동이 컸다'처럼 쉬운 말로 쓰십시오.
- 수치는 수익률(%), 비교기업·시장과의 차이(%p), 같은 방향으로 움직인 일수, 종가, 거래량 배수만 사용하십시오.
- 날짜는 '11월 3일(화)'처럼 쓰십시오. 시장은 '시장(KOSDAQ)'으로만 부르고, 분석 도구·지수 산출 방식·판정 기준은 설명하지 마십시오('엔진', '대용지수' 금지).
규칙:
- 제공된 근거(evidence)의 수치·사실만 쓰고, 수치는 근거의 display 값을 그대로 옮기십시오. 새로 계산하지 마십시오.
- 모든 문단에 근거 ID를 1개 이상 붙이십시오.
- 엔진 판정(pattern_label)을 바꾸지 마십시오.
- 공시·보도와 주가 변동은 같은 날 있었던 사실로만 나란히 쓰고, 둘 사이의 인과를 주장하지 마십시오.
  '단정하기 어렵다', '인과관계는 확인되지 않는다', '확인된 자료만으로는' 같은 얼버무림·면책 문장은 쓰지 마십시오(보고서 하단에 고지가 따로 있음).
- 보도(kind=news)는 '~라고 보도됐다'로 쓰고, 공시로 확인된 경우만 사실로 쓰십시오.
- 주간 요약(overview)에는 주가 흐름과 함께, 이번 주 회사 관련 공시·보도(target_issues)가 있었는지를 반드시 쓰십시오.
  있으면 날짜와 제목을 짧게 쓰고, 없으면 '이번 주 회사 관련 공시나 보도는 없었다'고 쓰십시오.
- 비교기업 대비 흐름의 배경(drivers)에는 고객사가 비교기업·시장보다 더 내리거나(오르거나) 다르게 움직인 이유를 근거로 설명하십시오.
  다음 근거를 순서대로 검토해, 근거가 있는 것만 쓰십시오(2~3개 문단):
  ① 직전 4주 흐름(context): 직전 4주 동안 고객사와 비교기업의 수익률 차이 — 예: 비교기업이 먼저 크게 내릴 때 고객사는 버텼다면 이번 주 하락은 그 격차가 좁혀진 흐름으로 설명. 5주 누적 수익률도 함께 비교.
  ② 차이가 벌어진 날(diverged_days): 그날 고객사·비교기업 등락, 거래량(평소 대비), 같은 날 공시·보도.
  ③ 직전 4주 회사 공시 중 주식 수급·투자심리에 영향을 줄 수 있는 것(전환청구·대량보유 변동·지분 매도·유상증자·계약 정정 등).
  ④ 관련 보도(kind=news)에서 언급된 약세·강세 사유.
  해석은 '~한 흐름으로 볼 수 있다', '~의 영향이 반영된 것으로 보인다'처럼 근거 바로 뒤에 한 번만 붙이고, 근거 없는 사유(수급 주체, 루머 등)는 만들지 마십시오.
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


def target_issues(target_id: int | None, discs: list[dict], news_events: list[dict]) -> dict:
    """이번 주 고객사 공시·보도 요약 (주간 요약에 '특별 이슈 유무'를 쓰기 위한 근거)."""
    d = [x for x in discs if target_id is None or x["company_id"] == target_id]
    n = [e for e in news_events if target_id is None or e.get("company_id") == target_id]
    return {"disclosures": [{"date": x["date"], "title": x["title"]} for x in d],
            "news": [{"date": e["date"], "event": e["event"], "outlets": e["outlets"][:2]} for e in n]}


def build_evidence(target_name: str, res: dict, discs: list[dict], news_events: list[dict], target_id: int | None = None) -> eng.Evidence:
    ev = eng.Evidence()
    s, w = res["summary"], res["week"]
    period = f"{w['start']}~{w['end']} ({w['trading_days']}거래일, 기준 {w['base_date']} 종가)"
    s["eids"] = {
        "target": ev.add("calc", f"{target_name} 주간 수익률", s["target_return"], "ratio", pct(s["target_return"]), period=period,
                         method="해당 주 일별 수익률 복리 합산(분할·병합 보정)", source="KRX 일별매매정보"),
        "peers": ev.add("calc", "비교기업 평균 주간 수익률", s["peer_return"], "ratio", pct(s["peer_return"]), period=period,
                        method="비교기업 일별 수익률의 가중 평균(비교 적합도 반영)", source="KRX 일별매매정보"),
        "market": ev.add("calc", f"시장({res['model']['market_label'].split(' ')[0]}) 주간 수익률", s["market_return"], "ratio",
                         pct(s["market_return"]), period=period, method="KRX 종가 기준 시장 평균", source="KRX 일별매매정보"),
        "excess": ev.add("calc", f"{target_name} 비교기업 대비 수익률 차이", s["excess_vs_peers"], "ratio",
                         f"{pct(s['excess_vs_peers'])} ({s['excess_vs_peers'] * 100:+.1f}%p)" if s["excess_vs_peers"] is not None else "N/A",
                         period=period, method="고객사 주간 수익률 − 비교기업 평균 주간 수익률", source="ValueScope"),
        "excess_mkt": ev.add("calc", f"{target_name} 시장 대비 수익률 차이", s["excess_vs_market"], "ratio",
                             f"{s['excess_vs_market'] * 100:+.1f}%p", period=period, method="고객사 주간 수익률 − 시장 대용지수 주간 수익률",
                             source="ValueScope"),
        "pattern": ev.add("calc", f"{target_name} 주간 흐름 판정", None, None, s["pattern_label"], period=period,
                          method="시장·비교기업 흐름을 감안한 주간 판정(ValueScope)", source="ValueScope(KRX 시세)"),
        "comove": ev.add("calc", "비교기업 평균과 같은 방향으로 움직인 날", s["same_dir_days"], "일",
                         f"{s['judged_days']}거래일 중 {s['same_dir_days']}일 같은 방향", period=period,
                         method="일별 등락 방향 비교(고객사 vs 비교기업 평균)", source="ValueScope"),
    }
    ctx = res.get("context") or {}
    if ctx.get("target_pre") is not None:
        span = f"{ctx['pre_start']}~{ctx['pre_end']}"
        s["eids"]["context"] = ev.add(
            "calc", f"{target_name} 직전 4주 수익률 비교", ctx["target_pre"], "ratio",
            (f"직전 4주({span}) {target_name} {pct(ctx['target_pre'])} · 비교기업 평균 {pct(ctx.get('peer_pre'))} · 시장 {pct(ctx.get('market_pre'))}"),
            period=span, method="KRX 종가 기준 누적 수익률(비교기업은 비교 적합도 가중 평균)", source="KRX 일별매매정보",
            detail={k: ctx.get(k) for k in ("target_pre", "peer_pre", "market_pre", "target_5w", "peer_5w", "market_5w")})
        if ctx.get("target_5w") is not None:
            s["eids"]["context_5w"] = ev.add(
                "calc", f"{target_name} 5주 누적 수익률 비교", ctx["target_5w"], "ratio",
                f"5주 누적({ctx['pre_start']}~{w['end']}) {target_name} {pct(ctx['target_5w'])} · 비교기업 평균 {pct(ctx.get('peer_5w'))} · 시장 {pct(ctx.get('market_5w'))}",
                period=f"{ctx['pre_start']}~{w['end']}", method="직전 4주 × 이번 주 누적", source="KRX 일별매매정보")
        for pp_ in ctx.get("peers", []):
            ev.add("calc", f"{pp_['name']} 직전 4주·이번 주 수익률", pp_["pre_4w"], "ratio",
                   f"{pp_['name']} 직전 4주 {pct(pp_['pre_4w'])} · 이번 주 {pct(pp_.get('week'))} · 5주 누적 {pct(pp_.get('five_week'))}",
                   period=span, source="KRX 일별매매정보", detail={"week": pp_.get("week"), "five_week": pp_.get("five_week")})
    prior = res.get("prior_disclosures") or []
    if prior:
        s["eids"]["prior"] = ev.add("fact", f"{target_name} 직전 4주 공시", len(prior), "건",
                                    f"직전 4주 공시 {len(prior)}건", method="; ".join(f"{x['date']} {x['title']}" for x in prior),
                                    source="OpenDART", detail={"items": prior})
    ti = target_issues(target_id, discs, news_events)
    res["target_issues"] = ti
    if ti["disclosures"] or ti["news"]:
        parts = [f"{x['date'][5:].replace('-', '/')} 공시 '{x['title']}'" for x in ti["disclosures"]]
        parts += [f"{x['date'][5:].replace('-', '/') if x['date'] else ''} 보도 '{x['event']}'" for x in ti["news"]]
        disp = f"공시 {len(ti['disclosures'])}건 · 보도 {len(ti['news'])}건: " + "; ".join(parts)
    else:
        disp = "이번 주 회사 관련 공시·보도 없음"
    s["eids"]["issues"] = ev.add("fact", f"{target_name} 이번 주 공시·보도", len(ti["disclosures"]) + len(ti["news"]), "건", disp[:100],
                                 period=period, method=disp, source="OpenDART, 언론 보도")
    if w.get("base_close") and w.get("last_close"):
        s["eids"]["price"] = ev.add("fact", f"{target_name} 주간 종가 변화", w["last_close"], "KRW/주",
                                    f"{w['base_close']:,.0f}원({w['base_date']}) → {w['last_close']:,.0f}원", period=period,
                                    source="KRX 일별매매정보", detail={"base_close": w["base_close"], "last_close": w["last_close"]})
    v = res.get("volatility") or {}
    s["eids"]["volatility"] = ev.add(
        "calc", f"{target_name} 등락폭·거래량(평소 대비)", v.get("std_ratio"), "x",
        (f"하루 등락폭 평소의 {v['std_ratio']:.1f}배 · 장중 고저 폭 {pa(v.get('range_week'))}(평소 {pa(v.get('range_60d'))}, {(v.get('range_ratio') or 0):.1f}배)"
         f" · 거래량 평소의 {(v.get('volume_ratio') or 0):.1f}배 · {s['volatility_label']}") if v.get("std_ratio") else "산출 불가", period=period,
        method="평소 = 직전 약 3개월(거래량은 직전 1개월) 평균",
        source="KRX 일별매매정보", detail={k: v.get(k) for k in ("ret_std_week", "ret_std_60d", "range_week", "range_60d",
                                                              "volume_week", "volume_20d", "std_ratio", "range_ratio", "volume_ratio")})
    for d in res["days"]:
        d["eid"] = ev.add("calc", f"{target_name} {d['date']}({d['weekday']}) 등락", d["ret"], "ratio",
                          f"{d['date']}({d['weekday']}) {pct(d['ret'])} · 비교기업 평균 {pct(d['peer_avg'])} · 시장 {pct(d['market'])}"
                          + (" · 시장·업종 흐름과 다른 이례적 변동" if d["abnormal"] else "")
                          + (f" · 거래량 평소의 {d['volume_ratio']:.1f}배" if d.get("volume_ratio") is not None else ""),
                          as_of=d["date"], method="KRX 종가 기준 일간 등락률", source="KRX 일별매매정보")
    for p in res["peers"]:
        if p.get("available"):
            p["eid"] = ev.add("calc", f"{p['name']} 주간 수익률", p["week_return"], "ratio",
                              f"{pct(p['week_return'])} (고객사와 같은 방향 {p['same_dir_days']}일)", period=period,
                              method="KRX 종가 기준", source="KRX 일별매매정보")
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


def _kdate(iso: str | None) -> str:
    return f"{int(iso[5:7])}월 {int(iso[8:10])}일" if iso and len(iso) >= 10 else ""


def _clean_title(t: str) -> str:
    """공시 제목을 읽기 쉽게: '[기재정정]단일판매ㆍ공급계약체결' → '단일판매·공급계약 체결(정정)'."""
    corr = "정정" in t[:8]
    t = t.split("]", 1)[-1] if t.startswith("[") else t
    t = t.replace("ㆍ", "·").replace("공급계약체결", "공급계약 체결").strip()
    return f"{t}(정정)" if corr else t


def issue_sentence(ti: dict) -> str:
    """주간 요약에 넣는 '이번 주 회사 관련 공시·보도' 문장 (없으면 없다고). 조사는 항상 '공시/보도' 뒤에 붙인다."""
    d, n = ti.get("disclosures") or [], ti.get("news") or []
    if not d and not n:
        return "이번 주 회사 관련 공시나 언론 보도는 없었습니다."

    def listing(items, fmt, noun):
        body = ", ".join(fmt(x) for x in items[:3])
        return f"{body} {noun}가 있었" if len(items) == 1 else f"{body}{' 등' if len(items) > 3 else ''} {noun} {len(items)}건이 있었"

    dp = listing(d, lambda x: f"{_kdate(x['date'])} '{_clean_title(x['title'])}'", "공시") if d else "공시는 없었"
    np_ = listing(n, lambda x: f"{_kdate(x['date'])} '{x['event']}'", "보도") if n else "언론 보도는 없었"
    return f"이번 주 회사 관련으로는 {dp}고, {np_}습니다."


def rule_based(target_name: str, res: dict) -> dict:
    s, ids = res["summary"], res["summary"]["eids"]
    paras = {
        "overview": [{"text": f"{target_name} 주가는 이번 주 {pct(s['target_return'])} 움직였다. 같은 기간 비교기업 평균은 {pct(s['peer_return'])}, "
                              f"시장은 {pct(s['market_return'])}였다. 이번 주 흐름은 '{s['pattern_label']}'로 정리된다.",
                      "evidence_ids": [ids["target"], ids["peers"], ids["market"], ids["pattern"]]},
                     {"text": issue_sentence(res.get("target_issues") or {}), "evidence_ids": [ids["issues"]]}],
        "comovement": [{"text": f"{s['judged_days']}거래일 중 {s['same_dir_days']}일은 비교기업 평균과 같은 방향으로 움직였다.", "evidence_ids": [ids["comove"]]}],
        "idiosyncratic": [{"text": ("시장·업종 흐름과 다르게 크게 움직인 날: " + ", ".join(s["abnormal_days"])) if s["abnormal_days"]
                           else "시장·업종 흐름과 크게 다르게 움직인 날은 없었다.", "evidence_ids": [ids["pattern"]]}],
        "volatility": [{"text": f"이번 주 변동성은 '{s['volatility_label']}'이었다.", "evidence_ids": [ids["volatility"]]}],
        "drivers": ([{"text": (f"직전 4주 동안 {target_name}은(는) {pct(res['context']['target_pre'])}, 비교기업 평균은 {pct(res['context'].get('peer_pre'))}였다. "
                               f"5주 누적으로는 {target_name} {pct(res['context'].get('target_5w'))}, 비교기업 평균 {pct(res['context'].get('peer_5w'))}이다."),
                      "evidence_ids": [ids["context"]] + ([ids["context_5w"]] if ids.get("context_5w") else [])}]
                    if ids.get("context") else []),
    }
    return {"headline": f"{target_name} 주간 {pct(s['target_return'])} — {s['pattern_label']}",
            "sections": [{"key": k, "paragraphs": v} for k, v in paras.items()]}


def write_report(ai: AIProvider, target_name: str, res: dict, ev: eng.Evidence) -> dict:
    errors, status, model = [], "generated", None
    sm = res["summary"]
    payload = {"target": target_name, "week": res["week"],
               "summary": {k: sm.get(k) for k in ("target_return", "peer_return", "market_return", "excess_vs_peers", "excess_vs_market",
                                                  "pattern_label", "same_dir_days", "judged_days", "abnormal_days", "volatility_label")},
               "days": [{"date": d["date"], "weekday": d["weekday"], "return": d["ret"], "peer_avg": d.get("peer_avg"), "market": d["market"],
                         "unusual_move": d["abnormal"], "volume_vs_usual": d.get("volume_ratio"), "eid": d.get("eid")} for d in res["days"]],
               "peers": [{k: p.get(k) for k in ("name", "week_return", "same_dir_days", "eid")} for p in res["peers"]],
               "target_issues": {**(res.get("target_issues") or {}), "eid": sm.get("eids", {}).get("issues")},
               "context": {**{k: (res.get("context") or {}).get(k) for k in ("pre_start", "pre_end", "target_pre", "peer_pre", "market_pre",
                                                                             "target_5w", "peer_5w", "market_5w", "diverged_days")},
                           "eid": sm.get("eids", {}).get("context"), "eid_5w": sm.get("eids", {}).get("context_5w")},
               "prior_disclosures": {"items": res.get("prior_disclosures") or [], "eid": sm.get("eids", {}).get("prior")}}
    try:
        r = ai.complete_json(WRITER_SYSTEM,
                             "## 엔진 결과\n" + json.dumps(payload, ensure_ascii=False, default=str)
                             + "\n\n## 근거(evidence)\n" + json.dumps([{k: e[k] for k in ("eid", "kind", "label", "display", "as_of", "method", "source")}
                                                                    for e in ev.items], ensure_ascii=False)
                             + "\n\n## 작성 지시\n섹션: " + ", ".join(f"{k}={t}" for k, t in SECTIONS)
                             + "\n각 섹션 1~2개 문단, 문단당 2~3문장으로 간결하게. headline은 이번 주 흐름을 한 문장으로(판정 포함, 수치는 수익률·차이만).",
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
            p = {**p, "text": strip_hedges(p.get("text", ""))}
            if not p["text"]:
                continue
            issues = []
            if not [x for x in p.get("evidence_ids", []) if x in ids]:
                issues.append("유효한 근거 ID 없음")
            if INVEST_ADVICE.search(p.get("text", "")):
                issues.append(f"투자 권유 표현: {INVEST_ADVICE.search(p['text']).group(0)}")
            bad = check_numbers(p.get("text", ""), values)
            if bad:
                issues.append("근거와 다른 수치: " + ", ".join(bad[:4]))
            if JARGON.search(p.get("text", "")):
                issues.append(f"보고서 표현 규칙 위반(통계 용어): {JARGON.search(p['text']).group(0)}")
            item = {"ref": f"{sec.get('key')}.{i + 1}", "text": p.get("text", ""), "evidence_ids": [x for x in p.get("evidence_ids", []) if x in ids]}
            if issues:
                held.append({**item, "section": titles.get(sec.get("key"), sec.get("key")), "issues": issues})
            else:
                paras.append(item)
        title = drivers_title(res) if sec.get("key") == "drivers" else titles.get(sec.get("key"), sec.get("key"))
        out.append({"key": sec.get("key"), "title": title, "paragraphs": paras})
    # 주간 요약에는 회사 관련 공시·보도 유무가 반드시 들어가야 한다 — 빠졌으면 근거 기반 문장을 덧붙인다
    ov = next((x for x in out if x["key"] == "overview"), None)
    issue_eid = res["summary"]["eids"].get("issues")
    if issue_eid:
        mentions = ov and any(issue_eid in x["evidence_ids"] or "공시" in x["text"] or "보도" in x["text"] for x in ov["paragraphs"])
        if not mentions:
            fallback = rule_based(target_name, res)["sections"][0]["paragraphs"][-1]
            item = {"ref": f"overview.{len(ov['paragraphs']) + 1 if ov else 1}", **fallback}
            if ov:
                ov["paragraphs"].append(item)
            else:
                out.append({"key": "overview", "title": titles["overview"], "paragraphs": [item]})
    headline = strip_hedges(content.get("headline") or "")
    if not headline or check_numbers(headline, values) or INVEST_ADVICE.search(headline) or JARGON.search(headline):
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
        prior_discs = wk.prior_disclosures(db, target, mon)

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
            # 비교기업과 다르게 움직인 주에는 '왜' 그랬는지에 초점을 둔 검색을 하나 더 한다(주 시작 3일 전 ~ 주말)
            pre = wk.quick_gap(db, target, peers, mon, sun)
            if pre is not None and abs(pre) >= wk.GAP_NOTE:
                direction = "약세(하락)" if pre < 0 else "강세(상승)"
                specs.append(news_svc.SearchSpec(
                    f"week-why:{target.id}:{mon}", f"{target.name} {direction} 배경",
                    f"'{target.name}'(종목코드 {target.stock_code}) 주가가 {mon}~{sun} 주간에 업종·시장보다 {direction}을 보인 이유를 다룬 기사를 웹검색으로 찾으십시오. "
                    "수급(외국인·기관 매매, 차익실현, 공매도, 대주주·임원 지분 변동, 전환사채 물량), 실적 전망, 수주, 업황, 증권가 의견 등 주가 흐름의 배경을 설명한 기사 위주.",
                    months=1, max_items=8, company_id=target.id, role="target", window=((mon - timedelta(days=3)).isoformat(), sun.isoformat())))
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
        res["prior_disclosures"] = prior_discs
        res["news"] = [{"date": e["date"], "date_certain": e["date_certain"], "company_id": e.get("company_id"),
                        "company": next((c.name for c in companies if c.id == e.get("company_id")), e.get("about") or ""),
                        "event": e["event"], "url": e["articles"][0]["url"], "outlets": e["outlets"][:4], "articles": len(e["articles"]),
                        "sentiment": e["sentiment"], "verification": e.get("verification"), "summary": e.get("summary")} for e in news_events]
        res["news_enabled"] = news_searcher is not None and w.options.get("include_news", True)
        res["peer_list"] = [{"company_id": p.company.id, "name": p.company.name, "weight": p.weight} for p in peers]

        # 5) 해석
        _stage(db, w, "AI 해석 작성")
        ev = build_evidence(target.name, res, discs, news_events, target_id=target.id)
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
