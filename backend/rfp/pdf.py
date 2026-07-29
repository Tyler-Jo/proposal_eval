"""텍스트 레이어 PDF에서 체계규격 표 후보 행을 추출한다."""

from __future__ import annotations

import re
from pathlib import Path

from .models import SpecificationSourceRow

_IMPORTANCE_MARKER = re.compile(r"(?:^|\s)(?:\d+\.\s*)*(?:\*\*|[∙·])\s*")


def _cell_text(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _column_index(header: list[str], names: tuple[str, ...]) -> int | None:
    for index, value in enumerate(header):
        compact = value.replace(" ", "").casefold()
        if any(name.casefold() in compact for name in names):
            return index
    return None


def _requirement_column_index(header: list[str]) -> int | None:
    """'규격평가' 같은 평가명과 체계규격 열 제목을 구분한다."""

    accepted = {"규격", "요구사항", "요구규격", "spec", "requirement", "specification"}
    for index, value in enumerate(header):
        compact = value.replace(" ", "").casefold()
        compact = re.sub(r"^(?:\d+\.)+", "", compact)
        if compact in accepted:
            return index
    return None


def _marker_key(text: str) -> str:
    return re.sub(r"^(?:\d+\.\s*)+(?=(?:\*\*|[∙·]))", "", _cell_text(text)).replace(" ", "")


def _marker_lines(page: object) -> list[SpecificationSourceRow]:
    """표 선을 인식하지 못한 PDF에서 표식이 있는 텍스트 행을 보조 수집한다."""

    result: list[SpecificationSourceRow] = []
    page_dict = page.get_text("dict")
    for block in page_dict.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            text = _cell_text("".join(span.get("text", "") for span in line.get("spans", [])))
            if not _IMPORTANCE_MARKER.search(text):
                continue
            # "** : 필수항목, ∙ : 일반항목"은 표의 범례이므로 규격으로 취급하지 않는다.
            if "필수항목" in text or "일반항목" in text:
                continue
            bbox = line.get("bbox")
            result.append(
                SpecificationSourceRow(
                    page=page.number + 1,
                    raw_requirement=text,
                    bbox=tuple(float(value) for value in bbox) if bbox else None,
                )
            )
    return result


def _has_specification_header(page: object) -> bool:
    for block in page.get_text("dict").get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            compact = _cell_text("".join(span.get("text", "") for span in line.get("spans", []))).replace(" ", "").casefold()
            if ("규격" in compact and "수량" in compact) or ("spec" in compact and ("qty" in compact or "quantity" in compact)):
                return True
    return False


def extract_specification_source_rows(pdf_path: str) -> list[SpecificationSourceRow]:
    """PyMuPDF 표 탐지 결과를 규격 행 후보로 변환한다.

    모든 PDF 표가 논리적인 셀 구조를 보장하지 않으므로, 이 함수의 결과는 규칙 추출 후보이며
    행·표식이 불명확하면 이후 단계에서 검토 대상으로 남긴다.
    """

    import fitz

    path = Path(pdf_path)
    if not path.is_file() or path.suffix.lower() != ".pdf":
        raise ValueError("존재하는 PDF 파일이 필요합니다.")
    rows: list[SpecificationSourceRow] = []
    document = fitz.open(path)
    try:
        specification_context_open = False
        for page_number, page in enumerate(document, start=1):
            page_rows: list[SpecificationSourceRow] = []
            tables = page.find_tables().tables
            for table in tables:
                values = [[_cell_text(cell) for cell in row] for row in table.extract()]
                if not values:
                    continue
                detected_header = getattr(getattr(table, "header", None), "names", None)
                header = [_cell_text(value) for value in detected_header] if detected_header else values[0]
                header_is_external = bool(getattr(getattr(table, "header", None), "external", False))
                requirement_index = _requirement_column_index(header)
                if requirement_index is None:
                    continue
                item_index = _column_index(header, ("항목", "item"))
                category_index = _column_index(header, ("구분", "category"))
                quantity_index = _column_index(header, ("수량", "qty", "quantity"))
                for row in (values if header_is_external else values[1:]):
                    if requirement_index >= len(row) or not row[requirement_index]:
                        continue
                    # 어떤 PDF는 헤더를 본문 첫 행으로도 반환한다.
                    if _cell_text(row[requirement_index]).casefold() == _cell_text(header[requirement_index]).casefold():
                        continue
                    page_rows.append(
                        SpecificationSourceRow(
                            page=page_number,
                            raw_requirement=row[requirement_index],
                            item_number=row[item_index] if item_index is not None and item_index < len(row) else "",
                            category=row[category_index] if category_index is not None and category_index < len(row) else "",
                            quantity=row[quantity_index] if quantity_index is not None and quantity_index < len(row) else "",
                            bbox=tuple(float(value) for value in table.bbox),
                        )
                    )
            marker_rows = _marker_lines(page)
            allow_marker_fallback = bool(page_rows) or _has_specification_header(page) or specification_context_open
            if allow_marker_fallback:
                known = {_marker_key(row.raw_requirement) for row in page_rows}
                for row in marker_rows:
                    key = _marker_key(row.raw_requirement)
                    if key and not any(key in existing or existing in key for existing in known):
                        page_rows.append(row)
                        known.add(key)
            specification_context_open = allow_marker_fallback and bool(page_rows or marker_rows)
            rows.extend(page_rows)
    finally:
        document.close()
    return rows


def extract_specification_source_rows_from_text_pages(pages: list[tuple[int, str]]) -> list[SpecificationSourceRow]:
    """OCR 텍스트에서 표식이 보존된 체계규격 행을 보조 추출한다.

    스캔 PDF는 표 셀을 신뢰성 있게 복원할 수 없으므로 ``**``/``∙``가 남은 행만
    후보로 삼고, 좌표·열 구조가 없다는 점은 이후 ``review_required``로 남긴다.
    """

    rows: list[SpecificationSourceRow] = []
    for page, text in pages:
        for line in text.splitlines():
            value = _cell_text(line)
            if _IMPORTANCE_MARKER.search(value) and "필수항목" not in value and "일반항목" not in value:
                rows.append(SpecificationSourceRow(page=page, raw_requirement=value))
    return rows


def extract_required_document_rows(pdf_path: str) -> list[dict[str, object]]:
    """RFP의 ``항목 | 작성방법`` 증빙 표에서 제출 문서명을 복원한다.

    표의 첫 열은 제출 서류명이므로 본문 문장 정규식보다 우선한다. 표 구조를 찾지
    못한 RFP에서만 catalog 모듈의 보수적 문장 후보 추출을 fallback으로 사용한다.
    """

    import fitz

    path = Path(pdf_path)
    if not path.is_file() or path.suffix.lower() != ".pdf":
        raise ValueError("존재하는 PDF 파일이 필요합니다.")
    result: list[dict[str, object]] = []
    seen: set[str] = set()
    document = fitz.open(path)
    try:
        evidence_section_open = False
        pending_row: tuple[str, str] | None = None
        for page_number, page in enumerate(document, start=1):
            for table in page.find_tables().tables:
                values = [[_cell_text(cell) for cell in row] for row in table.extract()]
                if not values:
                    continue
                header = [_cell_text(value) for value in values[0]]
                if len(header) < 2 or header[0].replace(" ", "") != "항목" or "작성방법" not in header[1].replace(" ", ""):
                    continue
                # A권 작성 기준과 B권 증빙자료는 같은 표 형식이다. B권의
                # 'Ⅱ. 증빙자료' 표가 시작된 뒤의 연속 표만 제출 서류로 본다.
                if any("Ⅱ.증빙자료" in re.sub(r"\s+", "", cell) for row in values for cell in row):
                    evidence_section_open = True
                if not evidence_section_open:
                    continue
                for row in values[1:]:
                    if len(row) < 2 or not row[0] or not row[1]:
                        continue
                    method = _cell_text(row[1])
                    raw_name = _cell_text(row[0])
                    has_submission = bool(re.search(r"(?:제출|첨부|제시|구비)", re.sub(r"\s+", "", method)))
                    # 표가 페이지에서 끊어지면 다음 페이지 첫 행이 번호 없는
                    # 제목 조각으로 이어진다. 직전 번호 행과 결합해 복원한다.
                    if pending_row and not re.match(r"^\d+\s*[.)]?", raw_name) and has_submission:
                        raw_name = f"{pending_row[0]} {raw_name}"
                        method = f"{pending_row[1]} {method}"
                        pending_row = None
                    elif re.match(r"^\d+\s*[.)]?", raw_name):
                        pending_row = None
                    # '기술하여야 한다'만 있는 회사소개/서술 항목은 제외한다.
                    if not has_submission:
                        if re.match(r"^\d+\s*[.)]?", raw_name):
                            pending_row = (raw_name, method)
                        continue
                    name = re.sub(r"^\s*\d+\s*[.)]?\s*", "", raw_name)
                    name = re.sub(r"제출$", "", name).strip()
                    if len(name) < 2 or name in seen:
                        continue
                    seen.add(name)
                    result.append({
                        "name": name,
                        "evidence": {"page": page_number, "bbox": tuple(float(value) for value in table.bbox), "text": method[:300]},
                        "confidence": "high",
                        "review_required": True,
                        "source": "rfp_submission_table",
                    })
    finally:
        document.close()
    return result
