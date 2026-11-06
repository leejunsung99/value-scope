"""근거 기반 AI 분석 보고서 + Review Agent 검증.

1) Writer: 엔진이 만든 근거(E1, E2, …)만 사용해 8개 섹션 작성. 모든 문단은 근거 ID를 인용한다.
2) 결정적 검증(코드): 근거 ID 존재, 본문 숫자 ↔ 근거 값 일치, 최종 판단 ↔ 엔진 판단 일치.
3) Review Agent(AI): 회계기간 불일치·근거 없는 주장·결론-근거 불일치·반대 근거 누락 점검.
검증 실패 문단은 status='unverified'로 표시하며 확정 결과로 게시하지 않는다.
AI를 쓸 수 없으면 엔진 결과로 규칙 기반 보고서를 만든다(같은 근거 연결).
"""
from __future__ import annotations

import json
import re

from app.services.ai import AIError, AIProvider, AIUnavailable

SECTIONS = [
    ("overall", "① 기업 종합 평가"),
    ("positive", "② 긍정적 평가 근거"),
    ("negative", "③ 부정적 평가 근거"),
    ("peers", "④ 동종업계 대비 평가"),
    ("price_move", "⑤ 주가 움직임 분석"),
    ("fair_value", "⑥ 적정주가 산출 근거"),
    ("conclusion", "⑦ 최종 결론"),
    ("triggers", "⑧ 평가 변화 조건"),
]
VERDICTS = ["고평가", "적정", "저평가", "판단 유보"]

WRITER_SYSTEM = """당신은 한국 상장사 기업가치 분석 보고서를 쓰는 애널리스트입니다.
규칙:
- 제공된 근거 목록(evidence)에 있는 사실과 수치만 사용합니다. 근거에 없는 수치·사건·전망을 만들지 마십시오.
- 수치는 근거의 display 값을 그대로 옮겨 쓰고, 새로 계산한 수치를 쓰지 마십시오.
- 모든 문단에 근거 ID(evidence_ids)를 1개 이상 붙이십시오.
- claim_type: 공시·시세로 확인된 사실은 fact, 엔진 추정치 설명은 estimate, 해석·판단은 judgment.
- 최종 판단은 엔진 판단(engine_verdict)과 반드시 같아야 합니다. 바꾸지 마십시오.
- 부정적 근거와 반대 해석도 반드시 다루십시오. 주가 하락을 악재로 단정하지 말고, 공시와 주가의 시간적 근접을 인과로 단정하지 마십시오.
- 매수·매도·보유 등 투자 권유 표현은 쓰지 마십시오.
- 근거 목록에 없는 업황·기술·고객 정보(예: 특정 세대 투자 사이클)를 단정하지 마십시오. 필요하면 '확인되지 않음'이라고 쓰십시오.
- ⑧ 평가 변화 조건에는 가정 조건의 기준 수치를 쓸 수 있으며, 이는 사실이 아닌 조건임을 문장에 밝히십시오.
- 한국어로 작성합니다."""

REVIEW_SYSTEM = """당신은 금융 분석 보고서 검증 담당(Review Agent)입니다. 제공된 근거 목록과 보고서 문단을 대조해 문제를 찾으십시오.
점검 항목: number_mismatch(수치 불일치), period_mismatch(회계기간·기준일 불일치), unsupported(근거 없는 주장),
double_count(수주 중복 집계), calc_error(계산식 오류), conclusion_mismatch(결론과 근거 불일치), missing_counter(반대 근거 미검토).
문제가 없으면 issues를 빈 배열로 두십시오. 사소한 표현 문제는 severity=low, 사실·수치·결론 오류는 high."""


def writer_schema() -> dict:
    para = {"type": "object", "properties": {
        "text": {"type": "string"},
        "evidence_ids": {"type": "array", "items": {"type": "string"}},
        "claim_type": {"type": "string", "enum": ["fact", "estimate", "judgment"]}},
        "required": ["text", "evidence_ids", "claim_type"], "additionalProperties": False}
    sec = {"type": "object", "properties": {
        "key": {"type": "string", "enum": [k for k, _ in SECTIONS]},
        "paragraphs": {"type": "array", "items": para}},
        "required": ["key", "paragraphs"], "additionalProperties": False}
    return {"type": "object", "properties": {
        "sections": {"type": "array", "items": sec},
        "final_verdict": {"type": "string", "enum": VERDICTS},
        "summary": {"type": "string"}},
        "required": ["sections", "final_verdict", "summary"], "additionalProperties": False}


