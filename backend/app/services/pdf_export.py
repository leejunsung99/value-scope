"""분석 결과 PDF 출력.

화면과 같은 보고서(차트 포함)를 인쇄용 페이지(/print/projects/{pid}/runs/{rid})로 렌더링하고,
로컬에 설치된 Edge/Chrome을 headless로 실행해 PDF로 저장한다. 외부 서비스로 데이터를 보내지 않는다.
PDF는 임시 폴더에 만들어 응답으로 내려보낸 뒤 삭제한다(저장소·서버 디스크에 남기지 않음).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

CANDIDATES = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    "/usr/bin/chromium", "/usr/bin/chromium-browser", "/usr/bin/google-chrome",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
]
TIMEOUT_S = 90


class PdfUnavailable(Exception):
    pass


def find_browser(configured: str | None = None) -> str | None:
    for p in [configured, os.environ.get("PDF_BROWSER_PATH"), *CANDIDATES]:
        if p and Path(p).is_file():
            return p
    for name in ("msedge", "chrome", "chromium", "google-chrome"):
        found = shutil.which(name)
        if found:
            return found
    return None


def render_pdf(url: str, browser: str | None = None, footer: str = "ValueScope analysis report") -> bytes:
    exe = find_browser(browser)
    if not exe:
        raise PdfUnavailable("PDF 변환에 사용할 Edge/Chrome을 찾지 못했습니다. .env의 PDF_BROWSER_PATH에 브라우저 경로를 지정하세요.")
    work = Path(tempfile.mkdtemp(prefix="vs_pdf_"))
    out = work / "report.pdf"
    try:
        cmd = [
            exe, "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
            "--disable-extensions", "--disable-sync", "--hide-scrollbars", "--window-size=760,1400",  # A4 세로 지면 폭(182mm≈688px)+여백
            f"--user-data-dir={work / 'profile'}",  # 사용자의 브라우저 프로필·실행 중인 창과 분리
            "--no-pdf-header-footer", "--run-all-compositor-stages-before-draw",
            "--virtual-time-budget=20000",            # 데이터 로딩·차트 렌더링 대기
            f"--print-to-pdf={out}", url,
        ]
        try:
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=TIMEOUT_S, check=False)
        except subprocess.TimeoutExpired as e:
            raise PdfUnavailable(f"PDF 변환이 {TIMEOUT_S}초 안에 끝나지 않았습니다.") from e
        if not out.is_file() or out.stat().st_size < 1000:
            raise PdfUnavailable("브라우저가 PDF를 만들지 못했습니다. 화면 상단의 '브라우저 인쇄로 저장'을 이용하세요.")
        data = out.read_bytes()
        try:
            return add_page_numbers(data, footer)
        except Exception:  # noqa: BLE001 — 쪽 번호 실패가 PDF 출력을 막지 않도록
            return data
    finally:
        shutil.rmtree(work, ignore_errors=True)



def add_page_numbers(pdf: bytes, label: str = "ValueScope") -> bytes:
    """각 쪽 하단에 쪽 번호('n / N')와 왼쪽 label을 찍는다. 내장 Helvetica(ASCII)만 써서 외부 폰트·DLL이 필요 없다."""
    import io

    from pypdf import PageObject, PdfReader, PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(pdf)))
    n = len(writer.pages)
    safe = "".join(ch for ch in label if 32 <= ord(ch) < 127 and ch not in "\()")
    for i, page in enumerate(writer.pages, start=1):
        w, h = float(page.mediabox.width), float(page.mediabox.height)
        num = f"{i} / {n}"
        ops = (f"q 0.45 g BT /F1 8 Tf {w / 2 - len(num) * 2.2:.1f} 20 Td ({num}) Tj ET "
               f"BT /F1 7 Tf 40 20 Td ({safe}) Tj ET Q")
        overlay = PageObject.create_blank_page(width=w, height=h)
        font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"),
                                 NameObject("/BaseFont"): NameObject("/Helvetica")})
        overlay[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
        stream = DecodedStreamObject()
        stream.set_data(ops.encode("ascii"))
        overlay[NameObject("/Contents")] = writer._add_object(stream)
        page.merge_page(overlay)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()
