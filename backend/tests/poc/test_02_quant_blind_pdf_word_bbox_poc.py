"""Test 02: 실제 PDF의 단어 단위 bbox를 이용한 블라인드 탐지 POC."""

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


def _resolve_pdf_path() -> Path:
    value = os.environ.get("POC_BLIND_PDF_PATH")
    if not value:
        pytest.skip("POC_BLIND_PDF_PATH가 없어 실제 PDF 블라인드 POC를 건너뜁니다.")
    path = Path(value).expanduser().resolve()
    if not path.is_file() or path.suffix.lower() != ".pdf":
        pytest.fail(f"유효한 PDF 경로가 아닙니다: {path}")
    return path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _line_word_groups(page: Any) -> list[list[tuple[Any, ...]]]:
    groups: dict[tuple[int, int], list[tuple[Any, ...]]] = {}
    for word in page.get_text("words", sort=True):
        groups.setdefault((word[5], word[6]), []).append(word)
    return list(groups.values())


def _tight_bbox(words: list[tuple[Any, ...]], match: re.Match[str]) -> list[float]:
    """매칭 문자 범위와 겹치는 단어들만 합쳐 tight bbox를 만든다."""

    cursor = 0
    matched_words: list[tuple[Any, ...]] = []
    for word in words:
        text = str(word[4])
        start, end = cursor, cursor + len(text)
        if start < match.end() and end > match.start():
            matched_words.append(word)
        cursor = end + 1
    if not matched_words:
        raise ValueError(f"매칭 단어를 찾지 못했습니다: {match.group(0)!r}")
    return [
        round(min(float(word[0]) for word in matched_words), 2),
        round(min(float(word[1]) for word in matched_words), 2),
        round(max(float(word[2]) for word in matched_words), 2),
        round(max(float(word[3]) for word in matched_words), 2),
    ]


def _findings(pdf_path: Path) -> tuple[list[dict[str, Any]], int]:
    fitz = pytest.importorskip("fitz", reason="PyMuPDF가 필요합니다.")
    document = fitz.open(pdf_path)
    findings: list[dict[str, Any]] = []
    try:
        for page_number, page in enumerate(document, start=1):
            for words in _line_word_groups(page):
                line_text = " ".join(str(word[4]) for word in words)
                for rule_id, pattern, penalty_score in BLIND_RULES:
                    for match in pattern.finditer(line_text):
                        findings.append(
                            {
                                "page": page_number,
                                "rule_id": rule_id,
                                "detected_text": match.group(0),
                                "penalty_score": penalty_score,
                                "bbox": _tight_bbox(words, match),
                                "location": "pdf_text_layer_word_bbox",
                                "status": "PENDING_REVIEW",
                            }
                        )
        return findings, document.page_count
    finally:
        document.close()
        gc.collect()


def _write_result(pdf_path: Path, page_count: int, findings: list[dict[str, Any]], elapsed: float) -> Path:
    psutil = pytest.importorskip("psutil", reason="psutil이 필요합니다.")
    output_root = Path("tests/results")
    output_root.mkdir(parents=True, exist_ok=True)
    output = output_root / f"test_02_word_bbox_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    output.write_text(
        json.dumps(
            {
                # 기존 대시보드의 Test 02 결과 규약을 유지한다.
                "test": "test_02_quant_blind_pdf_text_poc",
                "input_type": "pdf_text_layer_word_bbox",
                "bbox_strategy": "matched_words_union",
                "document_path": str(pdf_path),
                "document_sha256": _sha256(pdf_path),
                "page_count": page_count,
                "elapsed_seconds": round(elapsed, 4),
                "rss_mb": round(psutil.Process().memory_info().rss / (1024 * 1024), 2),
                "environment": {"platform": platform.platform(), "python": sys.version},
                "findings": findings,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return output


@pytest.mark.poc
def test_02_quant_blind_pdf_word_bbox_poc() -> None:
    pdf_path = _resolve_pdf_path()
    started_at = time.perf_counter()
    findings, page_count = _findings(pdf_path)
    elapsed = time.perf_counter() - started_at

    assert page_count > 0
    assert findings, "블라인드 위반 후보를 찾지 못했습니다. 규칙 또는 입력을 검토하세요."
    assert all(len(finding["bbox"]) == 4 for finding in findings)
    result_path = _write_result(pdf_path, page_count, findings, elapsed)
    print(f"\n[Test 02 tight bbox] {page_count}페이지, 후보 {len(findings)}건, {elapsed:.4f}초")
    print(f"[Test 02 tight bbox] 결과 저장: {result_path}")
