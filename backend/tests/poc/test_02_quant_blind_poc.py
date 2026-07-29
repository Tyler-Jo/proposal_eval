"""Test 02: OCR 영역 결과를 이용한 블라인드 위반 탐지 POC.

제품 ``src/``에 의존하지 않는다. 실제 OCR 엔진이 반환해야 할 최소 계약
(``page``, ``text``, ``bbox``, ``location``)을 JSON fixture로 받아 업체명·대표자명
위반, whitelist, UI 검토 상태 결과를 검증한다.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any

import pytest


FIXTURE_DIR = Path(__file__).parents[1] / "fixtures" / "blind"
DEFAULT_RULES_PATH = FIXTURE_DIR / "rules.example.json"
DEFAULT_PAGES_PATH = FIXTURE_DIR / "pages.example.json"


def _load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as file:
        return json.load(file)


def _validate_bbox(value: Any) -> list[float]:
    if not isinstance(value, list) or len(value) != 4 or not all(isinstance(item, (int, float)) for item in value):
        raise ValueError(f"bbox는 네 숫자의 배열이어야 합니다: {value!r}")
    return [float(item) for item in value]


def detect_blind_violations(pages: list[dict[str, Any]], rule_set: dict[str, Any]) -> list[dict[str, Any]]:
    """OCR 영역에서 위반을 찾고 UI 수동 검토용 결과를 반환한다."""

    whitelist_patterns = [re.compile(pattern) for pattern in rule_set.get("whitelist_patterns", [])]
    findings: list[dict[str, Any]] = []

    for page_data in pages:
        page_number = page_data["page"]
        for region in page_data.get("regions", []):
            text = region["text"]
            if any(pattern.search(text) for pattern in whitelist_patterns):
                continue
            bbox = _validate_bbox(region["bbox"])
            for rule in rule_set["rules"]:
                for pattern_text in rule["patterns"]:
                    for match in re.finditer(pattern_text, text):
                        findings.append(
                            {
                                "page": page_number,
                                "rule_id": rule["rule_id"],
                                "detected_text": match.group(0),
                                "penalty_score": rule["penalty_score"],
                                "bbox": bbox,
                                "location": region.get("location", "unknown"),
                                "status": "PENDING_REVIEW",
                            }
                        )
                        break
                    else:
                        continue
                    break
    return findings


def _load_pages_from_environment() -> list[dict[str, Any]]:
    path_value = os.environ.get("POC_BLIND_PAGES_PATH")
    if not path_value:
        pytest.skip("POC_BLIND_PAGES_PATH가 없어 실제 OCR 출력 POC를 건너뜁니다.")
    path = Path(path_value).expanduser().resolve()
    if not path.is_file():
        pytest.fail(f"POC_BLIND_PAGES_PATH 파일을 찾을 수 없습니다: {path}")
    return _load_json(path)["pages"]


def _load_rules_from_environment() -> dict[str, Any]:
    path_value = os.environ.get("POC_BLIND_RULES_PATH")
    if not path_value:
        return _load_json(DEFAULT_RULES_PATH)
    path = Path(path_value).expanduser().resolve()
    if not path.is_file():
        pytest.fail(f"POC_BLIND_RULES_PATH 파일을 찾을 수 없습니다: {path}")
    return _load_json(path)


@pytest.mark.poc
def test_02_quant_blind_contract_poc() -> None:
    """업체명·대표자명 탐지와 whitelist·수동 검토 계약을 검증한다."""

    started_at = time.perf_counter()
    findings = detect_blind_violations(_load_json(DEFAULT_PAGES_PATH)["pages"], _load_json(DEFAULT_RULES_PATH))

    assert findings == [
        {
            "page": 12,
            "rule_id": "BLIND_VIOLATION_COMPANY",
            "detected_text": "(주)ABC통신",
            "penalty_score": -0.5,
            "bbox": [72.0, 130.0, 412.0, 156.0],
            "location": "body",
            "status": "PENDING_REVIEW",
        },
        {
            "page": 12,
            "rule_id": "BLIND_VIOLATION_CEO",
            "detected_text": "대표이사 홍길동",
            "penalty_score": -0.5,
            "bbox": [72.0, 690.0, 230.0, 716.0],
            "location": "footer",
            "status": "PENDING_REVIEW",
        },
    ]
    assert all(finding["status"] == "PENDING_REVIEW" for finding in findings)
    print(f"\n[Test 02 POC] {len(findings)}건 탐지, {time.perf_counter() - started_at:.4f}초")


@pytest.mark.poc
def test_02_quant_blind_external_ocr_output_poc() -> None:
    """실제 OCR JSON을 받아 후보 위반을 생성한다. 최종 판정은 사람이 검토한다."""

    started_at = time.perf_counter()
    findings = detect_blind_violations(_load_pages_from_environment(), _load_rules_from_environment())

    assert all(len(finding["bbox"]) == 4 for finding in findings)
    assert all(finding["status"] == "PENDING_REVIEW" for finding in findings)
    print(f"\n[Test 02 external POC] {len(findings)}건 후보, {time.perf_counter() - started_at:.4f}초")
