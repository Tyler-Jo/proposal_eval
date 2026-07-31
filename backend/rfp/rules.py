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
    # PDF 텍스트와 OCR은 "필수 항목", "미충족", "– 0.5점"처럼 공백과
    # 기호를 제각각 반환한다. 따라서 표현 자체보다 규칙의 의미 단어와 숫자를
    # 함께 확인한다. 여기서 잡은 규칙은 모두 RFP 원문 페이지를 함께 보존한다.
    patterns: list[tuple[str, str, re.Pattern[str], Callable[[re.Match[str]], tuple[float | None, float | None]]]] = [
        ("minimum_passing_score", "pass_threshold", re.compile(r"총\s*(\d+(?:\.\d+)?)\s*점\s*중\s*(\d+(?:\.\d+)?)\s*점\s*이상\s*득점\s*시\s*합격"), lambda m: (float(m.group(2)), float(m.group(1)))),
        ("minimum_passing_score", "pass_threshold", re.compile(r"(?:평가\s*)?점수\s*(\d+(?:\.\d+)?)\s*점\s*(?:만점|중).{0,70}?(\d+(?:\.\d+)?)\s*점\s*미만.{0,35}?불합격"), lambda m: (float(m.group(2)), float(m.group(1)))),
        ("required_item_failure", "disqualification_candidate", re.compile(r"필수\s*항목.{0,100}?미\s*충족.{0,50}?불합격"), lambda m: (None, None)),
        ("general_item_deduction", "deduction_candidate", re.compile(r"일반\s*항목.{0,120}?미\s*충족.{0,100}?(?:항목\s*당\s*)?(?:감점\s*(?:처리|적용)?\s*\(?\s*)?[–—-]\s*(\d+(?:\.\d+)?)\s*점"), lambda m: (-float(m.group(1)), None)),
        ("upper_specification_bonus", "bonus_candidate", re.compile(r"(?:항목\s*별\s*)?10\s*%\s*이상.{0,120}?가점.{0,100}?\+\s*(\d+(?:\.\d+)?)\s*점.{0,100}?최대\s*(\d+(?:\.\d+)?)\s*점"), lambda m: (float(m.group(1)), float(m.group(2)))),
        ("upper_specification_bonus", "bonus_candidate", re.compile(r"(?:항목\s*별\s*)?10\s*%\s*이상.{0,120}?가점.{0,100}?\+\s*(\d+(?:\.\d+)?)\s*점"), lambda m: (float(m.group(1)), None)),
        ("writing_guideline_deduction_cap", "deduction_candidate", re.compile(r"제안서.{0,100}?작성\s*기준.{0,100}?최대\s*[–—-]\s*(\d+(?:\.\d+)?)\s*점"), lambda m: (-float(m.group(1)), float(m.group(1)))),
    ]
    for page, raw_text in page_texts:
        text = _normalized(raw_text)
        for rule_type, effect, pattern, values in patterns:
            for match in pattern.finditer(text):
                value, cap = values(match)
                # 같은 페이지의 같은 효과/점수는 표현이 반복되어도 한 규칙이다.
                # 다만 작성기준처럼 점수가 서로 다른 규칙은 각각 남긴다.
                key = (page, rule_type, str(value))
                if key in seen:
                    continue
                seen.add(key)
                sequence += 1
                results.append(_rule(sequence, rule_type, effect, value, cap, page, text, match.start(), match.end()))
    return results
