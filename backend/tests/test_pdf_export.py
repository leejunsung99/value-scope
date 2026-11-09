import io

from pypdf import PdfReader, PdfWriter

from app.services.pdf_export import add_page_numbers


def _blank_pdf(pages: int) -> bytes:
    w = PdfWriter()
    for _ in range(pages):
        w.add_blank_page(width=595, height=842)
    out = io.BytesIO()
    w.write(out)
    return out.getvalue()


def test_page_numbers_are_stamped_on_every_page():
    out = add_page_numbers(_blank_pdf(3), "ValueScope | 171090 | 20261109 (주)")
    r = PdfReader(io.BytesIO(out))
    texts = [p.extract_text() for p in r.pages]
    assert [("%d / 3" % i) in t for i, t in enumerate(texts, start=1)] == [True, True, True]
    assert "ValueScope | 171090 | 20261109" in texts[0] and "주" not in texts[0]  # non-ASCII dropped
