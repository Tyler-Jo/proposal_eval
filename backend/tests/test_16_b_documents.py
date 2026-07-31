from b_documents import find_required_documents


def test_b_document_check_requires_distinctive_tokens_and_returns_evidence() -> None:
    required = [{"name": "TTA Verified Ver.4 (ARIA) 인증서"}, {"name": "방송통신기자재 적합등록필증 또는 인증서"}]
    pages = [(2, "TTA Verified Ver.4 ARIA 인증서를 첨부합니다."), (8, "방송통신기자재 적합등록필증 사본")]

    results = find_required_documents(required, pages)

    assert results[0]["status"] == "FOUND"
    assert results[0]["page"] == 2
    assert results[1]["status"] == "FOUND"
    assert results[1]["page"] == 8


def test_b_document_check_excludes_table_of_contents() -> None:
    required = [{"name": "안전 보건 확보 조치 이행 확약서"}]
    pages = [(1, "제안서 목차\n안전 보건 확보 조치 이행 확약서 · 3쪽"), (3, "서약서\n안전 보건 확보 조치 이행 확약서")]

    result = find_required_documents(required, pages)[0]

    assert result["status"] == "FOUND"
    assert result["page"] == 3


def test_b_document_check_excludes_unlabelled_document_listing() -> None:
    required = [{"name": "안전 보건 확보 조치 이행 확약서"}, {"name": "TTA Verified ARIA 인증서"}]
    pages = [(1, "안전 보건 확보 조치 이행 확약서\nTTA Verified ARIA 인증서"), (4, "TTA Verified ARIA 인증서")]

    results = find_required_documents(required, pages)

    assert results[1]["page"] == 4
