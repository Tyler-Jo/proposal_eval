import fitz

from api.server import Evaluation, _run_evaluation
from ocr import extract_pdf_pages_cached


def _pdf(path) -> None:
    document = fitz.open()
    page = document.new_page()
    page.insert_text((40, 40), "수행 계획과 일정 관리 방안을 제시한다.")
    document.save(path)
    document.close()


def test_cached_extraction_reuses_unchanged_pdf(tmp_path) -> None:
    path = tmp_path / "proposal.pdf"
    _pdf(path)

    first = extract_pdf_pages_cached(str(path))
    second = extract_pdf_pages_cached(str(path))

    assert first.cache_hit is False
    assert second.cache_hit is True
    assert second.pages == first.pages


def test_rule_evaluation_completes_without_waiting_for_llm(tmp_path, monkeypatch) -> None:
    path = tmp_path / "proposal.pdf"
    _pdf(path)
    evaluation = Evaluation("evaluation", "project")
    monkeypatch.setattr("api.server.LOCAL_MODEL.generate_comment", lambda *_: (_ for _ in ()).throw(AssertionError("LLM must be deferred")))

    _run_evaluation(evaluation, str(path), {"items": [{"id": "plan", "name": "수행 계획", "max_score": 10, "required_keywords": ["수행", "계획"]}]})

    assert evaluation.status == "COMPLETED"
    assert evaluation.comment_status == "NOT_STARTED"
    assert evaluation.result["item_results"][0]["comment_source"] == "rule_based_pending"
    assert evaluation.processing["cache_hit"] is False


def test_general_item_deduction_links_rfp_rule_to_missing_item(tmp_path) -> None:
    path = tmp_path / "proposal.pdf"
    _pdf(path)
    evaluation = Evaluation("evaluation-adjustment", "project")

    _run_evaluation(evaluation, str(path), {"items": [{"id": "general", "name": "일반 규격", "source": "specification", "importance": "general", "max_score": 10, "required_keywords": ["없는키워드"]}], "adjustment_rules": [{"name": "general_item_deduction", "rule_type": "general_item_deduction", "effect": "deduction_candidate", "value": -0.5, "cap": None, "condition_summary": "일반항목 미충족 시 항목당 -0.5점", "evidence": {"page": 4, "text": "일반항목 미충족 시 감점"}}]})

    adjustment = evaluation.result["adjustment_results"][0]
    assert adjustment["status"] == "APPLIED"
    assert adjustment["applied_delta"] == -0.5
    assert adjustment["related_items"][0]["name"] == "일반 규격"
    assert "제안서에서" in adjustment["related_items"][0]["proposal_basis"]
    assert "없는키워드" in adjustment["related_items"][0]["proposal_basis"]
