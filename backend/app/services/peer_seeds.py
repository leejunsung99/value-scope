"""사용자 제공 초기 후보 목록 (고정 결과가 아님).

여기 적힌 종목코드는 '기대값'일 뿐이며, 실행 시 OpenDART 기업목록과 대조해 검증한다.
검증되지 않으면 화면에 '미검증/불일치'로 표시한다.
"""

SEED_CANDIDATES: dict[str, list[dict]] = {
    # 선익시스템 (171090) — OLED 증착장비
    "171090": [
        {"name": "야스", "expected_stock_code": "255440", "category": "A", "hint": "직접 경쟁·기술 관련"},
        {"name": "캐논토키", "country": "JP", "category": "A", "foreign_unlisted": True,
         "hint": "직접 경쟁·기술 관련 (해외·비상장 — 기술 경쟁 분석 전용, 주식가치 비교 제외)"},
        {"name": "AP시스템", "aliases": ["에이피시스템"], "expected_stock_code": "265520", "category": "B", "hint": "유관 장비"},
        {"name": "힘스", "expected_stock_code": "238490", "category": "B", "hint": "유관 장비"},
        {"name": "비아트론", "expected_stock_code": "141000", "category": "B", "hint": "유관 장비"},
        {"name": "에스엔유", "aliases": ["에스엔유프리시젼"], "expected_stock_code": "080000", "category": "B", "hint": "유관 장비"},
        {"name": "에스에프에이", "aliases": ["SFA"], "expected_stock_code": "056190", "category": "B", "hint": "유관 장비"},
        {"name": "LG디스플레이", "expected_stock_code": "034220", "category": "C", "hint": "전방산업"},
        {"name": "덕산네오룩스", "expected_stock_code": "213420", "category": "D", "hint": "소재 관련"},
    ]
}
