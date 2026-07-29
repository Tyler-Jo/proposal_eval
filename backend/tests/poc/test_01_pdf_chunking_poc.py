"""Test 01: 실제 PDF 청크 렌더링과 메모리 안정성 POC.

제품 ``src/`` 모듈을 사용하지 않는 독립 POC다. PyMuPDF(fitz)로 PDF 페이지를
렌더링하고, psutil로 청크별 RSS를 수집한다.
"""

from __future__ import annotations

import csv
import gc
import hashlib
import json
import os
import platform
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

import pytest


@dataclass(frozen=True)
class PocConfig:
    pdf_path: Path
    chunk_sizes: tuple[int, ...]
    repeat_count: int
    render_scale: float
    max_post_gc_growth_mb: float | None
    results_dir: Path


@dataclass(frozen=True)
class ChunkMetric:
    run_number: int
    chunk_size: int
    chunk_number: int
    start_page: int
    end_page: int
    rss_before_mb: float
    rss_peak_mb: float
    rss_after_gc_mb: float
    render_seconds: float
    processed_pages: int


def _parse_positive_ints(value: str, variable_name: str) -> tuple[int, ...]:
    try:
        values = tuple(int(item.strip()) for item in value.split(",") if item.strip())
    except ValueError as error:
        raise pytest.UsageError(f"{variable_name}에는 양의 정수 목록이 필요합니다: {value}") from error

    if not values or any(item <= 0 for item in values):
        raise pytest.UsageError(f"{variable_name}에는 하나 이상의 양의 정수가 필요합니다: {value}")
    return values


def _load_config() -> PocConfig:
    pdf_value = os.environ.get("POC_PDF_PATH")
    if not pdf_value:
        pytest.skip("POC_PDF_PATH가 설정되지 않아 실제 PDF POC를 건너뜁니다.")

    pdf_path = Path(pdf_value).expanduser().resolve()
    if not pdf_path.is_file():
        pytest.fail(f"POC_PDF_PATH 파일을 찾을 수 없습니다: {pdf_path}")
    if pdf_path.suffix.lower() != ".pdf":
        pytest.fail(f"POC_PDF_PATH는 PDF 파일이어야 합니다: {pdf_path}")

    repeat_value = os.environ.get("POC_REPEAT", "1")
    try:
        repeat_count = int(repeat_value)
    except ValueError as error:
        raise pytest.UsageError(f"POC_REPEAT에는 양의 정수가 필요합니다: {repeat_value}") from error
    if repeat_count <= 0:
        raise pytest.UsageError("POC_REPEAT는 1 이상이어야 합니다.")

    scale_value = os.environ.get("POC_RENDER_SCALE", "1.0")
    try:
        render_scale = float(scale_value)
    except ValueError as error:
        raise pytest.UsageError(f"POC_RENDER_SCALE에는 양수가 필요합니다: {scale_value}") from error
    if render_scale <= 0:
        raise pytest.UsageError("POC_RENDER_SCALE는 0보다 커야 합니다.")

    growth_value = os.environ.get("POC_MAX_POST_GC_GROWTH_MB")
    max_growth = float(growth_value) if growth_value else None
    if max_growth is not None and max_growth < 0:
        raise pytest.UsageError("POC_MAX_POST_GC_GROWTH_MB는 0 이상이어야 합니다.")

    return PocConfig(
        pdf_path=pdf_path,
        chunk_sizes=_parse_positive_ints(os.environ.get("POC_CHUNK_SIZES", "30,50"), "POC_CHUNK_SIZES"),
        repeat_count=repeat_count,
        render_scale=render_scale,
        max_post_gc_growth_mb=max_growth,
        results_dir=Path(os.environ.get("POC_RESULTS_DIR", "tests/results")).resolve(),
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _page_chunks(page_count: int, chunk_size: int) -> Iterator[range]:
    for start in range(0, page_count, chunk_size):
        yield range(start, min(start + chunk_size, page_count))


def _rss_mb(process: object) -> float:
    return round(process.memory_info().rss / (1024 * 1024), 2)


def _render_pdf_in_chunks(config: PocConfig, chunk_size: int, run_number: int) -> tuple[list[ChunkMetric], list[int], int]:
    fitz = pytest.importorskip("fitz", reason="PyMuPDF가 필요합니다. `pip install pymupdf` 후 실행하세요.")
    psutil = pytest.importorskip("psutil", reason="psutil이 필요합니다. `pip install psutil` 후 실행하세요.")

    process = psutil.Process()
    document = fitz.open(config.pdf_path)
    try:
        page_count = document.page_count
        if page_count == 0:
            pytest.fail("빈 PDF는 POC 입력으로 사용할 수 없습니다.")

        matrix = fitz.Matrix(config.render_scale, config.render_scale)
        metrics: list[ChunkMetric] = []
        processed_pages: list[int] = []

        for chunk_number, page_indexes in enumerate(_page_chunks(page_count, chunk_size), start=1):
            rss_before = _rss_mb(process)
            rss_peak = rss_before
            started_at = time.perf_counter()

            for page_index in page_indexes:
                page = document.load_page(page_index)
                # 실제 스캔 PDF와 비슷한 메모리 경로를 확인하기 위해 픽셀 렌더링까지 수행한다.
                pixmap = page.get_pixmap(matrix=matrix, alpha=False)
                processed_pages.append(page_index + 1)
                rss_peak = max(rss_peak, _rss_mb(process))
                del pixmap
                del page

            render_seconds = round(time.perf_counter() - started_at, 4)
            gc.collect()
            rss_after_gc = _rss_mb(process)
            metrics.append(
                ChunkMetric(
                    run_number=run_number,
                    chunk_size=chunk_size,
                    chunk_number=chunk_number,
                    start_page=page_indexes.start + 1,
                    end_page=page_indexes.stop,
                    rss_before_mb=rss_before,
                    rss_peak_mb=rss_peak,
                    rss_after_gc_mb=rss_after_gc,
                    render_seconds=render_seconds,
                    processed_pages=len(page_indexes),
                )
            )
    finally:
        document.close()

    gc.collect()
    return metrics, processed_pages, page_count


def _write_artifacts(config: PocConfig, metrics: list[ChunkMetric], metadata: dict[str, object]) -> Path:
    config.results_dir.mkdir(parents=True, exist_ok=True)
    run_id = datetime.now(timezone.utc).strftime("test_01_%Y%m%dT%H%M%SZ")
    output_dir = config.results_dir / run_id
    suffix = 1
    while output_dir.exists():
        suffix += 1
        output_dir = config.results_dir / f"{run_id}_{suffix}"
    output_dir.mkdir()

    with (output_dir / "chunks.csv").open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=list(asdict(metrics[0]).keys()))
        writer.writeheader()
        writer.writerows(asdict(metric) for metric in metrics)

    with (output_dir / "summary.json").open("w", encoding="utf-8") as file:
        json.dump(metadata, file, ensure_ascii=False, indent=2)
    return output_dir


