"""Test 05: 실제 PDF를 페이지 근거와 함께 로컬 vLLM으로 평가한다."""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from test_dashboard.document_evaluation import build_windows, choose_page_source, citation_pages, parse_rubric, score_rubric


VLLM_URL = os.environ.get("POC_VLLM_URL", "http://127.0.0.1:8000").rstrip("/")
MAX_INPUT_TOKENS = int(os.environ.get("POC_LLM_MAX_INPUT_TOKENS", "3000"))
OVERLAP_TOKENS = int(os.environ.get("POC_LLM_OVERLAP_TOKENS", "200"))
RUBRIC_JSON = os.environ.get("POC_LLM_RUBRIC_JSON", "")
OCR_MODE = os.environ.get("POC_LLM_OCR_MODE", "all")
TEXT_LAYER_MIN_CHARS = int(os.environ.get("POC_LLM_TEXT_LAYER_MIN_CHARS", "80"))
OCR_RENDER_SCALE = float(os.environ.get("POC_LLM_OCR_RENDER_SCALE", "1.5"))
PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _request(path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    request = urllib.request.Request(f"{VLLM_URL}{path}", method="POST" if payload is not None else "GET")
    if payload is not None:
        request.add_header("Content-Type", "application/json")
        request.data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
        pytest.fail(f"vLLM 서버 요청 실패({VLLM_URL}{path}): {error}")


def _model_id() -> str:
    configured = os.environ.get("POC_VLLM_MODEL")
    if configured:
        return configured
    models = _request("/v1/models").get("data", [])
    if not models or not models[0].get("id"):
        pytest.fail("vLLM /v1/models에서 모델 ID를 찾지 못했습니다.")
    return str(models[0]["id"])


def _tokens(model: str, text: str) -> list[int]:
    """vLLM tokenizer를 사용해 실제 토큰 경계로 예산을 계산한다."""

    payload = _request("/tokenize", {"model": model, "prompt": text})
    token_ids = payload.get("tokens") or payload.get("token_ids")
    if not isinstance(token_ids, list):
        pytest.fail(f"vLLM /tokenize 응답에 token 목록이 없습니다: {payload}")
    return [int(token) for token in token_ids]


def _pdf_path() -> Path:
    value = os.environ.get("POC_LLM_PDF_PATH")
    if not value:
        pytest.skip("POC_LLM_PDF_PATH가 없어 Test 05 실제 PDF 평가를 건너뜁니다.")
    path = Path(value).expanduser().resolve()
    if not path.is_file() or path.suffix.lower() != ".pdf":
        pytest.fail(f"유효한 PDF 경로가 아닙니다: {path}")
    return path


def _rubric() -> list[Any]:
    if not RUBRIC_JSON:
        pytest.fail("POC_LLM_RUBRIC_JSON에 배점표 JSON을 입력해야 합니다.")
    try:
        return parse_rubric(json.loads(RUBRIC_JSON))
    except (json.JSONDecodeError, ValueError) as error:
        pytest.fail(f"배점표 JSON이 올바르지 않습니다: {error}")


def _ocr_engine() -> Any:
    os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")
    pytest.importorskip("paddle", reason="PaddleOCR 실행에 PaddlePaddle이 필요합니다.")
    paddleocr = pytest.importorskip("paddleocr", reason="PaddleOCR 실행에 paddleocr가 필요합니다.")
    det = PROJECT_ROOT / "model" / "paddleocr" / "PP-OCRv5_mobile_det"
    rec = PROJECT_ROOT / "model" / "paddleocr" / "korean_PP-OCRv5_mobile_rec"
    if not det.is_dir() or not rec.is_dir():
        pytest.fail("Test 05 OCR용 로컬 PaddleOCR 모델 디렉터리를 찾을 수 없습니다.")
    return paddleocr.PaddleOCR(text_detection_model_name="PP-OCRv5_mobile_det", text_detection_model_dir=str(det), text_recognition_model_name="korean_PP-OCRv5_mobile_rec", text_recognition_model_dir=str(rec), use_doc_orientation_classify=False, use_doc_unwarping=False, use_textline_orientation=False)


def _extract_pages_with_ocr(pdf_path: Path) -> tuple[list[tuple[int, str]], list[dict[str, Any]]]:
    fitz = pytest.importorskip("fitz", reason="PDF 페이지 추출에 PyMuPDF가 필요합니다.")
    numpy = pytest.importorskip("numpy", reason="PaddleOCR 입력 변환에 numpy가 필요합니다.")
    document = fitz.open(pdf_path)
    engine: Any | None = None
    pages: list[tuple[int, str]] = []
    sources: list[dict[str, Any]] = []
    try:
        for number, page in enumerate(document, start=1):
            text_layer = page.get_text("text", sort=True).strip()
            source = choose_page_source(text_layer, OCR_MODE, TEXT_LAYER_MIN_CHARS)
            if source == "paddleocr":
                engine = engine or _ocr_engine()
                pixmap = page.get_pixmap(matrix=fitz.Matrix(OCR_RENDER_SCALE, OCR_RENDER_SCALE), alpha=False)
                image = numpy.frombuffer(pixmap.samples, dtype=numpy.uint8).reshape(pixmap.height, pixmap.width, pixmap.n)
                prediction = list(engine.predict(image))[0].json["res"]
                text = "\n".join(str(value) for value in prediction.get("rec_texts", []) if str(value).strip()).strip()
                del image, pixmap
            else:
                text = text_layer
            pages.append((number, text))
            sources.append({"page": number, "source": source, "text_char_count": len(text)})
    finally:
        document.close()
    return pages, sources


def _content_as_json(content: str) -> dict[str, Any]:
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip(), flags=re.IGNORECASE)
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError as error:
        pytest.fail(f"실제 LLM 응답이 JSON이 아닙니다: {error}; 응답={content!r}")
    if not isinstance(value, dict):
        pytest.fail("실제 LLM JSON 최상위 값은 객체여야 합니다.")
    return value


