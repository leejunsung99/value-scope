"""Test fixtures.

All company/disclosure data below is SYNTHETIC test fixture data (not real DART data).
Tests run against TEST_DATABASE_URL if set (PostgreSQL), otherwise in-memory SQLite.
"""
from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from app import api as api_module
from app import db as db_module
from app.db import Base
from app import models_analysis  # noqa: F401  (register PHASE 2 tables)
from app.services.dart import CorpCodeEntry, DartNoData, DartNotConfigured

TARGET_TEXT = (
    "II. 사업의 내용\n1. 사업의 개요\n당사는 OLED 증착장비를 제조하는 기업으로 주요 고객은 LG디스플레이 등 패널 제조사입니다. "
    "경쟁사로는 캐논토키와 야스가 있으며, 테스트전자는 당사의 부품 공급사입니다. "
    "LG디스플레이의 투자계획에 따라 수주가 변동합니다. 힘스는 마스크 검사 장비를 공급합니다.\n"
)

SAMPLE_REGISTRY = [
    CorpCodeEntry("00000001", "선익시스템", None, "171090", "20250101"),
    CorpCodeEntry("00000002", "야스", None, "255440", "20250101"),
    CorpCodeEntry("00000003", "에이피시스템", None, "265520", "20250101"),
    CorpCodeEntry("00000004", "힘스", None, "238490", "20250101"),
    CorpCodeEntry("00000005", "비아트론", None, "141000", "20250101"),
    CorpCodeEntry("00000006", "에스엔유", None, "080000", "20250101"),
    CorpCodeEntry("00000007", "에스에프에이", None, "056190", "20250101"),
    CorpCodeEntry("00000008", "LG디스플레이", None, "034220", "20250101"),
    CorpCodeEntry("00000009", "덕산네오룩스", None, "213420", "20250101"),
    CorpCodeEntry("00000010", "테스트전자", None, "999990", "20250101"),
    CorpCodeEntry("00000011", "LG", None, "003550", "20250101"),
    CorpCodeEntry("00000012", "비상장테스트", None, None, "20250101"),
]


def _markup(text: str) -> str:
    body = text.replace("II. 사업의 내용\n", "")
    return (f"<DOCUMENT><TITLE ATOC=\"Y\">I. 회사의 개요</TITLE><P>개요</P>"
            f"<TITLE ATOC=\"Y\">II. 사업의 내용</TITLE><P>{body}</P>"
            f"<TITLE ATOC=\"Y\">III. 재무에 관한 사항</TITLE><P>재무</P></DOCUMENT>")


class FakeDart:
    """Stands in for DartClient with synthetic fixture data."""

    def __init__(self, configured: bool = True):
        self.configured = configured
        self.calls: list[str] = []

    def _check(self):
        if not self.configured:
            raise DartNotConfigured()

    def corp_codes(self):
        self._check()
        self.calls.append("corp_codes")
        return SAMPLE_REGISTRY

    def company(self, corp_code):
        self._check()
        self.calls.append(f"company:{corp_code}")
        e = next(e for e in SAMPLE_REGISTRY if e.corp_code == corp_code)
        return {"status": "000", "corp_name": e.corp_name, "stock_code": e.stock_code or "",
                "corp_cls": "K" if e.stock_code else "E", "induty_code": f"9{corp_code[-4:]}", "ceo_nm": "테스트", "hm_url": ""}

    def latest_business_report(self, corp_code):
        self._check()
        self.calls.append(f"list:{corp_code}")
        return {"rcept_no": f"2025031{corp_code[-4:]}", "report_nm": "사업보고서 (2024.12)", "rcept_dt": "20250318"}

    def document_markup(self, rcept_no):
        self._check()
        self.calls.append(f"doc:{rcept_no}")
        if rcept_no.endswith("0001"):
            return _markup(TARGET_TEXT)
        return _markup(f"II. 사업의 내용\n1. 사업의 개요\n테스트 후보기업 {rcept_no}의 사업 개요 원문입니다. 디스플레이 장비를 제조합니다.")


@pytest.fixture()
def engine(tmp_path):
    url = os.environ.get("TEST_DATABASE_URL")
    if url:
        eng = create_engine(url)
        Base.metadata.drop_all(eng)
    else:
        eng = create_engine(f"sqlite:///{tmp_path / 'test.db'}", connect_args={"check_same_thread": False, "timeout": 30})
    Base.metadata.create_all(eng)
    db_module.set_engine(eng)
    yield eng
    Base.metadata.drop_all(eng)
    eng.dispose()


@pytest.fixture()
def db(engine):
    gen = db_module.get_session()
    s = next(gen)
    yield s
    gen.close()


@pytest.fixture()
def fake_dart():
    return FakeDart()


@pytest.fixture()
def client(engine, fake_dart, monkeypatch):
    from app.main import app
    from app.services.ai import NoAIProvider

    monkeypatch.setattr(api_module, "make_dart", lambda: fake_dart)
    monkeypatch.setattr(api_module, "make_monitor", lambda: None)
    import app.api_analysis as api_analysis_module

    monkeypatch.setattr(api_analysis_module, "make_news_searcher", lambda: None)
    monkeypatch.setattr(api_module, "make_ai", lambda: NoAIProvider())
    with TestClient(app) as c:
        yield c


__all__ = ["DartNoData", "FakeDart", "TARGET_TEXT"]
