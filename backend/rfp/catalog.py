"""RFP 업로드 직후 표시할 심의 기준 초안 후보."""
from __future__ import annotations
import re
from typing import Any
from .models import EvaluationRule, SpecificationRequirement

_DOC_SUFFIX = r"(?:증명원|확인서|인증서|계약서|세금계산서|확약서|평가서)"
_SUBMISSION_VERB = r"(?:제출|첨부|제시|구비)"
_NEGATIVE_SUBMISSION = re.compile(r"(?:미\s*제출|제출\s*(?:하지|불가|누락)|제출을\s*요구|제출한)")
_QUOTED_DOCUMENT = re.compile(rf"[‘'“\"](?P<name>[^’'”\"]{{2,60}}?{_DOC_SUFFIX})[’'”\"]\s*(?:을|를|은|는|이|가)?\s*(?:반드시\s*)?{_SUBMISSION_VERB}")
_DOCUMENT_WITH_SUBMISSION = re.compile(rf"(?P<name>[가-힣A-Za-z0-9·() /-]{{2,60}}?{_DOC_SUFFIX})\s*(?:을|를|은|는|이|가)?\s*(?:반드시\s*)?{_SUBMISSION_VERB}(?:해야|하여야|하여|하고|한다|합니다|할|시|하는)?")
_QUAL = re.compile(r"^\s*([가-힣A-Za-z][가-힣A-Za-z ]{1,30}?)\s*\*?\s*상대평가")

def _e(text: str) -> str: return re.sub(r"\s+", " ", text).strip()[:300]


def _document_name(value: str) -> str | None:
    """문장 조각이 아닌 제출 대상 문서명만 허용한다."""

    name = _e(value).strip(" ·‘’'“”()")
    name = re.sub(r"^(?:\(?\d+\)?[.)]?\s*)+", "", name)
    # '제품은 반드시 인증서'처럼 설명문 주어가 섞인 후보는 문서명이 아니다.
    if re.search(r"(?:은|는|이|가|을|를)\s", name):
        return None
    if len(name) < 2 or name in {"인증서", "확인서", "확약서", "계약서", "평가서"}:
        return None
    return name


def _required_document_names(line: str) -> list[str]:
    """명시적인 제출 의무에서만 서류명 후보를 얻는다.

    미제출 감점·인증 설명·'제출을 요구한' 과거 서술은 제출 목록이 아니므로
    의도적으로 제외한다. 결과는 평가위원이 확인하는 후보이며 자동 확정값이 아니다.
    """

    compact = _e(line)
    if _NEGATIVE_SUBMISSION.search(compact):
        return []
    matches = list(_QUOTED_DOCUMENT.finditer(compact)) + list(_DOCUMENT_WITH_SUBMISSION.finditer(compact))
    result: list[str] = []
    for match in matches:
        name = _document_name(match.group("name"))
        if name and name not in result:
            result.append(name)
    return result

def build_rfp_review_catalog(pages: list[tuple[int, str]], requirements: list[SpecificationRequirement], rules: list[EvaluationRule], required_document_rows: list[dict[str, Any]] | None = None) -> dict[str, list[dict[str, Any]]]:
    docs: dict[str, dict[str, Any]] = {}
    qualitative: dict[str, dict[str, Any]] = {}
    # RFP의 증빙 표가 존재하면 열 제목·행 구조를 신뢰하고 본문 추정을 하지 않는다.
    for item in required_document_rows or []:
        name = item.get("name")
        if isinstance(name, str) and name:
            docs[name] = item
    use_text_fallback = not docs
    for page, text in pages:
        for line in text.splitlines():
            if use_text_fallback:
                for name in _required_document_names(line):
                    docs.setdefault(name, {"name": name, "evidence": {"page": page, "bbox": None, "text": _e(line)}, "confidence": "medium", "review_required": True, "source": "text_submission_sentence"})
            match = _QUAL.search(line)
            if match:
                name = _e(match.group(1))
                qualitative.setdefault(name, {"name": name, "evaluation_method": "relative", "evidence": {"page": page, "bbox": None, "text": _e(line)}, "confidence": "medium", "review_required": True})
    quantitative = [{"name": x.category or x.equipment_name or x.raw_requirement[:80], "source": "specification", "importance": x.importance, "condition": x.condition, "rfp_requirement": x.raw_requirement, "bonus_eligible": x.bonus_eligible, "evidence": x.evidence.to_dict(), "confidence": x.confidence, "review_required": x.review_required} for x in requirements]
    quantitative += [{"name": x.rule_type, "source": "evaluation_rule", "rule_type": x.rule_type, "effect": x.effect, "value": x.value, "cap": x.cap, "condition_summary": x.condition_summary, "evidence": x.evidence.to_dict(), "confidence": x.confidence, "review_required": x.review_required} for x in rules]
    return {"required_documents": list(docs.values()), "quantitative_evaluation_items": quantitative, "qualitative_evaluation_items": list(qualitative.values())}
