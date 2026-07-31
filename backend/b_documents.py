"""B권 증빙서류 존재 후보를 보수적으로 찾는다."""

from __future__ import annotations

import re
from typing import Any

_GENERIC = {"인증서", "증명서", "확인서", "필증", "등의", "또는", "관련", "대한", "제출"}
_REQUIRED_CUES = ("필수", "반드시", "미제출", "감점", "불이익", "인증", "필증", "등록", "서약", "확약")


def _tokens(name: str) -> list[str]:
    values = re.findall(r"[가-힣A-Za-z0-9]{2,}", name)
    return [value for value in values if value not in _GENERIC]


def _is_index_page(text: str) -> bool:
    compact = "".join(text.split()).casefold()
    return any(marker in compact for marker in ("제안서목차", "목차", "차례", "contents")) or len(re.findall(r"\d+\s*(?:[~-]\s*\d+)?\s*쪽", compact)) >= 3


def _category(document: dict[str, Any]) -> str:
    """RFP의 제출 방식 문구를 우선해 필수·일반 증빙을 구분한다."""

    evidence = document.get("evidence") if isinstance(document.get("evidence"), dict) else {}
    source = f"{document.get('name', '')} {evidence.get('text', '')}".replace(" ", "")
    return "REQUIRED" if any(cue in source for cue in _REQUIRED_CUES) else "GENERAL"


def find_required_documents(required_documents: list[dict[str, Any]], pages: list[tuple[int, str]]) -> list[dict[str, Any]]:
    """서류별로 정확/부분 일치를 분리해 평가위원 검토 근거를 만든다."""

    document_tokens = {str(document.get("name", "")).strip(): _tokens(str(document.get("name", ""))) for document in required_documents}
    # 한 페이지에서 여러 필수서류명이 함께 발견되면 실제 증빙보다 목차/목록일
    # 가능성이 높다. OCR이 '목차'를 놓친 경우를 위한 보조 차단 규칙이다.
    listing_pages = {
        page for page, text in pages
        if sum(bool(set(tokens) & {token for token in tokens if token.casefold() in text.casefold()}) for tokens in document_tokens.values()) >= 2
    }
    results: list[dict[str, Any]] = []
    for document in required_documents:
        name = str(document.get("name", "")).strip()
        tokens = _tokens(name)
        hits: list[tuple[int, str, int]] = []
        for page, text in pages:
            if _is_index_page(text) or page in listing_pages:
                continue
            normalized = text.casefold()
            matched = [token for token in tokens if token.casefold() in normalized]
            if matched:
                index = min(normalized.find(token.casefold()) for token in matched)
                hits.append((page, text[max(0, index - 100):index + 240].strip(), len(matched)))
        best = max(hits, key=lambda value: value[2], default=None)
        required_matches = 1 if len(tokens) <= 2 else 2
        status = "FOUND" if best and best[2] >= required_matches else "REVIEW_REQUIRED" if best else "MISSING"
        results.append({"name": name, "category": _category(document), "status": status, "page": best[0] if best else None, "evidence": best[1] if best else "", "matched_tokens": [token for token in tokens if best and token.casefold() in best[1].casefold()], "source": "B_PDF_OCR"})
    return results
