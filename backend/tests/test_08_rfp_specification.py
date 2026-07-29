from rfp.models import SpecificationSourceRow
from rfp.specification import build_specification_requirements, parse_specification_row


def test_08_required_bonus_comparison_is_structured() -> None:
    result = parse_specification_row(
        SpecificationSourceRow(page=12, item_number="2", category="메모리", raw_requirement="** 16GB 이상 [상위규격 제안시가점]")
    )

    assert result.importance == "required"
    assert result.bonus_eligible is True
    assert result.condition == {"type": "comparison", "operator": "gte", "value": 16.0, "unit": "GB", "text": "16GB 이상"}
    assert result.evidence.page == 12
    assert result.review_required is False


def test_08_numbered_required_marker_is_recognized() -> None:
    result = parse_specification_row(SpecificationSourceRow(page=1, raw_requirement="12. ** 16GB 이상"))

    assert result.importance == "required"
    assert result.raw_requirement == "16GB 이상"


def test_08_general_text_condition_requires_review() -> None:
    result = parse_specification_row(
        SpecificationSourceRow(page=2, item_number="6", category="OS", raw_requirement="∙ 시스템의 안정적 운용에 적합한 OS")
    )

    assert result.importance == "general"
    assert result.condition["type"] == "text"
    assert result.review_required is True
    assert result.confidence == "medium"


def test_08_or_and_parenthetical_conditions_are_preserved() -> None:
    cpu = parse_specification_row(
        SpecificationSourceRow(page=1, item_number="1", category="CPU", raw_requirement="** 2.6GHz 8Core 이상 2개 또는 2.4GHz 10Core 이상 2개")
    )
    storage = parse_specification_row(
        SpecificationSourceRow(page=1, item_number="3", category="저장장치", raw_requirement="** SSD 300GB × 2 이상(RAID 1 구성)")
    )

    assert cpu.condition["type"] == "any"
    assert len(cpu.condition["conditions"]) == 2
    assert cpu.review_required is True
    assert storage.condition["type"] == "all"
    assert storage.condition["conditions"][1]["text"] == "RAID 1 구성"
    assert storage.review_required is True


def test_08_unknown_marker_is_never_auto_approved() -> None:
    result = parse_specification_row(SpecificationSourceRow(page=3, raw_requirement="10 / 100 / 1000Mbps 2개 포트 이상"))

    assert result.importance == "unknown"
    assert result.review_required is True
    assert result.confidence == "review_required"


def test_08_build_requirements_keeps_each_source_evidence() -> None:
    results = build_specification_requirements(
        [
            SpecificationSourceRow(page=1, item_number="1", raw_requirement="** 16GB 이상"),
            SpecificationSourceRow(page=2, item_number="2", raw_requirement="∙ DVD-ROM"),
        ]
    )

    assert [item.evidence.page for item in results] == [1, 2]
    assert [item.importance for item in results] == ["required", "general"]
