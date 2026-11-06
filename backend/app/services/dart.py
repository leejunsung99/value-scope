"""OpenDART API client (https://opendart.fss.or.kr/).

Only official OpenDART endpoints are used. Without DART_API_KEY every call raises
`DartNotConfigured` so callers can report the limitation instead of guessing data.
"""
from __future__ import annotations

import io
import re
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, timedelta
from html import unescape

import httpx

BASE_URL = "https://opendart.fss.or.kr/api"
VIEWER_URL = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}"

STATUS_MESSAGES = {
    "000": "정상",
    "010": "등록되지 않은 API 키입니다.",
    "011": "사용할 수 없는 API 키입니다.",
    "012": "접근할 수 없는 IP입니다.",
    "013": "조회된 데이터가 없습니다.",
    "014": "파일이 존재하지 않습니다.",
    "020": "API 요청 제한을 초과했습니다.",
    "021": "조회 가능한 회사 개수가 초과했습니다.",
    "100": "필드의 부적절한 값입니다.",
    "101": "부적절한 접근입니다.",
    "800": "시스템 점검으로 인한 서비스가 중지 중입니다.",
    "900": "정의되지 않은 오류가 발생했습니다.",
    "901": "개인정보 보유기간이 만료된 API 키입니다.",
}

CORP_CLS_MARKET = {"Y": "KOSPI", "K": "KOSDAQ", "N": "KONEX", "E": "OTHER"}


class DartError(Exception):
    def __init__(self, message: str, status: str | None = None):
        super().__init__(message)
        self.status = status


class DartNotConfigured(DartError):
    def __init__(self) -> None:
        super().__init__("DART_API_KEY가 설정되지 않아 OpenDART 조회를 할 수 없습니다.", "NO_KEY")


class DartNoData(DartError):
    pass


@dataclass
class CorpCodeEntry:
    corp_code: str
    corp_name: str
    corp_eng_name: str | None
    stock_code: str | None
    modify_date: str | None


def viewer_url(rcept_no: str) -> str:
    return VIEWER_URL.format(rcept_no=rcept_no)


def parse_corp_code_xml(xml_bytes: bytes) -> list[CorpCodeEntry]:
    root = ET.fromstring(xml_bytes)
    out: list[CorpCodeEntry] = []
    for node in root.iter("list"):
        def g(tag: str) -> str | None:
            v = node.findtext(tag)
            v = v.strip() if v else None
            return v or None

        code = g("corp_code")
        name = g("corp_name")
        if not code or not name:
            continue
        out.append(CorpCodeEntry(code, name, g("corp_eng_name"), g("stock_code"), g("modify_date")))
    return out


_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t ]+")


def xml_to_text(markup: str) -> str:
    text = re.sub(r"(?i)<(br|/p|/tr|/title|/td|/th)[^>]*>", "\n", markup)
    text = _TAG_RE.sub(" ", text)
    text = unescape(text)
    text = _WS_RE.sub(" ", text)
    lines = [ln.strip() for ln in text.splitlines()]
    return "\n".join(ln for ln in lines if ln)


def extract_business_section(markup: str) -> str | None:
    """Extract 'II. 사업의 내용' section text from a DART 사업보고서 XML document."""
    start = re.search(r"<TITLE[^>]*>\s*I{2}\.\s*사업의\s*내용\s*</TITLE>", markup)
    if not start:
        return None
    end = re.search(r"<TITLE[^>]*>\s*I{3}\.\s*재무에\s*관한\s*사항", markup[start.end():])
    body = markup[start.end(): start.end() + end.start()] if end else markup[start.end():]
    return xml_to_text(body)


def decode_dart_xml(raw: bytes) -> str:
    for enc in ("utf-8", "euc-kr", "cp949"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


class DartClient:
    def __init__(self, api_key: str, http: httpx.Client | None = None, timeout: float = 30.0):
        self.api_key = api_key
        self.http = http or httpx.Client(timeout=timeout)

    def _require_key(self) -> None:
        if not self.api_key:
            raise DartNotConfigured()

    def _get_json(self, path: str, **params) -> dict:
        self._require_key()
        try:
            resp = self.http.get(f"{BASE_URL}/{path}", params={"crtfc_key": self.api_key, **params})
            resp.raise_for_status()
        except httpx.HTTPError as e:
            raise DartError(f"OpenDART 요청 실패: {type(e).__name__}") from e
        data = resp.json()
        status = data.get("status")
        if status == "013":
            raise DartNoData(STATUS_MESSAGES["013"], status)
        if status != "000":
            raise DartError(STATUS_MESSAGES.get(status, data.get("message", "알 수 없는 오류")), status)
        return data

    def _get_zip(self, path: str, **params) -> zipfile.ZipFile:
        self._require_key()
        try:
            resp = self.http.get(f"{BASE_URL}/{path}", params={"crtfc_key": self.api_key, **params})
            resp.raise_for_status()
        except httpx.HTTPError as e:
            raise DartError(f"OpenDART 요청 실패: {type(e).__name__}") from e
        content = resp.content
        if not content.startswith(b"PK"):
            # Error responses come back as XML/JSON instead of a zip.
            m = re.search(rb"<status>(\d+)</status>", content) or re.search(rb'"status"\s*:\s*"(\d+)"', content)
            status = m.group(1).decode() if m else None
            if status == "013" or status == "014":
                raise DartNoData(STATUS_MESSAGES[status], status)
            raise DartError(STATUS_MESSAGES.get(status or "", "OpenDART 파일 응답 오류"), status)
        return zipfile.ZipFile(io.BytesIO(content))

    # --- endpoints ---------------------------------------------------------
    def corp_codes(self) -> list[CorpCodeEntry]:
        zf = self._get_zip("corpCode.xml")
        name = next(n for n in zf.namelist() if n.lower().endswith(".xml"))
        return parse_corp_code_xml(zf.read(name))

    def company(self, corp_code: str) -> dict:
        return self._get_json("company.json", corp_code=corp_code)

    def disclosures(
        self,
        corp_code: str,
        bgn_de: str | None = None,
        end_de: str | None = None,
        pblntf_ty: str | None = None,
        pblntf_detail_ty: str | None = None,
        page_no: int = 1,
        page_count: int = 100,
    ) -> dict:
        params = {"corp_code": corp_code, "page_no": page_no, "page_count": page_count}
        params["bgn_de"] = bgn_de or (date.today() - timedelta(days=730)).strftime("%Y%m%d")
        if end_de:
            params["end_de"] = end_de
        if pblntf_ty:
            params["pblntf_ty"] = pblntf_ty
        if pblntf_detail_ty:
            params["pblntf_detail_ty"] = pblntf_detail_ty
        return self._get_json("list.json", **params)

    def latest_business_report(self, corp_code: str) -> dict | None:
        """가장 최근 사업보고서(A001) 공시 1건. 정정 공시가 있으면 최신 접수건."""
        try:
            data = self.disclosures(corp_code, pblntf_ty="A", pblntf_detail_ty="A001")
        except DartNoData:
            return None
        items = [i for i in data.get("list", []) if "사업보고서" in i.get("report_nm", "")]
        if not items:
            return None
        items.sort(key=lambda i: (i.get("rcept_dt", ""), i.get("rcept_no", "")), reverse=True)
        return items[0]

    def document_markup(self, rcept_no: str) -> str:
        zf = self._get_zip("document.xml", rcept_no=rcept_no)
        names = [n for n in zf.namelist() if n.lower().endswith(".xml")]
        # main document is named <rcept_no>.xml
        main = next((n for n in names if n.startswith(rcept_no)), names[0])
        return decode_dart_xml(zf.read(main))
