from pathlib import Path

from app.models import CorpRegistry
from app.services.dart import extract_business_section, parse_corp_code_xml
from app.services.peer_rubric import build_breakdown, compute_score, data_availability
from app.services.peers import find_mentions, verify_quote

REPO = Path(__file__).resolve().parents[2]


def test_parse_corp_code_xml():
    xml = ("<result><list><corp_code>00126380</corp_code><corp_name>테스트</corp_name>"
           "<stock_code> </stock_code><modify_date>20240101</modify_date></list></result>").encode()
    rows = parse_corp_code_xml(xml)
    assert len(rows) == 1 and rows[0].stock_code is None and rows[0].corp_code == "00126380"


def test_extract_business_section():
    m = ("<TITLE>I. 회사의 개요</TITLE><P>a</P><TITLE ATOC=\"Y\">II. 사업의 내용</TITLE>"
         "<P>본문 &amp; 내용</P><TITLE>III. 재무에 관한 사항</TITLE><P>b</P>")
    assert extract_business_section(m) == "본문 & 내용"
    assert extract_business_section("<P>none</P>") is None


def _reg(code, name, stock="000001"):
    return CorpRegistry(corp_code=code, corp_name=name, stock_code=stock)


def test_find_mentions_prefers_longer_names_and_word_boundaries():
    text = "주요 고객은 LG디스플레이이며 야스는 경쟁사이다. 나야스타일은 무관. LG디스플레이 투자."
    reg = [_reg("1", "LG", "000001"), _reg("2", "LG디스플레이", "000002"), _reg("3", "야스", "000003"),
           _reg("4", "비상장", None)]
    found = find_mentions(text, reg, exclude_codes=set())
    assert found["2"]["count"] == 2
    assert "1" not in found  # 'LG' only appears inside 'LG디스플레이'
    assert found["3"]["count"] == 1  # '나야스타일' must not match
    assert "4" not in found  # unlisted ignored


def test_verify_quote():
    src = "당사는  OLED 증착장비를\n제조합니다."
    assert verify_quote("OLED 증착장비를 제조합니다.", src)
    assert not verify_quote("당사는 반도체 장비를 제조합니다.", src)
    assert not verify_quote("짧음", src)


def test_score_ignores_unassessed_and_requires_coverage():
    only_data = build_breakdown({"data": data_availability("KOSDAQ", True, True)})
    score, cov = compute_score(only_data)
    assert score is None and cov == 0.1  # coverage too low -> 보류, never 0 points

    full = build_breakdown({
        "product": {"level": 1.0}, "process": {"level": 0.5}, "customer": {"level": 1.0},
        "data": {"level": 1.0},
    })
    score, cov = compute_score(full)
    # (30*1 + 20*0.5 + 15*1 + 10*1) / (30+20+15+10) = 65/75
    assert cov == 0.75 and score == round(65 / 75 * 100, 1)


def test_data_availability_rules():
    assert data_availability("KOSDAQ", True, True)["level"] == 1.0
    assert data_availability("FOREIGN", False, False)["level"] is None
    assert data_availability("UNLISTED", False, True)["level"] == 0.5


def test_gitignore_and_env_example_safe():
    gi = (REPO / ".gitignore").read_text(encoding="utf-8")
    for pattern in [".env", ".venv/", "node_modules/", "data/", "*.log", "*.dump"]:
        assert pattern in gi
    for line in (REPO / ".env.example").read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.startswith("#"):
            key, _, value = line.partition("=")
            if key.endswith(("KEY", "TOKEN", "PASSWORD")) or key == "DATABASE_URL":
                assert value == "", f"{key} must be empty in .env.example"
