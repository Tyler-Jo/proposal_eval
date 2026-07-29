"""Test 04: A권 PDF의 목차/헤더 기반 섹션 구조화 POC (제품 src 미사용)."""

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


HEADER_PATTERN = re.compile(r"^\s*(?P<id>\d+(?:\.\d+)*|[IVXLCDM]+)(?:[.)])?\s+(?P<title>[^\n]{2,100})\s*$")


def _pdf_path() -> Path:
    value = os.environ.get("POC_SECTION_PDF_PATH")
    if not value:
        pytest.skip("POC_SECTION_PDF_PATH가 없어 Test 04 실제 PDF POC를 건너뜁니다.")
    path = Path(value).expanduser().resolve()
    if not path.is_file() or path.suffix.lower() != ".pdf":
        pytest.fail(f"유효한 PDF 경로가 아닙니다: {path}")
    return path


def _headers(page_text: str) -> list[tuple[str, str]]:
    return [(match["id"], match["title"].strip()) for line in page_text.splitlines() if (match := HEADER_PATTERN.match(" ".join(line.split())))]


def structure_sections(page_texts: list[str], fallback_keywords: list[str]) -> list[dict[str, Any]]:
    starts: list[tuple[int, str, str]] = []
    for page_number, text in enumerate(page_texts, start=1):
        starts.extend((page_number, section_id, title) for section_id, title in _headers(text))

    if not starts:
        for keyword in fallback_keywords:
            for page_number, text in enumerate(page_texts, start=1):
                if keyword in text:
                    starts.append((page_number, f"fallback-{len(starts) + 1}", f"{keyword} 관련 내용"))
                    break
        if not starts:
            starts.append((1, "fallback-1", "문서 전체"))

    sections: list[dict[str, Any]] = []
    for index, (start_page, section_id, title) in enumerate(starts):
        end_page = starts[index + 1][0] - 1 if index + 1 < len(starts) else len(page_texts)
        end_page = max(start_page, end_page)
        content = "\n".join(page_texts[start_page - 1 : end_page]).strip()
        sections.append({"section_id": section_id, "title": title, "content": content, "page_range": [start_page, end_page]})
    return sections


@pytest.mark.poc
def test_04_header_and_fallback_contract() -> None:
    sections = structure_sections(["1. 사업 개요\n내용", "1.1 추진 배경\n내용"], ["사업"])
    assert [(item["section_id"], item["page_range"]) for item in sections] == [("1", [1, 1]), ("1.1", [2, 2])]
    fallback = structure_sections(["일반 텍스트"], ["추진"])
    assert fallback[0]["section_id"] == "fallback-1"


@pytest.mark.poc
def test_04_section_structurer_pdf_poc() -> None:
    fitz = pytest.importorskip("fitz", reason="PyMuPDF가 필요합니다.")
    pdf_path = _pdf_path()
    started = time.perf_counter()
    document = fitz.open(pdf_path)
    try:
        page_texts = [page.get_text() for page in document]
    finally:
        document.close()
        gc.collect()
    keywords = [item.strip() for item in os.environ.get("POC_SECTION_KEYWORDS", "사업,추진,성과,지원").split(",") if item.strip()]
    sections = structure_sections(page_texts, keywords)
    output_dir = Path("tests/results") / f"test_04_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    output_dir.mkdir(parents=True, exist_ok=True)
    payload = {"test": "test_04_section_structurer_poc", "document_path": str(pdf_path), "page_count": len(page_texts), "elapsed_seconds": round(time.perf_counter() - started, 4), "sections": sections}
    (output_dir / "summary.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    assert sections
    assert all(section["page_range"][0] <= section["page_range"][1] for section in sections)
    print(f"\n[Test 04] {len(page_texts)}페이지, 섹션 {len(sections)}개, {payload['elapsed_seconds']}초")
    print(f"[Test 04] 결과 저장: {output_dir}")
