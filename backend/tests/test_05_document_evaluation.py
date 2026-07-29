from test_dashboard.document_evaluation import aggregate_scores, build_windows, choose_page_source, citation_pages, parse_rubric, score_rubric


def test_05_document_windows_preserve_pages_and_overlap() -> None:
    pages = [(1, "가" * 4), (2, "나" * 4), (3, "다" * 4)]
    windows = build_windows(pages, len, max_tokens=8, overlap_tokens=4)

    assert [window.page_range for window in windows] == [[1, 2], [2, 3]]
    assert windows[1].text.startswith("[페이지 2]")


def test_05_citation_pages_and_score_aggregation() -> None:
    window = build_windows([(3, "복구계획을 제출한다."), (4, "이중화한다.")], len, 30, 1)[0]

    assert citation_pages("복구계획을\n제출한다.", window.pages) == [3]
    assert citation_pages("없는 인용", window.pages) == []
    assert aggregate_scores([{"score": 70}, {"score": 80}]) == 75


def test_05_citation_allows_a_model_to_join_pdf_lines() -> None:
    window = build_windows([(8, "□(제안서평가) 제안서 평가\n□(협상 진행) 우선협상대상자와 세부 과업내용 협상을 진행함")], len, 100, 1)[0]

    assert citation_pages("□제안서 평가\n□(협상 진행) 우선협상대상자와 세부 과업내용 협상을 진행함", window.pages) == [8]


def test_05_ocr_source_selection() -> None:
    assert choose_page_source("충분한 텍스트", "all", 100) == "paddleocr"
    assert choose_page_source("짧음", "fallback", 100) == "paddleocr"
    assert choose_page_source("충분한 텍스트" * 100, "fallback", 100) == "pymupdf_text_layer"
    assert choose_page_source("", "text", 100) == "pymupdf_text_layer"


def test_05_rubric_score_comes_from_required_keywords() -> None:
    rubric = parse_rubric({"items": [{"id": "schedule", "name": "일정", "max_score": 20, "required_keywords": ["착수", "완료"]}]})

    assert score_rubric([(2, "착수 계획"), (3, "완료 보고")], rubric)[0]["score"] == 20
    assert score_rubric([(2, "착수 계획")], rubric)[0]["status"] == "MISSING"
