"""Guards added after the first real-data run (synthetic inputs)."""
import pytest

from app.services import engine as eng
from app.services.report import INVEST_ADVICE, known_values


def test_peer_outlier_is_trimmed_not_whole_multiple():
    ev = eng.Evidence()
    t = eng.CompanyMetrics(1, "T", values={"ebit_norm": 100.0, "ni_core_norm": 80.0, "net_debt": 0.0,
                                           "minority_interest": 0.0, "shares_out": 10.0})
    # EV/정상EBIT 2.8 / 3.1 / 18.0: 18.0 is > 2.5x the median 3.1 -> trimmed -> only 2 left -> multiple unavailable
    # 정상 P/E 4 / 7 / 11: all within 2.5x of the median -> used
    peers = [eng.CompanyMetrics(i, f"P{i}", values={"ev_ebit_norm": a, "pe_norm": b})
             for i, (a, b) in enumerate([(2.8, 4.0), (3.1, 7.0), (18.0, 11.0)], start=2)]
    r = eng.method_peer(t, peers, ev)
    assert [u["multiple"] for u in r["inputs"]] == ["정상 P/E"]
    assert any("이상치 제외" in n and "P4 18.0" in n for n in r["notes"])
    assert r["scenarios"]["base"]["per_share"] == pytest.approx(7.0 * 80 / 10)  # no quality data -> median


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


def test_small_sample_outlier_trim_keeps_comparable_peers():
    ev = eng.Evidence()
    t = eng.CompanyMetrics(1, "T", values={"ebit_norm": 100.0, "ni_core_norm": 80.0, "net_debt": 0.0,
                                           "minority_interest": 0.0, "shares_out": 10.0})
    # 2.6 / 16.8 / 18.7 / 35.9: median 17.75 -> 2.6 trimmed (< 17.75/2.5); remaining max/min 2.1x -> reliable
    peers = [eng.CompanyMetrics(i, f"P{i}", values={"ev_ebit_norm": a, "pe_norm": b})
             for i, (a, b) in enumerate([(2.6, 10.0), (16.8, 12.0), (18.7, 13.0), (35.9, 14.0)], start=2)]
    r = eng.method_peer(t, peers, ev)
    ev_in = next(u for u in r["inputs"] if u["multiple"] == "EV/정상EBIT")
    assert [p["name"] for p in ev_in["dropped"]] == ["P2"] and ev_in["reliable"]
    assert {p["name"] for p in ev_in["peers"]} == {"P3", "P4", "P5"}


def test_peer_weights_and_quality_percentile_pick_the_multiple():
    ev = eng.Evidence()
    # target has the best margin/ROE/growth among peers -> percentile 1.0 -> clamped to 75%
    t = eng.CompanyMetrics(1, "T", values={"ebit_norm": 100.0, "net_debt": 0.0, "minority_interest": 0.0, "shares_out": 10.0,
                                           "margin_norm": 0.30, "roe_norm": 0.25, "revenue_growth": 0.5})
    peers = []
    for i, (m, w) in enumerate([(8.0, 1.0), (10.0, 1.0), (12.0, 1.0), (14.0, 0.25)], start=2):
        p = eng.CompanyMetrics(i, f"P{i}", values={"ev_ebit_norm": m, "margin_norm": 0.1, "roe_norm": 0.1, "revenue_growth": 0.1})
        p.weight = w
        peers.append(p)
    r = eng.method_peer(t, peers, ev)
    assert r["quality"]["percentile"] == pytest.approx(1.0) and r["quality_percentile_used"] == 0.75
    q = r["inputs"][0]["quantiles"]
    # scenarios centred on the quality percentile: 50% / 75% / 100% weighted quantiles
    assert q["conservative"] < q["base"] < q["optimistic"]
    assert q["optimistic"] == pytest.approx(14.0) and q["base"] < 14.0  # low-weight 14x peer only reached at the top