@pytest.mark.poc
def test_01_pdf_chunking_memory_poc() -> None:
    """실제 PDF를 청크 렌더링해 페이지 무결성과 메모리 추이를 기록한다."""

    config = _load_config()
    all_metrics: list[ChunkMetric] = []
    expected_pages: list[int] | None = None
    page_count: int | None = None

    for chunk_size in config.chunk_sizes:
        for run_number in range(1, config.repeat_count + 1):
            metrics, processed_pages, current_page_count = _render_pdf_in_chunks(config, chunk_size, run_number)
            expected = list(range(1, current_page_count + 1))
            assert processed_pages == expected, f"청크 {chunk_size}, 반복 {run_number}: 페이지 누락 또는 순서 오류"
            assert len(processed_pages) == len(set(processed_pages)), "중복 페이지가 처리되었습니다."
            all_metrics.extend(metrics)
            expected_pages = expected
            page_count = current_page_count

            if config.max_post_gc_growth_mb is not None:
                growth_mb = metrics[-1].rss_after_gc_mb - metrics[0].rss_before_mb
                assert growth_mb <= config.max_post_gc_growth_mb, (
                    f"청크 {chunk_size}, 반복 {run_number}: GC 후 RSS 증가 {growth_mb:.2f}MB가 "
                    f"허용치 {config.max_post_gc_growth_mb:.2f}MB를 초과했습니다."
                )

    assert expected_pages is not None and page_count is not None
    metadata = {
        "test": "test_01_pdf_chunking_memory_poc",
        "document_path": str(config.pdf_path),
        "document_sha256": _sha256(config.pdf_path),
        "page_count": page_count,
        "expected_pages": expected_pages,
        "config": {
            "chunk_sizes": config.chunk_sizes,
            "repeat_count": config.repeat_count,
            "render_scale": config.render_scale,
            "max_post_gc_growth_mb": config.max_post_gc_growth_mb,
        },
        "environment": {
            "platform": platform.platform(),
            "python": sys.version,
        },
        "summary": {
            "max_rss_mb": max(metric.rss_peak_mb for metric in all_metrics),
            "total_render_seconds": round(sum(metric.render_seconds for metric in all_metrics), 4),
            "processed_pages": sum(metric.processed_pages for metric in all_metrics),
            "failed_pages": 0,
            "duplicate_pages": 0,
        },
        "chunks": [asdict(metric) for metric in all_metrics],
    }
    output_dir = _write_artifacts(config, all_metrics, metadata)
    print(f"\n[Test 01 POC] 결과 저장: {output_dir}")
