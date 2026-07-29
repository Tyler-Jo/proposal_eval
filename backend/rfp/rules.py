"""RFP 본문의 명시적 평가·가감점·불합격 규칙 추출."""

from __future__ import annotations

import re
from collections.abc import Callable

from .models import EvaluationRule, EvidenceLocation


def _normalized(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _excerpt(text: str, start: int, end: int) -> str:
    return _normalized(text[max(0, start - 80) : min(len(text), end + 120)])[:400]


def _rule(sequence: int, rule_type: str, effect: str, value: float | None, cap: float | None, page: int, text: str, start: int, end: int) -> EvaluationRule:
    excerpt = _excerpt(text, start, end)
    return EvaluationRule(
        rule_id=f"rule-{page}-{sequence}", rule_type=rule_type, effect=effect, value=value, cap=cap,
        condition_summary=excerpt, confidence="high", review_required=False,
        evidence=EvidenceLocation(page=page, bbox=None, text=excerpt),
    )


def extract_evaluation_rules(page_texts: list[tuple[int, str]]) -> list[EvaluationRule]:
    """명시적 숫자와 효과가 함께 있는 규칙만 자동 구조화한다."""

    results: list[EvaluationRule] = []
    seen: set[tuple[int, str, str]] = set()
    sequence = 0
    patterns: list[tuple[str, str, re.Pattern[str], Callable[[re.Match[str]], tuple[float | None, float | None]]]] = [
        ("minimum_passing_score", "pass_threshold", re.compile(r"총\s*(\d+(?:\.\d+)?)\s*점\s*중\s*(\d+(?:\.\d+)?)\s*점\s*이상\s*득점\s*시\s*합격"), lambda m: (float(m.group(2)), float(m.group(1)))),
        ("required_item_failure", "disqualification_candidate", re.compile(r"필수항목.{0,80}?미\s*충족\s*시\s*불합격"), lambda m: (None, None)),
        ("general_item_deduction", "deduction_candidate", re.compile(r"일반항목.{0,100}?미\s*충족\s*시.{0,80}?항목\s*당\s*[–-]\s*(\d+(?:\.\d+)?)\s*점"), lambda m: (-float(m.group(1)), None)),
        ("upper_specification_bonus", "bonus_candidate", re.compile(r"10\s*%\s*이상.{0,100}?가점.{0,80}?\+\s*(\d+(?:\.\d+)?)\s*점.{0,80}?최대\s*(\d+(?:\.\d+)?)\s*점"), lambda m: (float(m.group(1)), float(m.group(2)))),
        ("writing_guideline_deduction_cap", "deduction_candidate", re.compile(r"제안서.{0,80}?작성기준.{0,80}?최대\s*[–-]\s*(\d+(?:\.\d+)?)\s*점"), lambda m: (-float(m.group(1)), float(m.group(1)))),
    ]
    for page, raw_text in page_texts:
        text = _normalized(raw_text)
        for rule_type, effect, pattern, values in patterns:
            for match in pattern.finditer(text):
                key = (page, rule_type, match.group(0))
                if key in seen:
                    continue
                seen.add(key)
                sequence += 1
                value, cap = values(match)
                results.append(_rule(sequence, rule_type, effect, value, cap, page, text, match.start(), match.end()))
    return results
