"""Test 02: 텍스트 PDF와 스캔 PDF를 모두 처리하는 블라인드 탐지 POC."""

from __future__ import annotations

import gc
import hashlib
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
RULES = (
    ("BLIND_VIOLATION_COMPANY", re.compile(r"(?:㈜|\(주\))\s*[가-힣A-Za-z0-9&·.-]+"), -0.5),
    ("BLIND_VIOLATION_CEO", re.compile(r"대표이사\s*[가-힣]{2,4}"), -0.5),
)


def _pdf_path() -> Path:
    value = os.environ.get("POC_BLIND_PDF_PATH")
    if not value:
        pytest.skip("POC_BLIND_PDF_PATH가 없어 하이브리드 블라인드 POC를 건너뜁니다.")
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


def _find_matches(page_number: int, text: str, bbox: list[float], source: str, confidence: float | None = None) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for rule_id, pattern, penalty in RULES:
        for match in pattern.finditer(text):
            findings.append({"page": page_number, "rule_id": rule_id, "detected_text": match.group(0), "penalty_score": penalty, "bbox": bbox, "location": source, "confidence": confidence, "status": "PENDING_REVIEW"})
    return findings


def _text_layer_findings(page: Any, page_number: int) -> tuple[list[dict[str, Any]], int]:
    words = page.get_text("words", sort=True)
    groups: dict[tuple[int, int], list[tuple[Any, ...]]] = {}
    for word in words:
        groups.setdefault((word[5], word[6]), []).append(word)
    findings: list[dict[str, Any]] = []
    for line in groups.values():
        text = " ".join(str(word[4]) for word in line)
        bbox = [round(min(float(word[0]) for word in line), 2), round(min(float(word[1]) for word in line), 2), round(max(float(word[2]) for word in line), 2), round(max(float(word[3]) for word in line), 2)]
        findings.extend(_find_matches(page_number, text, bbox, "pymupdf_text_layer"))
    return findings, len(words)


def _ocr_engine() -> Any:
    os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")
    pytest.importorskip("paddle")
    paddleocr = pytest.importorskip("paddleocr")
    det = PROJECT_ROOT / "model" / "paddleocr" / "PP-OCRv5_mobile_det"
    rec = PROJECT_ROOT / "model" / "paddleocr" / "korean_PP-OCRv5_mobile_rec"
    if not det.is_dir() or not rec.is_dir():
        pytest.fail("스캔 PDF용 로컬 PaddleOCR 모델을 찾을 수 없습니다.")
    return paddleocr.PaddleOCR(text_detection_model_name="PP-OCRv5_mobile_det", text_detection_model_dir=str(det), text_recognition_model_name="korean_PP-OCRv5_mobile_rec", text_recognition_model_dir=str(rec), use_doc_orientation_classify=False, use_doc_unwarping=False, use_textline_orientation=False)


def _ocr_findings(page: Any, page_number: int, engine: Any, fitz: Any, numpy: Any, scale: float) -> list[dict[str, Any]]:
    pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
    image = numpy.frombuffer(pixmap.samples, dtype=numpy.uint8).reshape(pixmap.height, pixmap.width, pixmap.n)
    result = list(engine.predict(image))[0].json["res"]
    findings: list[dict[str, Any]] = []
    for index, text in enumerate(result.get("rec_texts", [])):
        polygon = result.get("dt_polys", [])[index] if index < len(result.get("dt_polys", [])) else []
        if not polygon:
            continue
        xs, ys = [point[0] for point in polygon], [point[1] for point in polygon]
        bbox = [round(min(xs) / scale, 2), round(min(ys) / scale, 2), round(max(xs) / scale, 2), round(max(ys) / scale, 2)]
        scores = result.get("rec_scores", [])
        confidence = round(float(scores[index]), 4) if index < len(scores) else None
        findings.extend(_find_matches(page_number, str(text), bbox, "paddleocr_scan_page", confidence))
    del image, pixmap
    gc.collect()
    return findings


@pytest.mark.poc
def test_02_quant_blind_hybrid_poc() -> None:
    pdf_path = _pdf_path()
    fitz = pytest.importorskip("fitz", reason="PyMuPDF가 필요합니다.")
    numpy = pytest.importorskip("numpy", reason="numpy가 필요합니다.")
    scale = float(os.environ.get("POC_BLIND_OCR_RENDER_SCALE", "1.5"))
    min_words = int(os.environ.get("POC_BLIND_TEXT_LAYER_MIN_WORDS", "8"))
    document = fitz.open(pdf_path)
    engine: Any | None = None
    findings: list[dict[str, Any]] = []
    page_sources: list[dict[str, Any]] = []
    started = time.perf_counter()
    try:
        for number, page in enumerate(document, start=1):
            page_findings, word_count = _text_layer_findings(page, number)
            if word_count >= min_words:
                findings.extend(page_findings)
                page_sources.append({"page": number, "source": "pymupdf_text_layer", "word_count": word_count})
            else:
                engine = engine or _ocr_engine()
                findings.extend(_ocr_findings(page, number, engine, fitz, numpy, scale))
                page_sources.append({"page": number, "source": "paddleocr_scan_page", "word_count": word_count})
    finally:
        page_count = document.page_count
        document.close()
        gc.collect()
    output = PROJECT_ROOT / "tests" / "results" / f"test_02_hybrid_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({"test": "test_02_quant_blind_pdf_text_poc", "input_type": "hybrid_pymupdf_or_paddleocr", "document_path": str(pdf_path), "document_sha256": _sha256(pdf_path), "page_count": page_count, "elapsed_seconds": round(time.perf_counter() - started, 4), "page_sources": page_sources, "findings": findings}, ensure_ascii=False, indent=2), encoding="utf-8")
    assert page_count > 0
    assert all(len(item["bbox"]) == 4 for item in findings)
    print(f"\n[Test 02 hybrid] {page_count}페이지, OCR 페이지 {sum(item['source'] == 'paddleocr_scan_page' for item in page_sources)}건, 후보 {len(findings)}건")
    print(f"[Test 02 hybrid] 결과 저장: {output}")
