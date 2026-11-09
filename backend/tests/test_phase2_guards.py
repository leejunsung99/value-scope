"""Guards added after the first real-data run (synthetic inputs)."""
import pytest

from app.services import engine as eng
from app.services.report import INVEST_ADVICE, known_values


def test_peer_multiple_with_excessive_dispersion_is_excluded():
    ev = eng.Evidence()
    t = eng.CompanyMetrics(1, "T", values={"ebit_norm": 100.0, "ni_core_norm": 80.0, "net_debt": 0.0,
                                           "minority_interest": 0.0, "shares_out": 10.0})
    # EV/정상EBIT 2.8 / 3.1 / 18.0 -> max/min 6.4x > 3 -> excluded; 정상 P/E 4/7/11 -> kept
    peers = [eng.CompanyMetrics(i, f"P{i}", values={"ev_ebit_norm": a, "pe_norm": b})
             for i, (a, b) in enumerate([(2.8, 4.0), (3.1, 7.0), (18.0, 11.0)], start=2)]
    r = eng.method_peer(t, peers, ev)
    assert [u["multiple"] for u in r["inputs"]] == ["정상 P/E"]
    assert any("분산 과대" in n for n in r["notes"])
    assert r["scenarios"]["base"]["per_share"] == pytest.approx(7.0 * 80 / 10)


def test_wide_combined_range_means_hold_judgement():
    ev = eng.Evidence()
    mk = lambda name, lo, b, hi: {"method": name, "label": name, "available": True, "notes": [], "earnings_based": True, "assumptions": {},
                                  "scenarios": {"conservative": {"per_share": lo}, "base": {"per_share": b}, "optimistic": {"per_share": hi}}}
    out = eng.combine([mk("peer", 10, 50, 100), mk("history", 20, 60, 140), mk("forward", 15, 55, 120)],
                      price=55, peer_count=5, earnings_unstable=False, undisclosed=0, ev=ev, target_name="T")
    assert out["range_ratio"] > 3 and out["verdict"] == "판단 유보" and "범위" in out["verdict_reason"]


class _Row:
    def __init__(self, v):
        self.value, self.filed_date = v, "20000101"


class _FS:
    """8 quarters of operating income = 10 each (sum 80)."""
    def __init__(self):
        from app.services.engine_data import prev_quarter
        self.q = {"operating_income": {prev_quarter("2026-06-30", k): _Row(10.0) for k in range(8)}}


def test_forward_keeps_multiple_fixed_across_scenarios():
    ev = eng.Evidence()
    t = eng.CompanyMetrics(1, "T", values={"net_debt": 0.0, "minority_interest": 0.0, "shares_out": 1.0,
                                           "margin_norm": 0.10, "op_margin": 0.20, "latest_quarter": "2026-06-30"})
    peer = {"label": "B", "inputs": [{"key": "ev_ebit_norm", "quantiles": {"conservative": 2.0, "base": 5.0, "optimistic": 20.0}}]}
    sched = {"available": True, "official_backlog": 3000.0, "as_of": "2026-06-30", "rule": "r", "undisclosed_contracts": 0,
             "undisclosed_pool": 0.0, "notes": [],
             "scenarios": {"conservative": {"revenue": 500.0}, "base": {"revenue": 1000.0}, "optimistic": {"revenue": 1500.0}}}
    r = eng.method_forward(t, _FS(), sched, peer, {}, ev)
    assert {r["scenarios"][s]["multiple"] for s in eng.SCENARIOS} == {5.0}
    # fix 1: forward EBIT is put on the same 3-year-average basis as the EV/normalized-EBIT multiple
    assert r["scenarios"]["conservative"]["per_share"] == pytest.approx((80 + 500 * 0.10) / 3 * 5.0)
    assert r["scenarios"]["base"]["per_share"] == pytest.approx((80 + 1000 * 0.10) / 3 * 5.0)
    assert r["scenarios"]["optimistic"]["per_share"] == pytest.approx((80 + 1500 * 0.20) / 3 * 5.0)
    assert r["earnings_based"] is True


def test_small_sample_dispersion_uses_min_max():
    ev = eng.Evidence()
    t = eng.CompanyMetrics(1, "T", values={"ebit_norm": 100.0, "ni_core_norm": 80.0, "net_debt": 0.0,
                                           "minority_interest": 0.0, "shares_out": 10.0})
    # 4 peers 2.6 / 16.8 / 18.7 / 35.9: quartiles 13.3~23.0 (1.7x) would pass, but min/max is 13.8x -> excluded
    peers = [eng.CompanyMetrics(i, f"P{i}", values={"ev_ebit_norm": a, "pe_norm": b})
             for i, (a, b) in enumerate([(2.6, 10.0), (16.8, 12.0), (18.7, 13.0), (35.9, 14.0)], start=2)]
    r = eng.method_peer(t, peers, ev)
    assert [u["multiple"] for u in r["inputs"]] == ["정상 P/E"]
    assert any("최대/최소 13.8배" in n for n in r["notes"])


def test_loss_maker_without_earnings_method_is_held():
    ev = eng.Evidence()
    mk = lambda name, lo, b, hi, eb: {"method": name, "label": name, "available": True, "notes": [], "earnings_based": True, "assumptions": {},
                                      "earnings_based": eb, "fallback": not eb,
                                      "scenarios": {"conservative": {"per_share": lo}, "base": {"per_share": b}, "optimistic": {"per_share": hi}}}
    out = eng.combine([mk("peer", 2800, 3500, 6200, False), mk("history", 2700, 3500, 5300, False)], price=1730, peer_count=4,
                      earnings_unstable=True, undisclosed=0, ev=ev, target_name="T")
    assert out["verdict"] == "판단 유보" and "이익 기반" in out["verdict_reason"]


def test_invest_advice_and_sign_flip():
    assert INVEST_ADVICE.search("보유 유지, 신규 매수는 조정 시")
    assert not INVEST_ADVICE.search("수주잔고가 증가했습니다")
    ev = eng.Evidence()
    ev.add("calc", "순차입금", -858e8, "KRW", "-858억원")
    assert 858e8 in known_values(ev.items)


def test_method_disagreement_means_hold_judgement():
    ev = eng.Evidence()
    mk = lambda name, lo, b, hi: {"method": name, "label": name, "available": True, "notes": [], "earnings_based": True, "assumptions": {},
                                  "scenarios": {"conservative": {"per_share": lo}, "base": {"per_share": b}, "optimistic": {"per_share": hi}}}
    out = eng.combine([mk("peer", 5500, 10300, 15800), mk("history", 72900, 86000, 110600)], price=66400, peer_count=4,
                      earnings_unstable=False, undisclosed=0, ev=ev, target_name="T")
    assert out["method_dispersion"] > 3 and out["verdict"] == "판단 유보" and "불일치" in out["verdict_reason"]
    assert out["envelope"] == {"low": 5500, "high": 110600}
