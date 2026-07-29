"""RFP 체계규격 추출의 외부 계약 모델."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class EvidenceLocation:
    """원문에서 다시 확인할 수 있는 위치."""

    page: int
    bbox: tuple[float, float, float, float] | None
    text: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SpecificationSourceRow:
    """PDF 표 또는 텍스트에서 복원한 체계규격의 원시 행."""

    page: int
    raw_requirement: str
    category: str = ""
    item_number: str = ""
    equipment_name: str = ""
    quantity: str = ""
    bbox: tuple[float, float, float, float] | None = None


@dataclass(frozen=True)
class SpecificationRequirement:
    """심의 지원에 사용하는 구조화된 체계규격 항목."""

    requirement_id: str
    equipment_name: str
    item_number: str
    category: str
    raw_requirement: str
    importance: str
    bonus_eligible: bool
    quantity: str
    condition: dict[str, Any]
    confidence: str
    review_required: bool
    evidence: EvidenceLocation

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["evidence"] = self.evidence.to_dict()
        return value


@dataclass(frozen=True)
class EvaluationRule:
    """RFP 본문에 서술된 가점·감점·불합격 등 평가 규칙."""

    rule_id: str
    rule_type: str
    effect: str
    value: float | None
    cap: float | None
    condition_summary: str
    confidence: str
    review_required: bool
    evidence: EvidenceLocation

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["evidence"] = self.evidence.to_dict()
        return value
