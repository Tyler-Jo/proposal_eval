"""텍스트 레이어와 로컬 PaddleOCR을 결합하는 PDF 페이지 추출기.

PaddleOCR은 최초 스캔 PDF에서만 지연 로드한다. 따라서 텍스트형 PDF는
기존처럼 빠르게 처리하고, OCR 엔진이나 모델이 없을 때는 원인을 명확히 반환한다.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_DET_MODEL = PROJECT_ROOT / "model" / "paddleocr" / "PP-OCRv5_mobile_det"
DEFAULT_REC_MODEL = PROJECT_ROOT / "model" / "paddleocr" / "korean_PP-OCRv5_mobile_rec"
_ENGINE: Any | None = None


class OcrUnavailableError(RuntimeError):
    """OCR 런타임 또는 로컬 모델이 준비되지 않은 경우."""


@dataclass(frozen=True)
class ExtractedPage:
    page: int
    text: str
    source: str


def _engine() -> Any:
    global _ENGINE
    if _ENGINE is not None:
        return _ENGINE
    if not DEFAULT_DET_MODEL.is_dir() or not DEFAULT_REC_MODEL.is_dir():
        raise OcrUnavailableError("로컬 PaddleOCR 모델을 찾을 수 없습니다. backend/model/paddleocr 경로를 확인하세요.")
    # 온디바이스 실행에서는 모델 호스트 연결 확인도 하지 않는다.
    os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")
    try:
        import paddleocr
    except ImportError as error:
        raise OcrUnavailableError("PaddleOCR가 설치되지 않았습니다. backend에서 `uv sync --group dev`를 실행하세요.") from error
    _ENGINE = paddleocr.PaddleOCR(
        text_detection_model_name="PP-OCRv5_mobile_det",
        text_detection_model_dir=str(DEFAULT_DET_MODEL),
        text_recognition_model_name="korean_PP-OCRv5_mobile_rec",
        text_recognition_model_dir=str(DEFAULT_REC_MODEL),
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        use_textline_orientation=False,
    )
    return _ENGINE


def _ocr_page(page: Any, render_scale: float) -> str:
    """한 PDF 페이지를 렌더링해 OCR 읽기 순서의 텍스트로 변환한다."""

    try:
        import numpy
    except ImportError as error:
        raise OcrUnavailableError("OCR용 numpy가 설치되지 않았습니다. backend에서 `uv sync --group dev`를 실행하세요.") from error
    pixmap = page.get_pixmap(matrix=__import__("fitz").Matrix(render_scale, render_scale), alpha=False)
    try:
        image = numpy.frombuffer(pixmap.samples, dtype=numpy.uint8).reshape(pixmap.height, pixmap.width, pixmap.n)
        result = list(_engine().predict(image))[0].json.get("res", {})
        return "\n".join(str(text).strip() for text in result.get("rec_texts", []) if str(text).strip())
    finally:
        del pixmap


def extract_pdf_pages_with_ocr(
    pdf_path: str,
    *,
    mode: str = "fallback",
    minimum_text_chars: int = 20,
    render_scale: float = 1.5,
) -> list[ExtractedPage]:
    """페이지별 텍스트를 반환한다.

    ``fallback``은 텍스트 레이어가 부족한 페이지만 OCR하고, ``all``은 모든
    페이지를 OCR하며, ``text``는 OCR을 사용하지 않는다.
    """

    if mode not in {"fallback", "all", "text"}:
        raise ValueError("OCR 모드는 fallback, all, text 중 하나여야 합니다.")
    if minimum_text_chars < 0 or render_scale <= 0:
        raise ValueError("minimum_text_chars는 0 이상, render_scale은 0보다 커야 합니다.")
    import fitz

    document = fitz.open(pdf_path)
    try:
        pages: list[ExtractedPage] = []
        for number, page in enumerate(document, start=1):
            text = page.get_text("text", sort=True).strip()
            use_ocr = mode == "all" or (mode == "fallback" and len(text) < minimum_text_chars)
            if use_ocr:
                ocr_text = _ocr_page(page, render_scale).strip()
                pages.append(ExtractedPage(number, ocr_text, "paddleocr"))
            else:
                pages.append(ExtractedPage(number, text, "pymupdf_text_layer"))
        return pages
    finally:
        document.close()
