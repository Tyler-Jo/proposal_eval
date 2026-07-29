"""Test 03: B권 목차·상단 ROI 기반 증빙서류 확인 POC (제품 src 미사용)."""

from __future__ import annotations

import gc
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest


INDEX_PATTERN = re.compile(r"^\s*(?P<id>[IVXLCDM]+|\d+(?:\.\d+)*|[가나다라마바사])(?:[.)])?\s+(?P<title>.+?)\s+(?P<page>\d{1,3})\s*$")


def _pdf_path() -> Path:
    value = os.environ.get("POC_B_DOCUMENT_PDF_PATH")
    if not value:
        pytest.skip("POC_B_DOCUMENT_PDF_PATH가 없어 Test 03 실제 PDF POC를 건너뜁니다.")
    path = Path(value).expanduser().resolve()
    if not path.is_file() or path.suffix.lower() != ".pdf":
        pytest.fail(f"유효한 PDF 경로가 아닙니다: {path}")
    return path


def parse_b_index(document: Any, max_pages: int = 15) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for page_number in range(1, min(document.page_count, max_pages) + 1):
        for line in document.load_page(page_number - 1).get_text().splitlines():
            match = INDEX_PATTERN.match(" ".join(line.split()))
            if match:
                entries.append({"section_id": match["id"], "title": match["title"], "declared_page": int(match["page"]), "index_page": page_number})
    return entries


def crop_top_roi(page: Any, fraction: float = 0.25) -> tuple[Any, list[float]]:
    """상단 영역을 잘라 POC artifact로 저장할 이미지와 PDF 좌표를 반환한다."""
    if not 0 < fraction <= 1:
        raise ValueError("ROI 비율은 0보다 크고 1 이하여야 합니다.")
    fitz = pytest.importorskip("fitz", reason="PyMuPDF가 필요합니다.")
    rect = page.rect
    roi = fitz.Rect(rect.x0, rect.y0, rect.x1, rect.y0 + rect.height * fraction)
    return page.get_pixmap(clip=roi, matrix=fitz.Matrix(1.5, 1.5), alpha=False), [roi.x0, roi.y0, roi.x1, roi.y1]


def top_roi_text(page: Any, fraction: float = 0.25) -> str:
    boundary = page.rect.y0 + page.rect.height * fraction
    blocks = page.get_text("blocks")
    return "\n".join(text for _x0, y0, _x1, _y1, text, *_ in blocks if y0 <= boundary)


def document_results(required_documents: list[str], entries: list[dict[str, Any]], document: Any) -> tuple[list[dict[str, Any]], list[tuple[int, Any, str]]]:
    results: list[dict[str, Any]] = []
    artifact_pages: list[tuple[int, Any, str]] = []
    for title in required_documents:
        candidates = [entry["declared_page"] for entry in entries if title.lower() in entry["title"].lower()]
        pages = sorted({page for declared in candidates for page in (declared - 1, declared, declared + 1) if 1 <= page <= document.page_count})
        found_page: int | None = None
        for page_number in pages:
            page = document.load_page(page_number - 1)
            text = top_roi_text(page)
            artifact_pages.append((page_number, page, title))
            if title.lower() in text.lower():
                found_page = page_number
                break
        results.append({"document": title, "status": "FOUND" if found_page else "MISSING", "page": found_page, "source": "ROI_TEXT_LAYER_BASELINE"})
    return results, artifact_pages


def merge_manual_override(results: list[dict[str, Any]], additions: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], float]:
    merged = {item["document"]: dict(item) for item in results}
    for addition in additions:
        merged[addition["document"]] = {**addition, "status": "FOUND", "source": "MANUAL_OVERRIDE"}
    values = list(merged.values())
    return values, -0.5 * sum(item["status"] == "MISSING" for item in values)


def _required_documents() -> list[str]:
    value = os.environ.get("POC_REQUIRED_DOCUMENTS", "적합등록필증,이행확약서,TTA인증서")
    return [item.strip() for item in value.split(",") if item.strip()]


@pytest.mark.poc
def test_03_manual_override_contract() -> None:
    base = [{"document": "이행확약서", "status": "MISSING", "page": None, "source": "ROI_TEXT_LAYER_BASELINE"}]
    merged, penalty = merge_manual_override(base, [{"document": "이행확약서", "page": 7}])
    assert merged[0]["status"] == "FOUND"
    assert merged[0]["source"] == "MANUAL_OVERRIDE"
    assert penalty == 0


@pytest.mark.poc
def test_03_b_index_and_top_roi_poc() -> None:
    fitz = pytest.importorskip("fitz", reason="PyMuPDF가 필요합니다.")
    pdf_path = _pdf_path()
    started = time.perf_counter()
    document = fitz.open(pdf_path)
    try:
        entries = parse_b_index(document)
        results, pages_for_artifact = document_results(_required_documents(), entries, document)
        output_dir = Path("tests/results") / f"test_03_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
        roi_dir = output_dir / "roi"
        roi_dir.mkdir(parents=True, exist_ok=True)
        saved_pages: list[dict[str, Any]] = []
        for page_number, page, title in pages_for_artifact[:30]:
            pixmap, bbox = crop_top_roi(page)
            image_name = f"page_{page_number}_{len(saved_pages) + 1}.png"
            pixmap.save(roi_dir / image_name)
            saved_pages.append({"document": title, "page": page_number, "bbox": bbox, "image": f"roi/{image_name}"})
    finally:
        document.close()
        gc.collect()

    merged, penalty = merge_manual_override(results, [])
    payload = {
        "test": "test_03_quant_b_documents_poc",
        "input_type": "pdf_text_layer_roi_baseline",
        "document_path": str(pdf_path),
        "elapsed_seconds": round(time.perf_counter() - started, 4),
        "toc_entries": entries,
        "document_results": merged,
        "manual_override_penalty": penalty,
        "roi_images": saved_pages,
    }
    (output_dir / "summary.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    assert all(item["status"] in {"FOUND", "MISSING"} for item in results)
    print(f"\n[Test 03] 목차 {len(entries)}건, 증빙 {len(results)}건, {payload['elapsed_seconds']}초")
    print(f"[Test 03] 결과 저장: {output_dir}")
