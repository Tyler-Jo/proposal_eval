import fitz

from ocr import extract_pdf_pages_with_ocr


def _pdf(path, text: str = "") -> None:
    document = fitz.open()
    page = document.new_page()
    if text:
        page.insert_text((40, 40), text)
    document.save(path)
    document.close()


def test_fallback_uses_text_layer_when_page_has_text(tmp_path, monkeypatch) -> None:
    path = tmp_path / "text.pdf"
    _pdf(path, "RFP evaluation requirements")
    monkeypatch.setattr("ocr._ocr_page", lambda *_: (_ for _ in ()).throw(AssertionError("OCR should not run")))

    pages = extract_pdf_pages_with_ocr(str(path))

    assert pages[0].source == "pymupdf_text_layer"
    assert "evaluation requirements" in pages[0].text


def test_fallback_ocrs_empty_page_and_preserves_page_number(tmp_path, monkeypatch) -> None:
    path = tmp_path / "scan.pdf"
    _pdf(path)
    monkeypatch.setattr("ocr._ocr_page", lambda *_: "필수 제출 서류")

    pages = extract_pdf_pages_with_ocr(str(path))

    assert pages == [type(pages[0])(page=1, text="필수 제출 서류", source="paddleocr")]
