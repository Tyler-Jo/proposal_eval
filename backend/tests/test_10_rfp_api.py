import fitz

from api.server import _analyze_rfp


def test_10_rfp_analysis_returns_reviewable_requirement_payload(tmp_path) -> None:
    path = tmp_path / "rfp.pdf"
    document = fitz.open()
    page = document.new_page()
    page.insert_text((40, 40), "Item Category Spec Qty", fontsize=12)
    page.insert_text((40, 60), "** 16GB minimum", fontsize=12)
    document.save(path)
    document.close()

    result = _analyze_rfp(str(path))

    assert result["source_row_count"] == 1
    assert result["requirement_count"] == 1
    assert result["requirements"][0]["importance"] == "required"
    assert result["requirements"][0]["evidence"]["page"] == 1
    assert "검토 필요" in result["notice"]


def test_rfp_analysis_reuses_sqlite_result(tmp_path, monkeypatch) -> None:
    path = tmp_path / "rfp.pdf"
    document = fitz.open(); page = document.new_page(); page.insert_text((40, 40), "Item Category Spec Qty\n** 16GB minimum"); document.save(path); document.close()
    monkeypatch.setenv("PROPOSAL_EVALUATION_DATA_DIR", str(tmp_path / "app-data"))

    _analyze_rfp(str(path))
    result = _analyze_rfp(str(path))

    assert result["cache_hit"] is True
