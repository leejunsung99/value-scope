"""주간 리포트 문체: 모든 문장 '~다'체, 공시 미확인 단서 문장 제거."""
from app.services.weekly_run import issue_sentence, strip_hedges, to_plain_style


def test_plain_style_conversion():
    assert to_plain_style("주가는 +28.3% 올랐습니다. 판정은 '동반 상승'입니다.") == "주가는 +28.3% 올랐다. 판정은 '동반 상승'이다."
    assert to_plain_style("주가가 오릅니다. 변동성이 큽니다. 흐름이 이어집니다.") == "주가가 오른다. 변동성이 크다. 흐름이 이어진다."
    assert to_plain_style("수요가 늘 것으로 보입니다.") == "수요가 늘 것으로 보인다."
    assert to_plain_style("이미 '다'체로 썼다.") == "이미 '다'체로 썼다."


def test_disclaimer_sentence_is_removed():
    t = ("13일 보도는 증착장비 수주 기대를 원인으로 들었다. 다만 이는 기사 해석에 근거한 것으로, 회사 공시로 확인된 내용은 아니다. "
         "XR 기기 시장 확대 기대도 기사에서 언급됐다.")
    assert strip_hedges(t) == "13일 보도는 증착장비 수주 기대를 원인으로 들었다. XR 기기 시장 확대 기대도 기사에서 언급됐다."


def test_issue_sentence_is_plain_style():
    assert issue_sentence({}).endswith("없었다.")
    s = issue_sentence({"disclosures": [{"date": "2026-11-09", "title": "단일판매ㆍ공급계약체결"}], "news": []})
    assert s.endswith("다.") and "습니다" not in s
