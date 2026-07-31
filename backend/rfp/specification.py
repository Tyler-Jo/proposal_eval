"""체계규격 행의 필수·일반·가점 표식과 비교 조건을 구조화한다."""

from __future__ import annotations

import re
from typing import Any

from .models import EvidenceLocation, SpecificationRequirement, SpecificationSourceRow

_BONUS_MARKER = re.compile(r"\[\s*상위\s*규격\s*제안\s*시\s*가점\s*\]")
_NUMBER_UNIT = re.compile(
    r"(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>GHz|G㎐|MHz|Core|GB|TB|Mbps|ms|시간|개월|대|개|식|포트|회|%)"
)
_COMPARISON = re.compile(r"(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>GHz|G㎐|MHz|Core|GB|TB|Mbps|ms|시간|개월|대|개|식|포트|회|%)?\s*(?P<operator>이상|이하|미만|초과)")


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _unit(unit: str) -> str:
    return "GHz" if unit == "G㎐" else unit


def _importance(text: str) -> tuple[str, str]:
    stripped = text.lstrip()
    # Word 자동번호가 표식 앞에 분리되어 추출되는 경우: "12. ** 16GB 이상"
    stripped = re.sub(r"^(?:\d+\.\s*)+(?=(?:\*\*|[∙·]))", "", stripped)
    if stripped.startswith("**"):
        return "required", stripped[2:].lstrip()
    if stripped.startswith("∙") or stripped.startswith("·"):
        return "general", stripped[1:].lstrip()
    return "unknown", stripped


def _needs_human_review(requirement: str, condition: dict[str, Any], importance: str) -> bool:
    if importance == "unknown" or condition["type"] == "text":
        return True
    # 여러 숫자 축, 곱셈 수량 또는 괄호 구성은 단순 비교만으로 안전하게 판정할 수 없다.
    numeric_values = list(_NUMBER_UNIT.finditer(requirement))
    has_multiplication = bool(re.search(r"\d\s*(?:×|x)\s*\d", requirement, flags=re.IGNORECASE))
    return len(numeric_values) > 1 or has_multiplication or "(" in requirement


def _leaf_condition(text: str) -> dict[str, Any]:
    values = [{"value": float(match.group("value")), "unit": _unit(match.group("unit"))} for match in _NUMBER_UNIT.finditer(text)]
    trailing_operator = re.search(r"(이상|이하|미만|초과)(?:\s+\d+\s*개)?\s*$", text)
    if len(values) > 1 and trailing_operator:
        operator = {"이상": "gte", "이하": "lte", "미만": "lt", "초과": "gt"}[trailing_operator.group(1)]
        return {"type": "all", "conditions": [{"type": "comparison", "operator": operator, "value": value["value"], "unit": value["unit"], "text": f'{value["value"]:g}{value["unit"]} {trailing_operator.group(1)}'} for value in values], "text": _clean(text)}
    comparison = _COMPARISON.search(text)
    if comparison:
        operator = {"이상": "gte", "이하": "lte", "미만": "lt", "초과": "gt"}[comparison.group("operator")]
        unit = _unit(comparison.group("unit") or "")
        return {
            "type": "comparison",
            "operator": operator,
            "value": float(comparison.group("value")),
            "unit": unit,
            "text": _clean(text),
        }
    return {"type": "text", "text": _clean(text), "values": values}


def _condition(text: str) -> dict[str, Any]:
    """명시적인 또는/및 구조만 기계 판정 조건으로 승격한다."""

    normalized = _clean(text)
    alternatives = re.split(r"\s+(?:또는|혹은)\s+", normalized)
    if len(alternatives) > 1:
        return {"type": "any", "conditions": [_condition(part) for part in alternatives]}

    # 괄호 안의 구성 요구도 독립 조건으로 남긴다. 자연어 '및' 전체를 무리하게 분해하지 않는다.
    parenthetical = re.findall(r"\(([^()]+)\)", normalized)
    if parenthetical:
        main = re.sub(r"\s*\([^()]+\)", "", normalized).strip()
        return {
            "type": "all",
            "conditions": [_leaf_condition(main), *[_leaf_condition(part) for part in parenthetical]],
        }
    return _leaf_condition(normalized)


def parse_specification_row(source: SpecificationSourceRow, sequence: int = 1) -> SpecificationRequirement:
    """한 체계규격 행을 구조화한다.

    표식과 비교 연산자가 불명확한 행은 자동 판정 대상으로 만들지 않고 검토 대상으로 남긴다.
    """

    importance, requirement = _importance(source.raw_requirement)
    bonus_eligible = bool(_BONUS_MARKER.search(requirement))
    requirement = _BONUS_MARKER.sub("", requirement).strip()
    condition = _condition(requirement)
    review_required = _needs_human_review(requirement, condition, importance)
    confidence = "high" if importance != "unknown" and not review_required else "medium" if importance != "unknown" else "review_required"
    item_key = source.item_number.strip() or str(sequence)
    return SpecificationRequirement(
        requirement_id=f"spec-{source.page}-{item_key}-{sequence}",
        equipment_name=_clean(source.equipment_name),
        item_number=source.item_number.strip(),
        category=_clean(source.category),
        raw_requirement=requirement,
        importance=importance,
        bonus_eligible=bonus_eligible,
        quantity=_clean(source.quantity),
        condition=condition,
        confidence=confidence,
        review_required=review_required,
        evidence=EvidenceLocation(page=source.page, bbox=source.bbox, text=_clean(source.raw_requirement)),
    )


def build_specification_requirements(rows: list[SpecificationSourceRow]) -> list[SpecificationRequirement]:
    return [parse_specification_row(row, sequence=index) for index, row in enumerate(rows, start=1) if row.raw_requirement.strip()]
