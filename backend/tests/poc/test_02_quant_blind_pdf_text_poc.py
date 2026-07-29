"""Test 02: 실제 PDF 텍스트 레이어 기반 블라인드 탐지 baseline POC.

스캔 OCR 엔진을 붙이기 전, PDF의 텍스트 블록과 좌표를 OCR 출력과 같은 형태로
변환해 블라인드 규칙이 실제 제안서에서 후보를 생성하는지 확인한다.
"""

from __future__ import annotations

import gc
import hashlib
import json
import os
import platform
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest


BLIND_RULES = (
    ("BLIND_VIOLATION_COMPANY", re.compile(r"(?:㈜|\(주\))\s*[가-힣A-Za-z0-9&·.-]+"), -0.5),
    ("BLIND_VIOLATION_CEO", re.compile(r"대표이사\s*[가-힣]{2,4}"), -0.5),
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _resolve_pdf_path() -> Path:
    path_value = os.environ.get("POC_BLIND_PDF_PATH")
    if not path_value:
        pytest.skip("POC_BLIND_PDF_PATH가 없어 실제 PDF 블라인드 POC를 건너뜁니다.")
    path = Path(path_value).expanduser().resolve()
    if not path.is_file() or path.suffix.lower() != ".pdf":
        pytest.fail(f"유효한 PDF 경로가 아닙니다: {path}")
    return path


def _findings_from_pdf(pdf_path: Path) -> tuple[list[dict[str, Any]], int]:
    fitz = pytest.importorskip("fitz", reason="PyMuPDF가 필요합니다.")
    findings: list[dict[str, Any]] = []
    document = fitz.open(pdf_path)
    try:
        for page_index, page in enumerate(document, start=1):
            for x0, y0, x1, y1, text, *_ in page.get_text("blocks"):
                normalized_text = " ".join(text.split())
                for rule_id, pattern, penalty_score in BLIND_RULES:
                    for match in pattern.finditer(normalized_text):
                        findings.append(
                            {
                                "page": page_index,
                                "rule_id": rule_id,
                                "detected_text": match.group(0),
                                "penalty_score": penalty_score,
                                "bbox": [round(x0, 2), round(y0, 2), round(x1, 2), round(y1, 2)],
                                "location": "pdf_text_layer",
                                "status": "PENDING_REVIEW",
                            }
                        )
        return findings, document.page_count
    finally:
        document.close()
        gc.collect()


def _write_result(pdf_path: Path, page_count: int, findings: list[dict[str, Any]], elapsed_seconds: float) -> Path:
    psutil = pytest.importorskip("psutil", reason="psutil이 필요합니다.")
    output_root = Path("tests/results")
    output_root.mkdir(parents=True, exist_ok=True)
    output_path = output_root / f"test_02_pdf_text_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    report = {
        "test": "test_02_quant_blind_pdf_text_poc",
        "input_type": "pdf_text_layer_baseline",
        "document_path": str(pdf_path),
        "document_sha256": _sha256(pdf_path),
        "page_count": page_count,
        "elapsed_seconds": round(elapsed_seconds, 4),
        "rss_mb": round(psutil.Process().memory_info().rss / (1024 * 1024), 2),
        "environment": {"platform": platform.platform(), "python": sys.version},
        "findings": findings,
    }
    with output_path.open("w", encoding="utf-8") as file:
        json.dump(report, file, ensure_ascii=False, indent=2)
    return output_path


@pytest.mark.poc
def test_02_quant_blind_pdf_text_baseline_poc() -> None:
    """실제 문서에서 업체/대표자 노출 후보와 UI 하이라이트 좌표를 생성한다."""

    pdf_path = _resolve_pdf_path()
    started_at = time.perf_counter()
    findings, page_count = _findings_from_pdf(pdf_path)
    elapsed_seconds = time.perf_counter() - started_at

    assert page_count > 0
    assert findings, "제안서에서 블라인드 위반 후보를 찾지 못했습니다. 규칙 또는 입력을 검토하세요."
    assert all(len(finding["bbox"]) == 4 for finding in findings)
    assert all(finding["status"] == "PENDING_REVIEW" for finding in findings)

    result_path = _write_result(pdf_path, page_count, findings, elapsed_seconds)
    print(f"\n[Test 02 PDF baseline] {page_count}페이지, 후보 {len(findings)}건, {elapsed_seconds:.4f}초")
    print(f"[Test 02 PDF baseline] 결과 저장: {result_path}")