def review_schema() -> dict:
    issue = {"type": "object", "properties": {
        "paragraph_ref": {"type": "string"},
        "type": {"type": "string", "enum": ["number_mismatch", "period_mismatch", "unsupported", "double_count",
                                            "calc_error", "conclusion_mismatch", "missing_counter"]},
        "severity": {"type": "string", "enum": ["high", "low"]},
        "message": {"type": "string"}},
        "required": ["paragraph_ref", "type", "severity", "message"], "additionalProperties": False}
    return {"type": "object", "properties": {
        "issues": {"type": "array", "items": issue},
        "counter_evidence_reviewed": {"type": "boolean"},
        "overall_comment": {"type": "string"}},
        "required": ["issues", "counter_evidence_reviewed", "overall_comment"], "additionalProperties": False}


def compact_evidence(items: list[dict]) -> list[dict]:
    return [{"id": e["eid"], "kind": e["kind"], "label": e["label"], "display": e["display"], "period": e["period"],
             "as_of": e["as_of"], "method": (e["method"] or "")[:300], "source": e["source"], "impact": e["impact"]}
            for e in items]


# ------------------------------------------------------------------ number verification
NUM_RE = re.compile(r"(-?\d[\d,]*(?:\.\d+)?)\s*(조원|억원|억|만원|원|%p|%|배|점)")


def _collect_numbers(obj, out: list[float]) -> None:
    if isinstance(obj, bool):
        return
    if isinstance(obj, (int, float)):
        out.append(float(obj))
    elif isinstance(obj, dict):
        for v in obj.values():
            _collect_numbers(v, out)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            _collect_numbers(v, out)


INVEST_ADVICE = re.compile(r"매수|매도|비중\s*확대|비중\s*축소|보유\s*유지|목표주가|추천|투자\s*의견")


def known_values(items: list[dict]) -> list[float]:
    vals: list[float] = []
    for e in items:
        if e.get("value") is not None:
            vals.append(e["value"])
        _collect_numbers(e.get("detail") or {}, vals)
        for m in NUM_RE.finditer(e.get("display") or ""):
            vals.append(_to_base(m.group(1), m.group(2)))
    return vals + [-v for v in vals]  # 순차입금 ↔ 순현금처럼 부호만 반대인 표현 허용


def _to_base(num: str, unit: str) -> float:
    x = float(num.replace(",", ""))
    return {"조원": x * 1e12, "억원": x * 1e8, "억": x * 1e8, "만원": x * 1e4, "원": x}.get(unit, x)


def check_numbers(text: str, values: list[float]) -> list[str]:
    bad = []
    for m in NUM_RE.finditer(text):
        num, unit = m.group(1), m.group(2)
        x = _to_base(num, unit)
        decimals = len(num.split(".")[1]) if "." in num else 0
        if unit in ("%", "%p"):
            cands = [x / 100, x]
            tol_abs = 0.5 * 10 ** (-decimals)
            ok = any(abs(v * 100 - x) <= tol_abs + 1e-9 or abs(v - x) <= tol_abs + 1e-9 for v in values)
        elif unit in ("배", "점"):
            tol_abs = 0.5 * 10 ** (-decimals)
            ok = any(abs(v - x) <= tol_abs + 1e-9 for v in values)
        else:
            scale = {"조원": 1e12, "억원": 1e8, "억": 1e8, "만원": 1e4}.get(unit, 1)
            tol_abs = 0.5 * scale * 10 ** (-decimals)
            ok = any(abs(v - x) <= max(tol_abs, abs(x) * 0.005) for v in values)
        if not ok:
            bad.append(m.group(0))
    return bad


