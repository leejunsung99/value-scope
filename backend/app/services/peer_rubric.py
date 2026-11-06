"""비교 적합성(Comparability) 점수 루브릭.

점수는 '투자매력도'가 아니라 '고객사와의 비교 적합성'이다.
각 항목 level: 1.0(높음) / 0.5(부분적) / 0.0(낮음·해당없음) / None(근거 부족으로 미평가).
미평가 항목은 0점 처리하지 않고 분모에서 제외하며, 평가 커버리지를 함께 표시한다.
"""
from __future__ import annotations

from dataclasses import dataclass

RUBRIC_VERSION = "comparability-v1"


@dataclass(frozen=True)
class Criterion:
    key: str
    label: str
    weight: int
    description: str


CRITERIA: list[Criterion] = [
    Criterion("product", "제품·서비스 유사성", 30, "동일 또는 유사한 제품·장비를 생산·판매하는가"),
    Criterion("process", "생산공정·장비 연관성", 20, "동일 공정(예: OLED 증착/봉지/검사) 또는 인접 공정에 속하는가"),
    Criterion("customer", "고객·전방산업 중복", 15, "주요 고객사·전방산업이 겹치는가"),
    Criterion("revenue", "매출구조 유사성", 15, "수주형/소모품형 등 매출 인식 구조와 사업 비중이 유사한가"),
    Criterion("cycle", "설비투자 사이클 연동", 10, "동일한 고객 CAPEX 사이클의 영향을 받는가"),
    Criterion("data", "상장·공시 데이터 가용성", 10, "국내 상장 주가와 DART 정기공시를 이용할 수 있는가"),
]
CRITERIA_BY_KEY = {c.key: c for c in CRITERIA}
LEVEL_LABELS = {1.0: "높음", 0.5: "부분적", 0.0: "낮음/해당없음"}
MIN_COVERAGE_FOR_SCORE = 0.5


def data_availability(market: str, is_listed: bool | None, is_verified: bool) -> dict:
    """객관 항목: DART 검증 결과로 자동 판정."""
    if not is_verified:
        return {"level": None, "rationale": "DART 등 공식자료로 상장·공시 여부 미확인", "origin": "rule"}
    if is_listed and market in ("KOSPI", "KOSDAQ"):
        return {"level": 1.0, "rationale": f"{market} 상장, DART 정기공시 이용 가능", "origin": "rule"}
    if is_listed:
        return {"level": 0.5, "rationale": f"{market} 상장(유동성·공시 범위 제한 가능)", "origin": "rule"}
    return {"level": 0.5 if market == "UNLISTED" else 0.0,
            "rationale": "DART 공시는 있으나 상장 주가 없음" if market == "UNLISTED" else "상장 주가·국내 공시 없음",
            "origin": "rule"}


def compute_score(breakdown: list[dict]) -> tuple[float | None, float]:
    """Returns (score 0-100 or None if coverage too low, coverage 0-1)."""
    total = assessed = earned = 0.0
    for c in CRITERIA:
        total += c.weight
        item = next((b for b in breakdown if b.get("key") == c.key), None)
        if item is None or item.get("level") is None:
            continue
        assessed += c.weight
        earned += c.weight * float(item["level"])
    coverage = assessed / total if total else 0.0
    if assessed == 0 or coverage < MIN_COVERAGE_FOR_SCORE:
        return None, round(coverage, 3)
    return round(earned / assessed * 100, 1), round(coverage, 3)


def build_breakdown(assessments: dict[str, dict]) -> list[dict]:
    """assessments: key -> {level, rationale, origin, evidence?}. Missing keys -> 미평가."""
    out = []
    for c in CRITERIA:
        a = assessments.get(c.key) or {}
        level = a.get("level")
        out.append(
            {
                "key": c.key,
                "label": c.label,
                "weight": c.weight,
                "level": level,
                "level_label": LEVEL_LABELS.get(level, "미평가") if level is not None else "미평가",
                "rationale": a.get("rationale") or "근거 부족으로 미평가",
                "origin": a.get("origin", "none"),
                "evidence": a.get("evidence"),
            }
        )
    return out


def rubric_definition() -> dict:
    return {
        "version": RUBRIC_VERSION,
        "min_coverage": MIN_COVERAGE_FOR_SCORE,
        "levels": [{"level": k, "label": v} for k, v in LEVEL_LABELS.items()],
        "criteria": [c.__dict__ for c in CRITERIA],
        "note": "비교 적합성 점수는 투자매력도가 아니라 고객사와의 사업 유사성·비교 가능성을 뜻합니다. "
        "근거가 없는 항목은 '미평가'로 분모에서 제외되며, 평가 커버리지가 50% 미만이면 점수를 산출하지 않습니다.",
    }


DEFAULT_USE_TYPE = {"A": "valuation", "B": "valuation", "C": "industry_reference", "D": "industry_reference", "E": "industry_reference"}


def default_use_type(category: str, is_listed: bool | None) -> str:
    if not is_listed:
        return "tech_competition" if category == "A" else "industry_reference"
    return DEFAULT_USE_TYPE.get(category, "industry_reference")
