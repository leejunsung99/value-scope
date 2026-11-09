from types import SimpleNamespace

import pytest

from app.services import peer_discovery as disc


@pytest.mark.parametrize("name,key", [
    ("Chengdu BOE Display Technology Co.,Ltd", "boe"),
    ("Beijing BOE CHUANGYUAN Technology Co., Ltd", "boe"),
    ("삼성디스플레이 주식회사", "삼성디스플레이"),
    ("(주)삼성디스플레이", "삼성디스플레이"),
    ("엘지디스플레이 주식회사", "엘지디스플레이"),
    ("LG디스플레이", "엘지디스플레이"),
    ("SAMSUNG SDI WUXI CO.,LTD", "samsung sdi"),
    ("CHINA STAR OPTOELECTRONICS TECHNOLOGY CO., LTD", "star"),
    (None, None),
    ("Co., Ltd.", None),
])
def test_customer_key(name, key):
    assert disc.customer_key(name) == key


def test_customer_key_does_not_merge_conglomerate_affiliates():
    assert disc.customer_key("삼성전자 주식회사") != disc.customer_key("삼성디스플레이 주식회사")
    assert disc.customer_key("Samsung Electronics Co., Ltd.") != disc.customer_key("SAMSUNG SDI WUXI CO.,LTD")


def _reg(code, name, stock, induty):
    return SimpleNamespace(corp_code=code, corp_name=name, stock_code=stock, induty_code=induty)


def test_industry_candidates_prefer_peer_subindustry_then_size():
    target = SimpleNamespace(corp_code="T", induty_code="292")
    universe = [
        _reg("A", "디스플레이장비큰", "000001", "29272"),
        _reg("B", "디스플레이장비비슷", "000002", "29272"),
        _reg("C", "반도체장비", "000003", "29271"),
        _reg("D", "무관업종", "000004", "26429"),
        _reg("E", "이미후보", "000005", "29272"),
    ]
    mcaps = {"000001": 100e12, "000002": 0.6e12, "000003": 0.5e12}
    out = disc.industry_candidates(target, {"야스": "29272"}, universe, mcaps, 0.5e12, exclude={"E"})
    assert [o["corp_code"] for o in out] == ["B", "A", "C"]
    assert out[0]["level"] == 2 and "야스" in out[0]["basis"]
    assert out[2]["level"] == 1


def test_industry_candidates_need_target_code():
    target = SimpleNamespace(corp_code="T", induty_code=None)
    assert disc.industry_candidates(target, {}, [_reg("A", "x", "1", "292")], {}, None, set()) == []


class FakeMonitor:
    enabled = True

    def contract_counterparties(self):
        return [
            {"corp_code": "P1", "stock_code": "111111", "corp_name": "증착장비사", "rcept_no": "20261020000001", "rcept_date": "20261020",
             "report_name": "단일판매ㆍ공급계약체결", "counterparty": "Chengdu BOE Display Technology Co.,Ltd.", "description": "OLED 장비"},
            {"corp_code": "P1", "stock_code": "111111", "corp_name": "증착장비사", "rcept_no": "20261021000001", "rcept_date": "20261021",
             "report_name": "단일판매ㆍ공급계약체결", "counterparty": "삼성디스플레이(주)", "description": "OLED 장비"},
            {"corp_code": "P2", "stock_code": "222222", "corp_name": "건설사", "rcept_no": "20261022000001", "rcept_date": "20261022",
             "report_name": "단일판매ㆍ공급계약체결", "counterparty": "삼성전자 주식회사", "description": "공장 건설"},
            {"corp_code": "T", "stock_code": "171090", "corp_name": "고객사", "rcept_no": "20261023000001", "rcept_date": "20261023",
             "report_name": "단일판매ㆍ공급계약체결", "counterparty": "BOE", "description": "self"},
        ]

    def contracts_since(self):
        return "20261013"


def test_customer_overlap_matches_same_counterparty(db):
    from app.models import Company
    from app.models_analysis import ContractOrder

    t = Company(company_key="dart:T", corp_code="T", name="고객사", stock_code="171090", is_listed=True)
    db.add(t)
    db.flush()
    for i, cp in enumerate(["Beijing BOE CHUANGYUAN Technology Co., Ltd", "삼성디스플레이 주식회사"]):
        db.add(ContractOrder(company_id=t.id, rcept_no=f"2026010100000{i}", report_nm="단일판매ㆍ공급계약체결", filed_date="20260101",
                             contract_key=f"k{i}", counterparty=cp, source="DART", url="u"))
    db.flush()
    out, meta = disc.customer_overlap_candidates(db, FakeMonitor(), t, exclude=set())
    assert [o["corp_code"] for o in out] == ["P1"]
    assert len(out[0]["customers"]) == 2 and len(out[0]["refs"]) == 2
    assert out[0]["refs"][0]["url"].endswith("20261020000001")
    assert meta["monitor_since"] == "20261013"