# ------------------------------------------------------------------ pipeline
def generate_report(ai: AIProvider, payload: dict, evidence: list[dict]) -> dict:
    """payload: engine summary (verdict, scenarios, ...). Returns report dict with per-paragraph review."""
    ev_ids = {e["eid"] for e in evidence}
    values = known_values(evidence)
    content = None
    status, ai_model, errors = "generated", None, []
    try:
        prompt = ("## 엔진 결과 요약\n" + json.dumps(payload, ensure_ascii=False, default=str) +
                  "\n\n## 근거 목록(evidence)\n" + json.dumps(compact_evidence(evidence), ensure_ascii=False) +
                  "\n\n## 작성 지시\n아래 8개 섹션을 모두 작성하십시오: " + ", ".join(f"{k}={t}" for k, t in SECTIONS) +
                  "\n각 섹션 1~4개 문단. ⑧ 평가 변화 조건에는 평가를 상향·하향해야 할 구체적 조건(수주·실적·업황)을 쓰십시오.")
        r = ai.complete_json(WRITER_SYSTEM, prompt, writer_schema())
        content, ai_model = r.data, r.model
    except (AIUnavailable, AIError) as e:
        errors.append(f"AI 보고서 생성 불가: {e}")
        status = "rule_based"
        content = rule_based_report(payload, evidence)

    # --- deterministic review
    titles = dict(SECTIONS)
    sections = []
    order = {k: i for i, (k, _) in enumerate(SECTIONS)}
    for si, sec in enumerate(sorted(content.get("sections", []), key=lambda s: order.get(s.get("key"), 99))):
        paras = []
        for pi, p in enumerate(sec.get("paragraphs", [])):
            ref = f"{sec['key']}.{pi + 1}"
            issues = []
            ids = [i for i in p.get("evidence_ids", []) if i in ev_ids]
            if not ids:
                issues.append({"type": "unsupported", "severity": "high", "message": "유효한 근거 ID가 없음", "by": "rule"})
            missing = [i for i in p.get("evidence_ids", []) if i not in ev_ids]
            if missing:
                issues.append({"type": "unsupported", "severity": "low", "message": f"존재하지 않는 근거 ID: {', '.join(missing)}", "by": "rule"})
            text = p.get("text", "")
            if INVEST_ADVICE.search(text):
                issues.append({"type": "unsupported", "severity": "high",
                               "message": f"투자 권유 표현 금지: '{INVEST_ADVICE.search(text).group(0)}'", "by": "rule"})
            bad = check_numbers(text, values) if sec["key"] != "triggers" else []
            if sec["key"] == "triggers" and check_numbers(text, values):
                issues.append({"type": "unsupported", "severity": "low",
                               "message": "조건 기준 수치(가정) 포함 — 사실이 아닌 평가 변화 조건", "by": "rule"})
            if bad:
                issues.append({"type": "number_mismatch", "severity": "high",
                               "message": f"근거와 일치하지 않는 수치: {', '.join(bad[:5])}", "by": "rule"})
            paras.append({"ref": ref, "text": p.get("text", ""), "evidence_ids": ids, "claim_type": p.get("claim_type", "judgment"),
                          "issues": issues})
        sections.append({"key": sec["key"], "title": titles.get(sec["key"], sec["key"]), "paragraphs": paras})
    have = {s["key"] for s in sections}
    for k, t in SECTIONS:
        if k not in have:
            sections.append({"key": k, "title": t, "paragraphs": [], "missing": True})
            errors.append(f"섹션 누락: {t}")
    verdict_ok = content.get("final_verdict") == payload.get("verdict")
    if not verdict_ok:
        for s in sections:
            if s["key"] == "conclusion":
                for p in s["paragraphs"]:
                    p["issues"].append({"type": "conclusion_mismatch", "severity": "high",
                                        "message": f"보고서 판단({content.get('final_verdict')}) ≠ 엔진 판단({payload.get('verdict')})", "by": "rule"})

    # --- AI review agent
    review_meta = {"by_ai": False}
    if status == "generated":
        try:
            rv = ai.complete_json(REVIEW_SYSTEM,
                                  "## 근거 목록\n" + json.dumps(compact_evidence(evidence), ensure_ascii=False) +
                                  "\n\n## 엔진 판단\n" + json.dumps({"verdict": payload.get("verdict"), "scenarios": payload.get("fair_value")}, ensure_ascii=False, default=str) +
                                  "\n\n## 보고서 문단 (ref: 텍스트 [근거])\n" +
                                  "\n".join(f"{p['ref']}: {p['text']} [{', '.join(p['evidence_ids'])}]" for s in sections for p in s["paragraphs"]),
                                  review_schema())
            review_meta = {"by_ai": True, "model": rv.model, "counter_evidence_reviewed": rv.data.get("counter_evidence_reviewed"),
                           "overall_comment": rv.data.get("overall_comment")}
            by_ref = {p["ref"]: p for s in sections for p in s["paragraphs"]}
            for iss in rv.data.get("issues", []):
                p = by_ref.get(iss.get("paragraph_ref"))
                if p:
                    p["issues"].append({**iss, "by": "review_agent"})
            if not rv.data.get("counter_evidence_reviewed"):
                errors.append("Review Agent: 반대 근거 검토가 충분하지 않다고 판단")
        except (AIUnavailable, AIError) as e:
            errors.append(f"Review Agent 실행 실패: {e} — 결정적 검증만 적용")
    for s in sections:
        for p in s["paragraphs"]:
            p["status"] = "unverified" if any(i["severity"] == "high" for i in p["issues"]) else "verified"
    n_total = sum(len(s["paragraphs"]) for s in sections)
    n_bad = sum(1 for s in sections for p in s["paragraphs"] if p["status"] == "unverified")
    return {
        "status": status, "ai_model": ai_model,
        "content": {"sections": sections, "summary": content.get("summary"), "final_verdict": payload.get("verdict"),
                    "writer_verdict": content.get("final_verdict")},
        "review": {**review_meta, "paragraphs": n_total, "unverified": n_bad, "verdict_consistent": verdict_ok, "errors": errors},
    }


