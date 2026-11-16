"""비교기업 세부 그룹: 판정은 전체 기준, 특이사항이 있을 때만 그룹을 언급한다."""
from app.services import weekly as wk


def _res(peers, target_daily, peer_return):
    days = [{"date": f"2026-11-{9 + i:02d}", "weekday": "월화수목금"[i], "ret": r, "peer_avg": None} for i, r in enumerate(target_daily)]
    return {"summary": {"peer_return": peer_return}, "days": days, "peers": peers}


def peer(name, cat, week, daily):
    return {"name": name, "category": cat, "available": True, "week_return": week, "daily": daily}


def test_no_notes_when_groups_move_alike():
    peers = [peer("a", "A", 0.02, [0.004] * 5), peer("b", "B", 0.03, [0.006] * 5)]
    groups, notes = wk.group_breakdown(_res(peers, [0.005] * 5, 0.025))
    assert [g["label"] for g in groups] == ["직접 경쟁기업", "유관 장비·부품기업"] and notes == []


def test_lagging_group_and_big_day_are_noted():
    peers = [peer("경쟁1", "A", 0.02, [0, 0, 0, 0, 0.02]),
             peer("장비1", "B", 0.25, [0, 0, 0, 0, 0.20]), peer("장비2", "B", 0.15, [0, 0, 0, 0, 0.10])]
    groups, notes = wk.group_breakdown(_res(peers, [0, 0, 0, 0, 0.12], 0.14))
    week = [n for n in notes if n["kind"] == "week"]
    day = [n for n in notes if n["kind"] == "day"]
    assert {n["label"] for n in week} == {"직접 경쟁기업", "유관 장비·부품기업"}
    assert len(day) == 1 and day[0]["date"] == "2026-11-13"
    assert [g["label"] for g in day[0]["groups"]] == ["유관 장비·부품기업"]  # 경쟁기업 +2%는 고객사 +12%의 절반 미만
    assert day[0]["groups"][0]["members"][0]["name"] == "장비1"


def test_single_group_has_no_week_note():
    peers = [peer("a", "B", 0.30, [0.05] * 5)]
    _, notes = wk.group_breakdown(_res(peers, [0.0] * 5, 0.10))
    assert not [n for n in notes if n["kind"] == "week"]
