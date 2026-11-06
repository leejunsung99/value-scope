"""Guards added after the first real-data run (synthetic inputs)."""
import pytest

from app.services import engine as eng
from app.services.report import INVEST_ADVICE, known_values


def test_peer_multiple_with_excessive_dispersion_is_excluded():
    ev = eng.Evidence()
    t = eng.CompanyMetrics(1, "T", values={"operating_income_ttm": 100.0, "net_income_owners_ttm": 80.0, "net_debt": 0.0,
                                           "minority_interest": 0.0, "shares_out": 10.0})
    # EV/EBIT 2.8 / 3.1 / 18.0 -> max/min 6.4x > 3 -> excluded; PER 4/7/11 -> kept
    peers = [eng.CompanyMetrics(i, f"P{i}", values={"ev_ebit": a, "per": b})
             for i, (a, b) in enumerate([(2.8, 4.0), (3.1, 7.0), (18.0, 11.0)], start=2)]
    r = eng.method_peer(t, peers, ev)
    assert [u["multiple"] for u in r["inputs"]] == ["PER"]
    assert any("분산 과대" in n for n in r["notes"])
    assert r["scenarios"]["base"]["per_share"] == pytest.approx(7.0 * 80 / 10)


def test_wide_combined_range_means_hold_judgement():
    ev = eng.Evidence()
    mk = lambda name, lo, b, hi: {"method": name, "label": name, "available": True, "notes": [], "assumptions": {},
                                  "scenarios": {"conservative": {"per_share": lo}, "base": {"per_share": b}, "optimistic": {"per_share": hi}}}
    out = eng.combine([mk("peer", 10, 50, 100), mk("history", 20, 60, 140), mk("forward", 15, 55, 120)],
                      price=55, peer_count=5, earnings_unstable=False, undisclosed=0, ev=ev, target_name="T")
    assert out["range_ratio"] > 3 and out["verdict"] == "판단 유보" and "범위" in out["verdict_reason"]


def test_forward_keeps_multiple_fixed_across_scenarios():
    ev = eng.Evidence()

    class FS:
        def annual(self, k):
            return {"revenue": {"2023": 100, "2024": 100, "2025": 100}, "operating_income": {"2023": 5, "2024": 10, "2025": 20}}[k]

    t = eng.CompanyMetrics(1, "T", values={"net_debt": 0.0, "minority_interest": 0.0, "shares_out": 1.0})
    peer = {"label": "B", "inputs": [{"key": "ev_ebit", "quantiles": {"conservative": 2.0, "base": 5.0, "optimistic": 20.0}}]}
    r = eng.method_forward(t, FS(), {"amount": 1000.0, "source_label": "x"}, peer, {}, ev)
    assert {r["scenarios"][s]["multiple"] for s in eng.SCENARIOS} == {5.0}
    assert r["scenarios"]["optimistic"]["per_share"] == pytest.approx(1000 * 0.9 * 0.2 * 5.0)


def test_invest_advice_and_sign_flip():
    assert INVEST_ADVICE.search("보유 유지, 신규 매수는 조정 시")
    assert not INVEST_ADVICE.search("수주잔고가 증가했습니다")
    ev = eng.Evidence()
    ev.add("calc", "순차입금", -858e8, "KRW", "-858억원")
    assert 858e8 in known_values(ev.items)


def test_method_disagreement_means_hold_judgement():
    ev = eng.Evidence()
    mk = lambda name, lo, b, hi: {"method": name, "label": name, "available": True, "notes": [], "assumptions": {},
                                  "scenarios": {"conservative": {"per_share": lo}, "base": {"per_share": b}, "optimistic": {"per_share": hi}}}
    out = eng.combine([mk("peer", 5500, 10300, 15800), mk("history", 72900, 86000, 110600)], price=66400, peer_count=4,
                      earnings_unstable=False, undisclosed=0, ev=ev, target_name="T")
    assert out["method_dispersion"] > 3 and out["verdict"] == "판단 유보" and "불일치" in out["verdict_reason"]
    assert out["envelope"] == {"low": 5500, "high": 110600}
