from rfp.rules import extract_evaluation_rules


def test_11_extracts_explicit_rfp_evaluation_rules_with_evidence() -> None:
    pages = [(3, "총 100점 중 90점 이상 득점 시 합격으로 판정한다. 필수항목 1개 이상 미 충족 시 불합격. 일반항목 미 충족 시 항목 당 – 0.5점. 항목별 10% 이상 상위규격 제안 시 가점(+0.3점, 최대 6점)을 적용한다.")]

    rules = extract_evaluation_rules(pages)

    assert [(r.rule_type, r.effect, r.value, r.cap) for r in rules] == [
        ("minimum_passing_score", "pass_threshold", 90.0, 100.0),
        ("required_item_failure", "disqualification_candidate", None, None),
        ("general_item_deduction", "deduction_candidate", -0.5, None),
        ("upper_specification_bonus", "bonus_candidate", 0.3, 6.0),
    ]
    assert all(rule.evidence.page == 3 for rule in rules)


def test_11_extracts_writing_deduction_cap() -> None:
    rules = extract_evaluation_rules([(8, "제안서 분량 및 작성기준 미 준수 시 감점 처리 : 최대 –3.5점")])

    assert len(rules) == 1
    assert rules[0].rule_type == "writing_guideline_deduction_cap"
    assert rules[0].value == -3.5
    assert rules[0].cap == 3.5
