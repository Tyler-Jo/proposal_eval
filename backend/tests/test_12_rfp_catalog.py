from rfp.catalog import build_rfp_review_catalog
from rfp.models import EvidenceLocation, EvaluationRule, SpecificationRequirement

def test_12_builds_upload_review_categories_from_rfp() -> None:
    req = SpecificationRequirement("s", "", "1", "메모리", "16GB 이상", "required", False, "", {"type":"comparison"}, "high", False, EvidenceLocation(1, None, "** 16GB 이상"))
    rule = EvaluationRule("r", "general_item_deduction", "deduction_candidate", -0.5, None, "일반", "high", False, EvidenceLocation(2, None, "일반"))
    result = build_rfp_review_catalog([(3, "제조사 체계규격 충족확인서를 제출해야 한다.\n사업이해도 *상대평가")], [req], [rule])
    assert result["required_documents"][0]["name"].endswith("확인서")
    assert result["quantitative_evaluation_items"][0]["name"] == "메모리"
    assert result["qualitative_evaluation_items"][0]["name"] == "사업이해도"


def test_12_keeps_only_explicit_submission_document_names() -> None:
    pages = [(1, "PMR-006 하도급계약 준수 확인서를 제출해야 한다.\n‘제품 인증서’를 첨부하여야 한다.\n인증대상 제품은 반드시 인증서를 제출해야 한다.\n관련 확약서 미제출 시 감점한다.\n제안요청서를 통해 제출을 요구한 제품인증서\n요구사항 명칭 하도급계약 준수 확인서")]

    result = build_rfp_review_catalog(pages, [], [])

    assert [item["name"] for item in result["required_documents"]] == ["PMR-006 하도급계약 준수 확인서", "제품 인증서"]


def test_12_prefers_structured_submission_table_rows() -> None:
    rows = [{"name": "TTA IPv6 인증서", "evidence": {"page": 43, "bbox": [1, 2, 3, 4], "text": "제출하여야 한다."}, "confidence": "high", "review_required": True, "source": "rfp_submission_table"}]

    result = build_rfp_review_catalog([(1, "임의 확약서를 제출해야 한다.")], [], [], rows)

    assert result["required_documents"] == rows
