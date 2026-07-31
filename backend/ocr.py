"""텍스트 레이어와 로컬 PaddleOCR을 결합하는 PDF 페이지 추출기.

PaddleOCR은 최초 스캔 PDF에서만 지연 로드한다. 따라서 텍스트형 PDF는
기존처럼 빠르게 처리하고, OCR 엔진이나 모델이 없을 때는 원인을 명확히 반환한다.
"""

from __future__ import annotations

import os
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Callable
from typing import Any

from storage import LocalStore


PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_DET_MODEL = PROJECT_ROOT / "model" / "paddleocr" / "PP-OCRv5_mobile_det"
DEFAULT_REC_MODEL = PROJECT_ROOT / "model" / "paddleocr" / "korean_PP-OCRv5_mobile_rec"
_ENGINE: Any | None = None
_ENGINE_LOCK = threading.Lock()
_PREDICT_LOCK = threading.Lock()
_DOCUMENT_CACHE: OrderedDict[tuple[str, int, int, str, int, float], "ExtractionResult"] = OrderedDict()
_CACHE_LOCK = threading.Lock()
_MAX_CACHED_DOCUMENTS = 4


class OcrUnavailableError(RuntimeError):
    """OCR 런타임 또는 로컬 모델이 준비되지 않은 경우."""


@dataclass(frozen=True)
class ExtractedPage:
    page: int
    text: str
    source: str


@dataclass(frozen=True)
class ExtractionResult:
    pages: tuple[ExtractedPage, ...]
    cache_hit: bool
    elapsed_seconds: float

    @property
    def ocr_page_count(self) -> int:
        return sum(page.source == "paddleocr" for page in self.pages)


def _engine() -> Any:
    global _ENGINE
    if _ENGINE is not None:
        return _ENGINE
    if not DEFAULT_DET_MODEL.is_dir() or not DEFAULT_REC_MODEL.is_dir():
        raise OcrUnavailableError("로컬 PaddleOCR 모델을 찾을 수 없습니다. backend/model/paddleocr 경로를 확인하세요.")
    # 온디바이스 실행에서는 모델 호스트 연결 확인도 하지 않는다.
    os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")
    # 일부 Intel CPU/oneDNN 조합에서 OCR 인식 추론이 빈 Tensor 오류로 종료된다.
    # 안정성이 우선인 심사 경로에서는 oneDNN을 사용하지 않는다.
    os.environ.setdefault("FLAGS_use_mkldnn", "0")
    try:
        import paddleocr
    except ImportError as error:
        raise OcrUnavailableError("PaddleOCR가 설치되지 않았습니다. backend에서 `uv sync --group dev`를 실행하세요.") from error
    with _ENGINE_LOCK:
        if _ENGINE is None:
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
        # Paddle/PaddleX static predictor는 동시에 호출하면 oneDNN Tensor 오류를
        # 낼 수 있다. 문서 작업은 병렬이어도 모델 추론은 하나씩 실행한다.
        with _PREDICT_LOCK:
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
    on_progress: Callable[[int, int, int], None] | None = None,
    existing_pages: dict[int, ExtractedPage] | None = None,
    on_page: Callable[[ExtractedPage], None] | None = None,
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
        ocr_page_count = 0
        total_pages = document.page_count
        for number, page in enumerate(document, start=1):
            if existing_pages is not None and (cached := existing_pages.get(number)) is not None:
                pages.append(cached)
                ocr_page_count += cached.source == "paddleocr"
                if on_progress is not None:
                    on_progress(number, total_pages, ocr_page_count)
                continue
            text = page.get_text("text", sort=True).strip()
            use_ocr = mode == "all" or (mode == "fallback" and len(text) < minimum_text_chars)
            if use_ocr:
                ocr_text = _ocr_page(page, render_scale).strip()
                pages.append(ExtractedPage(number, ocr_text, "paddleocr"))
                ocr_page_count += 1
            else:
                pages.append(ExtractedPage(number, text, "pymupdf_text_layer"))
            if on_page is not None:
                on_page(pages[-1])
            if on_progress is not None:
                on_progress(number, total_pages, ocr_page_count)
        return pages
    finally:
        document.close()


def extract_pdf_pages_cached(
    pdf_path: str,
    *,
    mode: str = "fallback",
    minimum_text_chars: int = 20,
    render_scale: float = 1.5,
    on_progress: Callable[[int, int, int], None] | None = None,
) -> ExtractionResult:
    """같은 원본의 텍스트/OCR 결과를 sidecar 실행 동안 재사용한다.

    파일 경로·크기·수정시각과 OCR 설정이 같을 때만 캐시를 사용한다. 원본이
    바뀌면 자동으로 새 추출 결과를 만든다.
    """

    path = Path(pdf_path).expanduser().resolve()
    stat = path.stat()
    key = (str(path), stat.st_size, stat.st_mtime_ns, mode, minimum_text_chars, render_scale)
    with _CACHE_LOCK:
        cached = _DOCUMENT_CACHE.get(key)
        if cached is not None:
            _DOCUMENT_CACHE.move_to_end(key)
            if on_progress is not None:
                on_progress(len(cached.pages), len(cached.pages), cached.ocr_page_count)
            return ExtractionResult(cached.pages, True, 0.0)
    store = LocalStore()
    persisted = store.load_extraction(path, mode=mode, minimum_text_chars=minimum_text_chars, render_scale=render_scale)
    if persisted is not None:
        result = ExtractionResult(tuple(ExtractedPage(number, text, source) for number, text, source in persisted), True, 0.0)
        if on_progress is not None:
            on_progress(len(result.pages), len(result.pages), result.ocr_page_count)
        with _CACHE_LOCK:
            _DOCUMENT_CACHE[key] = result
            _DOCUMENT_CACHE.move_to_end(key)
        return result
    import fitz
    document = fitz.open(path)
    try:
        total_pages = document.page_count
    finally:
        document.close()
    restored = {number: ExtractedPage(number, text, source) for number, text, source in store.load_partial_extraction(path, mode=mode, minimum_text_chars=minimum_text_chars, render_scale=render_scale)}
    pending: list[ExtractedPage] = []

    def persist_chunk(page: ExtractedPage) -> None:
        pending.append(page)
        if len(pending) >= 10:
            store.save_extraction_chunk(path, pending, mode=mode, minimum_text_chars=minimum_text_chars, render_scale=render_scale, page_count=total_pages)
            pending.clear()

    started = time.perf_counter()
    pages = tuple(extract_pdf_pages_with_ocr(str(path), mode=mode, minimum_text_chars=minimum_text_chars, render_scale=render_scale, on_progress=on_progress, existing_pages=restored, on_page=persist_chunk))
    if pending:
        store.save_extraction_chunk(path, pending, mode=mode, minimum_text_chars=minimum_text_chars, render_scale=render_scale, page_count=total_pages)
    store.mark_extraction_complete(path, mode=mode, minimum_text_chars=minimum_text_chars, render_scale=render_scale)
    result = ExtractionResult(pages, False, round(time.perf_counter() - started, 4))
    with _CACHE_LOCK:
        _DOCUMENT_CACHE[key] = result
        _DOCUMENT_CACHE.move_to_end(key)
        while len(_DOCUMENT_CACHE) > _MAX_CACHED_DOCUMENTS:
            _DOCUMENT_CACHE.popitem(last=False)
    return result
