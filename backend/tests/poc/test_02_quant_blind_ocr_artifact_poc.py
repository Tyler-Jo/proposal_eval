"""Test 02: 실제 OCR artifact의 text·bbox·confidence로 블라인드 후보를 만든다."""

from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest


RULES = (
    ("BLIND_VIOLATION_COMPANY", re.compile(r"(?:㈜|\(주\))\s*[가-힣A-Za-z0-9&·.-]+"), -0.5),
    ("BLIND_VIOLATION_CEO", re.compile(r"대표이사\s*[가-힣]{2,4}"), -0.5),
)


def _artifact_path() -> Path:
    value = os.environ.get("POC_OCR_ARTIFACT")
    if not value:
        pytest.skip("POC_OCR_ARTIFACT가 없어 OCR 연계 Test 02를 건너뜁니다.")
    path = Path(value).expanduser().resolve()
    if not path.is_file():
        pytest.fail(f"OCR artifact를 찾을 수 없습니다: {path}")
    return path


def blind_findings(artifact: dict[str, Any]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for page in artifact["pages"]:
        for region in page["regions"]:
            for rule_id, pattern, penalty in RULES:
                for match in pattern.finditer(region["text"]):
                    findings.append(
                        {
                            "page": page["page"],
                            "rule_id": rule_id,
                            "detected_text": match.group(0),
                            "confidence": region["confidence"],
                            "bbox": region["pdf_bbox"],
                            "status": "PENDING_REVIEW",
                            "penalty_score": penalty,
                            "source": "PADDLEOCR",
                        }
                    )
    return findings


@pytest.mark.poc
def test_02_quant_blind_from_actual_ocr_poc() -> None:
    started = time.perf_counter()
    artifact_path = _artifact_path()
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    findings = blind_findings(artifact)
    output_dir = Path("tests/results") / f"test_02_ocr_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    output_dir.mkdir(parents=True, exist_ok=True)
    payload = {"test": "test_02_quant_blind_ocr_poc", "ocr_artifact": str(artifact_path), "elapsed_seconds": round(time.perf_counter() - started, 4), "findings": findings}
    (output_dir / "summary.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    assert all(len(finding["bbox"]) == 4 for finding in findings)
    print(f"\n[Test 02 OCR] {len(artifact['pages'])}페이지, 후보 {len(findings)}건")
    print(f"[Test 02 OCR] 결과 저장: {output_dir}")
