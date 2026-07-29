"""Test 03: 상단 25% ROI에 실제 로컬 PaddleOCR을 적용하는 POC."""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest


ROOT = Path(__file__).resolve().parents[2]
DET = ROOT / "model/paddleocr/PP-OCRv5_mobile_det"
REC = ROOT / "model/paddleocr/korean_PP-OCRv5_mobile_rec"


def _required() -> list[str]:
    return [item.strip() for item in os.environ.get("POC_REQUIRED_DOCUMENTS", "적합등록필증,이행확약서,TTA인증서").split(",") if item.strip()]


def _engine() -> Any:
    os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")
    module = pytest.importorskip("paddleocr")
    return module.PaddleOCR(
        text_detection_model_name="PP-OCRv5_mobile_det", text_detection_model_dir=str(DET),
        text_recognition_model_name="korean_PP-OCRv5_mobile_rec", text_recognition_model_dir=str(REC),
        use_doc_orientation_classify=False, use_doc_unwarping=False, use_textline_orientation=False,
    )


@pytest.mark.poc
def test_03_top_roi_actual_ocr_poc() -> None:
    value = os.environ.get("POC_B_DOCUMENT_PDF_PATH")
    if not value:
        pytest.skip("POC_B_DOCUMENT_PDF_PATH가 없어 실제 ROI OCR POC를 건너뜁니다.")
    pdf_path = Path(value).expanduser().resolve()
    if not pdf_path.is_file():
        pytest.fail(f"PDF를 찾을 수 없습니다: {pdf_path}")
    fitz = pytest.importorskip("fitz")
    numpy = pytest.importorskip("numpy")
    started = time.perf_counter(); document = fitz.open(pdf_path)
    try:
        pages = [int(item) for item in os.environ.get("POC_B_OCR_PAGES", "1-3").replace("-", ",").split(",") if item.strip()]
        pages = sorted({page for page in pages if 1 <= page <= document.page_count})
        engine = _engine(); page_results: list[dict[str, Any]] = []
        for page_number in pages:
            page = document.load_page(page_number - 1); rect = page.rect
            roi = fitz.Rect(rect.x0, rect.y0, rect.x1, rect.y0 + rect.height * 0.25)
            pix = page.get_pixmap(clip=roi, matrix=fitz.Matrix(1.5, 1.5), alpha=False)
            image = numpy.frombuffer(pix.samples, dtype=numpy.uint8).reshape(pix.height, pix.width, pix.n)
            result = list(engine.predict(image))[0].json["res"]
            page_results.append({"page": page_number, "text": " ".join(result.get("rec_texts", [])), "region_count": len(result.get("rec_texts", []))})
    finally:
        document.close()
    results = [{"document": title, "status": "FOUND" if any(title.lower() in page["text"].lower() for page in page_results) else "MISSING", "source": "PADDLEOCR_TOP_25_ROI"} for title in _required()]
    output_dir = ROOT / "tests" / "results" / f"test_03_ocr_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"; output_dir.mkdir(parents=True, exist_ok=True)
    payload = {"test": "test_03_quant_b_documents_ocr_poc", "document_path": str(pdf_path), "elapsed_seconds": round(time.perf_counter() - started, 4), "roi_pages": page_results, "document_results": results}
    (output_dir / "summary.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    assert page_results and all(page["region_count"] >= 0 for page in page_results)
    print(f"\n[Test 03 OCR] ROI {len(page_results)}페이지, {sum(page['region_count'] for page in page_results)}개 영역")
    print(f"[Test 03 OCR] 결과 저장: {output_dir}")