def test_pe_and_pb_over_roe_are_not_double_counted():
    ev = eng.Evidence()
    t = eng.CompanyMetrics(1, "T", values={"ebit_norm": 100.0, "ni_core_norm": 80.0, "net_debt": 0.0, "minority_interest": 0.0,
                                           "shares_out": 10.0, "equity_owners": 500.0, "roe_norm": 0.16})
    peers = [eng.CompanyMetrics(i, f"P{i}", values={"ev_ebit_norm": x, "pe_norm": x}) for i, x in enumerate([5.0, 6.0, 7.0], start=2)]
    r = eng.method_peer(t, peers, ev)
    assert [u["multiple"] for u in r["inputs"]] == ["EV/정상EBIT", "정상 P/E"]


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


def _basis_case(ttm_ebit):
    t = eng.CompanyMetrics(1, "T", values={"ebit_norm": 100.0, "ni_core_norm": 80.0, "operating_income_ttm": ttm_ebit,
                                           "core_ni_ttm": ttm_ebit * 0.8, "norm_quarters": 20, "net_debt": 0.0,
                                           "minority_interest": 0.0, "shares_out": 10.0})
    peers = [eng.CompanyMetrics(i, f"P{i}", values={"ev_ebit_norm": x * 1.2, "pe_norm": x * 1.5, "ev_ebit": x, "per_adj": x * 1.3})
             for i, x in enumerate([8.0, 10.0, 12.0], start=2)]
    return t, peers


def test_peer_basis_switches_to_ttm_when_business_scaled_up():
    ev = eng.Evidence()
    t, peers = _basis_case(300.0)  # TTM 3x the 5y average
    r = eng.method_peer(t, peers, ev)
    assert r["basis"] == "ttm" and r["scale_ratio"] == pytest.approx(3.0)
    assert [u["multiple"] for u in r["inputs"]] == ["EV/EBIT(TTM)", "조정 P/E(TTM)"]
    assert r["scenarios"]["base"]["components"]["EV/EBIT(TTM)"]["per_share"] == pytest.approx(10.0 * 300 / 10)
    assert any("TTM 전환" in n for n in r["notes"])
    # normalized multiples still exposed for the forward method
    assert [u["key"] for u in r["inputs_norm"]] == ["ev_ebit_norm", "pe_norm"]


def test_peer_basis_stays_normalized_for_stable_earnings():
    ev = eng.Evidence()
    t, peers = _basis_case(150.0)
    r = eng.method_peer(t, peers, ev)
    assert r["basis"] == "normalized"
    assert [u["key"] for u in r["inputs"]] == ["ev_ebit_norm", "pe_norm"]


def test_unverified_ai_summary_is_replaced_by_engine_summary():
    from app.services.ai import AIProvider, AIResult
    from app.services.report import SECTIONS, generate_report

    ev = eng.Evidence()
    e1 = ev.add("fact", "T 종가", 63000, "KRW/주", "63,000원")

    class FakeAI(AIProvider):
        name = "fake"

        def complete_json(self, system, prompt, schema):
            if "Review Agent" in system:
                return AIResult(data={"issues": [], "counter_evidence_reviewed": True, "overall_comment": ""}, provider="fake", model="f")
            secs = [{"key": k, "paragraphs": [{"text": "현재가 63,000원입니다.", "evidence_ids": [e1], "claim_type": "fact"}]} for k, _ in SECTIONS]
            return AIResult(data={"sections": secs, "final_verdict": "적정",
                                  "summary": "전환사채 손실 388억원이 부담이라 매수는 신중해야 합니다."}, provider="fake", model="f")

    payload = {"name": "T", "verdict": "적정", "price": 63000.0, "confidence": 70.0,
               "fair_value": {"conservative": 51671.0, "base": 61071.0, "optimistic": 84775.0}}
    rep = generate_report(FakeAI(), payload, ev.items)
    c = rep["content"]
    assert c["summary_ai_rejected"].startswith("전환사채")
    assert "61,071원" in c["summary"] and "388억원" not in c["summary"] and "매수" not in c["summary"]
    assert any("요약" in e for e in rep["review"]["errors"])
    assert {i["type"] for i in c["summary_issues"]} == {"unsupported", "number_mismatch"}
