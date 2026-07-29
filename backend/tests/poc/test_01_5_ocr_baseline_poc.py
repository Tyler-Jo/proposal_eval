"""Test 01.5: 로컬 PP-OCRv5 실제 OCR baseline POC.

PDF를 이미지로 렌더링한 뒤 로컬 detector/recognizer를 사용해
페이지별 text·bbox·confidence artifact를 만든다.
"""

from __future__ import annotations

import gc
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DET_MODEL = PROJECT_ROOT / "model" / "paddleocr" / "PP-OCRv5_mobile_det"
DEFAULT_REC_MODEL = PROJECT_ROOT / "model" / "paddleocr" / "korean_PP-OCRv5_mobile_rec"


def _parse_pages(value: str, page_count: int) -> list[int]:
    pages: set[int] = set()
    for part in value.split(","):
        part = part.strip()
        if "-" in part:
            start, end = (int(item) for item in part.split("-", 1))
            pages.update(range(start, end + 1))
        elif part:
            pages.add(int(part))
    selected = sorted(page for page in pages if 1 <= page <= page_count)
    if not selected:
        raise ValueError("OCR 대상 페이지가 없습니다.")
    return selected


def _bbox_from_polygon(polygon: list[list[float]], scale: float) -> dict[str, list[float]]:
    xs, ys = [point[0] for point in polygon], [point[1] for point in polygon]
    image_bbox = [min(xs), min(ys), max(xs), max(ys)]
    return {
        "image_bbox": [round(value, 2) for value in image_bbox],
        "pdf_bbox": [round(value / scale, 2) for value in image_bbox],
    }


def _ocr_engine(det_model_dir: Path, rec_model_dir: Path) -> Any:
    os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")
    pytest.importorskip("paddle")
    module = pytest.importorskip("paddleocr")
    if not det_model_dir.is_dir() or not rec_model_dir.is_dir():
        pytest.fail("로컬 OCR 모델 디렉터리를 찾을 수 없습니다.")
    return module.PaddleOCR(
        text_detection_model_name="PP-OCRv5_mobile_det",
        text_detection_model_dir=str(det_model_dir),
        text_recognition_model_name="korean_PP-OCRv5_mobile_rec",
        text_recognition_model_dir=str(rec_model_dir),
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        use_textline_orientation=False,
    )


@pytest.mark.poc
def test_01_5_local_paddleocr_baseline_poc() -> None:
    pdf_value = os.environ.get("POC_OCR_PDF_PATH")
    if not pdf_value:
        pytest.skip("POC_OCR_PDF_PATH가 없어 실제 OCR POC를 건너뜁니다.")
    pdf_path = Path(pdf_value).expanduser().resolve()
    if not pdf_path.is_file():
        pytest.fail(f"PDF를 찾을 수 없습니다: {pdf_path}")

    fitz = pytest.importorskip("fitz", reason="PyMuPDF가 필요합니다.")
    numpy = pytest.importorskip("numpy", reason="numpy가 필요합니다.")
    psutil = pytest.importorskip("psutil", reason="psutil이 필요합니다.")
    scale = float(os.environ.get("POC_OCR_RENDER_SCALE", "1.5"))
    document = fitz.open(pdf_path)
    try:
        page_numbers = _parse_pages(os.environ.get("POC_OCR_PAGES", "1-3"), document.page_count)
        engine = _ocr_engine(
            Path(os.environ.get("POC_OCR_DET_MODEL_DIR", str(DEFAULT_DET_MODEL))),
            Path(os.environ.get("POC_OCR_REC_MODEL_DIR", str(DEFAULT_REC_MODEL))),
        )
        output_dir = PROJECT_ROOT / "tests" / "results" / f"test_01_5_ocr_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
        image_dir = output_dir / "pages"
        image_dir.mkdir(parents=True, exist_ok=True)
        started = time.perf_counter()
        pages: list[dict[str, Any]] = []

        for page_number in page_numbers:
            page_started = time.perf_counter()
            pixmap = document.load_page(page_number - 1).get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
            image = numpy.frombuffer(pixmap.samples, dtype=numpy.uint8).reshape(pixmap.height, pixmap.width, pixmap.n)
            result = list(engine.predict(image))[0].json["res"]
            polygons = result.get("dt_polys", [])
            texts = result.get("rec_texts", [])
            scores = result.get("rec_scores", [])
            regions = [
                {
                    "text": text,
                    "confidence": round(float(scores[index]), 4) if index < len(scores) else None,
                    **_bbox_from_polygon(polygons[index], scale),
                }
                for index, text in enumerate(texts)
                if index < len(polygons)
            ]
            image_name = f"page_{page_number:03d}.png"
            pixmap.save(image_dir / image_name)
            pages.append({"page": page_number, "image": f"pages/{image_name}", "image_size": [pixmap.width, pixmap.height], "ocr_seconds": round(time.perf_counter() - page_started, 4), "regions": regions})
            del image, pixmap
            gc.collect()
    finally:
        document.close()

    payload = {
        "test": "test_01_5_local_paddleocr_baseline_poc",
        "engine": "PaddleOCR 3.4.1 / PaddlePaddle 3.2.2",
        "models": {"det": str(DEFAULT_DET_MODEL), "rec": str(DEFAULT_REC_MODEL)},
        "document_path": str(pdf_path),
        "render_scale": scale,
        "total_seconds": round(time.perf_counter() - started, 4),
        "rss_mb": round(psutil.Process().memory_info().rss / (1024 * 1024), 2),
        "pages": pages,
    }
    (output_dir / "summary.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    assert pages and all(page["regions"] for page in pages)
    print(f"\n[Test 01.5 OCR] {len(pages)}페이지, {sum(len(page['regions']) for page in pages)}개 영역, {payload['total_seconds']}초")
    print(f"[Test 01.5 OCR] 결과 저장: {output_dir}")