@pytest.mark.poc
def test_05_actual_vllm_gemma_document_evaluation_poc() -> None:
    model = _model_id()
    pdf_path = _pdf_path()
    started = time.perf_counter()
    extracted_pages, page_sources = _extract_pages_with_ocr(pdf_path)
    empty_pages = [page for page, text in extracted_pages if not text]
    rubric_results = score_rubric(extracted_pages, _rubric())
    for item in rubric_results:
        context_pages = [(page, text) for page, text in extracted_pages if page in item["evidence_pages"]] or extracted_pages[:1]
        window = build_windows(context_pages, lambda text: len(_tokens(model, text)), MAX_INPUT_TOKENS, OVERLAP_TOKENS)[0]
        prompt = f"""당신은 공공 제안서 심사 보조자입니다. 아래 배점표 항목의 규칙 계산 결과를 바꾸지 말고, 제공된 PDF 원문만 근거로 짧은 심사 코멘트를 작성하세요.
배점표 항목: {item['name']} (배점 {item['max_score']}점)
규칙 산정 결과: {item['score']}점 / 상태 {item['status']}
누락 필수 키워드: {', '.join(item['missing_keywords']) or '없음'}
반드시 JSON 객체만 출력하세요. 키는 comment(문자열), evidence(원문에서 그대로 복사한 300자 이하 발췌)입니다.

원문 페이지:
{window.text}
"""
        response = _request("/v1/chat/completions", {"model": model, "messages": [{"role": "system", "content": "JSON만 출력하는 평가 엔진입니다."}, {"role": "user", "content": prompt}], "temperature": 0, "max_tokens": 800})
        choices = response.get("choices", [])
        assert choices and isinstance(choices[0].get("message", {}).get("content"), str)
        result = _content_as_json(choices[0]["message"]["content"])
        assert isinstance(result.get("comment"), str) and result["comment"].strip()
        assert isinstance(result.get("evidence"), str) and result["evidence"].strip()
        pages = citation_pages(result["evidence"], window.pages)
        item.update({"comment": result["comment"], "evidence": result["evidence"], "comment_context_pages": window.page_range, "citation_pages": pages, "citation_status": "VERIFIED" if pages else "PENDING_REVIEW"})
    elapsed = time.perf_counter() - started
    result = {"score": sum(int(item["score"]) for item in rubric_results), "max_score": sum(int(item["max_score"]) for item in rubric_results)}
    output = Path("tests/results") / f"test_05_vllm_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = {"test": "test_05_actual_vllm_gemma_document_evaluation_poc", "server": VLLM_URL, "model": model, "document_path": str(pdf_path), "rubric": json.loads(RUBRIC_JSON), "ocr_mode": OCR_MODE, "ocr_render_scale": OCR_RENDER_SCALE, "page_count": len(extracted_pages), "empty_text_pages": empty_pages, "page_sources": page_sources, "max_input_tokens": MAX_INPUT_TOKENS, "overlap_tokens": OVERLAP_TOKENS, "citation_verified_items": sum(bool(item["citation_pages"]) for item in rubric_results), "citation_pending_review_items": sum(not item["citation_pages"] for item in rubric_results), "elapsed_seconds": round(elapsed, 4), "result": result, "item_results": rubric_results}
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[Test 05 vLLM] 모델 {model}, {len(extracted_pages)}페이지, 배점 {result['score']}/{result['max_score']}점, {elapsed:.2f}초")
    print(f"[Test 05 vLLM] 결과 저장: {output}")