def rule_based_report(payload: dict, evidence: list[dict]) -> dict:
    """AI 없이 엔진 결과만으로 만드는 보고서 (같은 근거 ID 연결, 수치는 근거 display 그대로)."""
    by = {e["eid"]: e for e in evidence}

    def d(eid):
        return by[eid]["display"] if eid in by else "데이터 없음"

    t = payload
    fv = t.get("fair_value_eid")
    paras = {
        "overall": [{"text": f"{t['name']}의 현재 주가는 {d(t.get('price_eid'))}, 시가총액은 {d(t.get('mcap_eid'))}입니다. "
                             f"엔진이 산출한 적정주가 범위는 {d(fv)}이며 판단은 '{t['verdict']}'입니다.",
                     "evidence_ids": [x for x in (t.get("price_eid"), t.get("mcap_eid"), fv) if x], "claim_type": "estimate"}],
        "positive": [{"text": f"최근 TTM 영업이익은 {d(t.get('op_eid'))}, 영업이익률은 {d(t.get('opm_eid'))}입니다.",
                      "evidence_ids": [x for x in (t.get('op_eid'), t.get('opm_eid')) if x], "claim_type": "fact"}],
        "negative": [{"text": f"매출 성장률(TTM YoY)은 {d(t.get('growth_eid'))}입니다. 실적 변동성과 수주 시점에 따라 이익이 크게 달라질 수 있습니다.",
                      "evidence_ids": [x for x in (t.get('growth_eid'),) if x], "claim_type": "fact"}],
        "peers": [{"text": f"동종업계 상대평가 기준 주당가치는 {d(t.get('peer_eid'))}입니다.", "evidence_ids": [x for x in (t.get('peer_eid'),) if x], "claim_type": "estimate"}],
        "price_move": [{"text": "주가 변동은 시장·산업·개별 요인으로 회귀 분해했습니다. 세부 수치는 주가 변동 분석 표를 참고하십시오.",
                        "evidence_ids": [x for x in (t.get('attr_eid'),) if x], "claim_type": "estimate"}],
        "fair_value": [{"text": f"적정주가는 가용 평가방법을 가중 결합해 {d(fv)}로 산출했습니다.", "evidence_ids": [x for x in (fv,) if x], "claim_type": "estimate"}],
        "conclusion": [{"text": f"최종 판단은 '{t['verdict']}'이며 분석 신뢰도는 {d(t.get('confidence_eid'))}입니다.",
                        "evidence_ids": [x for x in (t.get('confidence_eid'), fv) if x], "claim_type": "judgment"}],
        "triggers": [{"text": "신규 수주 공시, 분기 실적의 흑자·적자 전환, 비교기업 밸류에이션 변화가 발생하면 평가를 다시 산출해야 합니다.",
                      "evidence_ids": [x for x in (fv,) if x], "claim_type": "judgment"}],
    }
    return {"sections": [{"key": k, "paragraphs": paras[k]} for k, _ in SECTIONS], "final_verdict": t["verdict"],
            "summary": f"{t['name']} — {t['verdict']} (규칙 기반 보고서)"}
