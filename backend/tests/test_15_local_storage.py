import fitz

from ocr import extract_pdf_pages_cached
from storage import LocalStore


def _pdf(path) -> None:
    document = fitz.open()
    page = document.new_page()
    page.insert_text((40, 40), "온디바이스 SQLite 캐시 테스트")
    document.save(path)
    document.close()


def _multipage_pdf(path, count: int) -> None:
    document = fitz.open()
    for _ in range(count):
        document.new_page()
    document.save(path)
    document.close()


def test_sqlite_persists_extraction_across_memory_cache_reset(tmp_path, monkeypatch) -> None:
    pdf = tmp_path / "proposal.pdf"
    _pdf(pdf)
    monkeypatch.setenv("PROPOSAL_EVALUATION_DATA_DIR", str(tmp_path / "app-data"))
    first = extract_pdf_pages_cached(str(pdf), mode="fallback")
    assert first.cache_hit is False

    import ocr
    ocr._DOCUMENT_CACHE.clear()
    second = extract_pdf_pages_cached(str(pdf), mode="fallback")

    assert second.cache_hit is True
    assert second.pages == first.pages
    assert LocalStore().path.is_file()


def test_extraction_resumes_from_last_saved_ten_page_chunk(tmp_path, monkeypatch) -> None:
    pdf = tmp_path / "scan.pdf"
    _multipage_pdf(pdf, 12)
    monkeypatch.setenv("PROPOSAL_EVALUATION_DATA_DIR", str(tmp_path / "app-data"))
    calls = []

    def interrupted_ocr(*_) -> str:
        calls.append(len(calls) + 1)
        if len(calls) == 11:
            raise RuntimeError("interrupted")
        return "페이지"

    monkeypatch.setattr("ocr._ocr_page", interrupted_ocr)
    import pytest
    with pytest.raises(RuntimeError, match="interrupted"):
        extract_pdf_pages_cached(str(pdf), mode="fallback")

    import ocr
    ocr._DOCUMENT_CACHE.clear()
    resumed_calls = []
    monkeypatch.setattr("ocr._ocr_page", lambda *_: resumed_calls.append(1) or "페이지")
    result = extract_pdf_pages_cached(str(pdf), mode="fallback")

    assert len(resumed_calls) == 2
    assert len(result.pages) == 12


def test_project_card_and_document_paths_persist(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PROPOSAL_EVALUATION_DATA_DIR", str(tmp_path / "app-data"))
    store = LocalStore()
    analysis = {"notice": "RFP 분석 완료", "review_catalog": {"required_documents": []}}
    store.save_project(
        project_id="project-1", name="온디바이스 사업", created_at="2026-07-31T00:00:00+00:00",
        documents={"RFP": [str(tmp_path / "rfp.pdf")], "A": [str(tmp_path / "a.pdf")], "B": [str(tmp_path / "b.pdf")]},
        rfp_analysis=analysis,
    )

    restored = LocalStore().load_projects()

    assert restored == [{
        "id": "project-1", "name": "온디바이스 사업", "created_at": "2026-07-31T00:00:00+00:00",
        "documents": {"RFP": [str(tmp_path / "rfp.pdf")], "A": [str(tmp_path / "a.pdf")], "B": [str(tmp_path / "b.pdf")]},
        "rfp_analysis": analysis,
    }]
