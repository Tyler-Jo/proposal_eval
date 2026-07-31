"""페이지 근거를 보존하는 Test 05 문서 평가 공통 계약."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import re
from typing import Any


@dataclass(frozen=True)
class PageText:
    page: int
    text: str
    token_count: int


@dataclass(frozen=True)
class DocumentWindow:
    number: int
    pages: tuple[PageText, ...]

    @property
    def page_range(self) -> list[int]:
        return [self.pages[0].page, self.pages[-1].page]

    @property
    def text(self) -> str:
        return "\n\n".join(f"[페이지 {item.page}]\n{item.text}" for item in self.pages)

    @property
    def token_count(self) -> int:
        return sum(item.token_count for item in self.pages)


@dataclass(frozen=True)
class RubricItem:
    item_id: str
    name: str
    max_score: int
    required_keywords: tuple[str, ...]
    source: str = ""
    importance: str = "unknown"
    condition: dict[str, Any] | None = None
    rfp_requirement: str = ""


def extract_pdf_pages(pdf_path: str) -> list[tuple[int, str]]:
    """PDF 텍스트 레이어를 페이지별로 추출한다. OCR은 별도 단계다."""

    import fitz

    document = fitz.open(pdf_path)
    try:
        return [(number, page.get_text("text", sort=True).strip()) for number, page in enumerate(document, start=1)]
    finally:
        document.close()


def choose_page_source(text: str, ocr_mode: str, minimum_text_chars: int) -> str:
    """텍스트 레이어와 OCR 중 페이지별 입력 원본을 결정한다."""

    if ocr_mode not in {"all", "fallback", "text"}:
        raise ValueError("ocr_mode는 all, fallback, text 중 하나여야 합니다.")
    if minimum_text_chars < 0:
        raise ValueError("minimum_text_chars는 0 이상이어야 합니다.")
    if ocr_mode == "all" or (ocr_mode == "fallback" and len(text.strip()) < minimum_text_chars):
        return "paddleocr"
    return "pymupdf_text_layer"


def build_windows(
    page_texts: list[tuple[int, str]],
    token_count: Callable[[str], int],
    max_tokens: int,
    overlap_tokens: int,
) -> list[DocumentWindow]:
    """페이지를 쪼개지 않고 최대 토큰 예산 및 페이지 기반 겹침을 유지한다."""

    if max_tokens <= 0 or not 0 <= overlap_tokens < max_tokens:
        raise ValueError("max_tokens는 양수이고 overlap_tokens는 0 이상 max_tokens 미만이어야 합니다.")
    pages = [PageText(page, text, token_count(text)) for page, text in page_texts if text.strip()]
    if not pages:
        raise ValueError("추출 가능한 텍스트가 없습니다. 스캔 PDF는 OCR 결과를 먼저 제공해야 합니다.")
    oversized = [item.page for item in pages if item.token_count > max_tokens]
    if oversized:
        raise ValueError(f"단일 페이지가 토큰 예산을 초과합니다: {oversized}. 페이지 분할 또는 더 큰 예산이 필요합니다.")

    windows: list[DocumentWindow] = []
    start = 0
    while start < len(pages):
        end = start
        used = 0
        while end < len(pages) and used + pages[end].token_count <= max_tokens:
            used += pages[end].token_count
            end += 1
        windows.append(DocumentWindow(len(windows) + 1, tuple(pages[start:end])))
        if end == len(pages):
            break
        overlap_used = 0
        next_start = end
        while next_start > start and overlap_used < overlap_tokens:
            next_start -= 1
            overlap_used += pages[next_start].token_count
        start = next_start if next_start < end else end
    return windows


def citation_pages(evidence: str, pages: tuple[PageText, ...]) -> list[int]:
    """모델 인용의 전체 또는 줄 단위 발췌가 포함된 실제 PDF 페이지를 반환한다."""

    normalized = "".join(evidence.split())
    if not normalized:
        return []
    fragments = [normalized]
    fragments.extend(fragment for line in evidence.splitlines() if len(fragment := "".join(line.split())) >= 20)
    return [item.page for item in pages if any(fragment in "".join(item.text.split()) for fragment in fragments)]


def aggregate_scores(results: list[dict[str, object]]) -> int:
    scores = [item["score"] for item in results if isinstance(item.get("score"), int)]
    if not scores:
        raise ValueError("집계할 윈도우 점수가 없습니다.")
    return round(sum(scores) / len(scores))


def parse_rubric(payload: Any) -> list[RubricItem]:
    """배점표 JSON을 점수 계산 가능한 최소 계약으로 변환한다."""

    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list) or not payload["items"]:
        raise ValueError("배점표는 items가 하나 이상인 JSON 객체여야 합니다.")
    items: list[RubricItem] = []
    for value in payload["items"]:
        if not isinstance(value, dict):
            raise ValueError("배점표 항목은 객체여야 합니다.")
        item_id, name, max_score, keywords = value.get("id"), value.get("name"), value.get("max_score"), value.get("required_keywords")
        if not isinstance(item_id, str) or not item_id.strip() or not isinstance(name, str) or not name.strip():
            raise ValueError("배점표 항목에는 id와 name이 필요합니다.")
        if not isinstance(max_score, int) or max_score <= 0:
            raise ValueError("max_score는 양의 정수여야 합니다.")
        if not isinstance(keywords, list) or not keywords or not all(isinstance(keyword, str) and keyword.strip() for keyword in keywords):
            raise ValueError("required_keywords는 비어 있지 않은 문자열 목록이어야 합니다.")
        source = value.get("source", "")
        importance = value.get("importance", "unknown")
        condition = value.get("condition")
        items.append(RubricItem(item_id.strip(), name.strip(), max_score, tuple(keyword.strip() for keyword in keywords), str(source), str(importance), condition if isinstance(condition, dict) else None, str(value.get("rfp_requirement", ""))))
    return items


_SPEC_VALUE = re.compile(r"(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>GHz|G㎐|MHz|Core|GB|TB|Mbps|ms|시간|개월|대|개|식|포트|회|%)", re.IGNORECASE)


def _comparison_options(condition: dict[str, Any] | None) -> list[list[dict[str, Any]]]:
    if not condition:
        return []
    if condition.get("type") == "comparison":
        return [[condition]]
    if condition.get("type") == "all":
        children = [child for child in condition.get("conditions", []) if isinstance(child, dict)]
        comparisons = [child for child in children if child.get("type") == "comparison"]
        return [comparisons] if comparisons and len(comparisons) == len(children) else []
    if condition.get("type") == "any":
        return [option for child in condition.get("conditions", []) if isinstance(child, dict) for option in _comparison_options(child)]
    return []


def _comparison_met(operator: str, proposed: float, required: float) -> bool:
    return {"gte": proposed >= required, "lte": proposed <= required, "lt": proposed < required, "gt": proposed > required}.get(operator, False)


def _numeric_specification_result(item: RubricItem, pages: list[tuple[int, str]]) -> dict[str, object] | None:
    """동일 항목 주변의 수치·단위를 RFP 수치 조건과 비교한다.

    표의 복잡한 조합 조건은 여기서 억지로 판정하지 않고, 단일 또는 명시적 all
    비교 조건만 처리한다. 결과에는 RFP/제안 수치를 모두 남긴다.
    """

    options = _comparison_options(item.condition)
    if not options:
        return None
    label = item.name.strip()
    matching = [(page, text) for page, text in pages if label and label.casefold() in text.casefold()]
    if not matching:
        return {
            "status": "MISSING", "evidence_pages": [], "missing_keywords": [label],
            "comparison": {"rfp_requirement": item.rfp_requirement or item.condition.get("text", ""), "proposed": "제안서에서 항목을 찾지 못함", "summary": f"RFP는 {item.rfp_requirement or item.condition.get('text', '')}을 요구하지만 제안서에서 {label} 항목을 찾지 못했습니다.", "checks": []},
        }
    observed: dict[str, float] = {}
    for _, text in matching:
        for occurrence in re.finditer(re.escape(label), text, flags=re.IGNORECASE):
            window = text[max(0, occurrence.start() - 80): occurrence.end() + 220]
            for found in _SPEC_VALUE.finditer(window):
                observed.setdefault(found.group("unit").casefold().replace("g㎐", "ghz"), float(found.group("value")))
    option_checks: list[list[dict[str, object]]] = []
    for option in options:
        checks: list[dict[str, object]] = []
        for condition in option:
            unit = str(condition.get("unit", ""))
            required = float(condition["value"])
            proposed = observed.get(unit.casefold())
            checks.append({"unit": unit, "required": required, "operator": condition["operator"], "proposed": proposed, "met": proposed is not None and _comparison_met(str(condition["operator"]), proposed, required)})
        option_checks.append(checks)
    checks = next((candidate for candidate in option_checks if all(bool(check["met"]) for check in candidate)), min(option_checks, key=lambda candidate: sum(not bool(check["met"]) for check in candidate)))
    passed = all(bool(check["met"]) for check in checks)
    unit_labels = {"ghz": "GHz", "mhz": "MHz", "core": "Core", "gb": "GB", "tb": "TB", "mbps": "Mbps"}
    proposed_text = ", ".join(f"{value:g}{unit_labels.get(unit, unit)}" for unit, value in observed.items()) or "수치 확인 불가"
    failed = [check for check in checks if not check["met"]]
    failed_text = ", ".join(f"{check['required']:g}{check['unit']} { {'gte': '이상', 'lte': '이하', 'lt': '미만', 'gt': '초과'}[str(check['operator'])]} (제안 {f'{check['proposed']:g}{check['unit']}' if check['proposed'] is not None else '확인 불가'})" for check in failed)
    return {
        "status": "MET" if passed else "MISSING", "evidence_pages": [page for page, _ in matching],
        "missing_keywords": [] if passed else [failed_text],
        "comparison": {"rfp_requirement": item.rfp_requirement or item.condition.get("text", ""), "proposed": proposed_text, "summary": f"RFP 요구: {item.rfp_requirement or item.condition.get('text', '')} / 제안서 확인: {proposed_text} / {'충족' if passed else f'미충족: {failed_text}'}", "checks": checks},
    }


def score_rubric(page_texts: list[tuple[int, str]], items: list[RubricItem]) -> list[dict[str, object]]:
    """필수 키워드 충족 여부로 배점표 점수를 결정하고 근거 페이지를 남긴다."""

    def is_index_page(text: str) -> bool:
        compact = "".join(text.split())
        page_ranges = compact.count("~") + compact.count("…") + compact.count("쪽")
        return ("목차" in compact or "조견표" in compact) and page_ranges >= 2

    eligible_pages = [(page, text) for page, text in page_texts if not is_index_page(text)]
    results: list[dict[str, object]] = []
    for item in items:
        numeric = _numeric_specification_result(item, eligible_pages)
        if numeric is not None:
            results.append({"id": item.item_id, "name": item.name, "source": item.source, "importance": item.importance, "max_score": item.max_score, "score": item.max_score if numeric["status"] == "MET" else 0, "required_keywords": list(item.required_keywords), **numeric})
            continue
        keyword_pages = {keyword: [page for page, text in eligible_pages if keyword.casefold() in text.casefold()] for keyword in item.required_keywords}
        missing = [keyword for keyword, pages in keyword_pages.items() if not pages]
        pages = sorted({page for values in keyword_pages.values() for page in values})
        results.append({"id": item.item_id, "name": item.name, "source": item.source, "importance": item.importance, "max_score": item.max_score, "score": item.max_score if not missing else 0, "required_keywords": list(item.required_keywords), "missing_keywords": missing, "evidence_pages": pages, "status": "MET" if not missing else "MISSING"})
    return results
