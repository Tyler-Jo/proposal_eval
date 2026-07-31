"""제안서 평가 POC 계약을 Tauri에서 사용할 수 있게 노출하는 로컬 API."""

from __future__ import annotations

import argparse
import json
import threading
import traceback
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from test_dashboard.document_evaluation import PageText, citation_pages, parse_rubric, score_rubric

from ocr import extract_pdf_pages_cached, extract_pdf_pages_with_ocr
from storage import LocalStore
from b_documents import find_required_documents
from rfp import (build_rfp_review_catalog, build_specification_requirements, extract_evaluation_rules,
                 extract_required_document_rows, extract_specification_source_rows, extract_specification_source_rows_from_text_pages)

from .local_llm import LOCAL_MODEL, LocalModelError


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _comment(item: dict[str, Any], pages: list[tuple[int, str]]) -> tuple[str, str, list[int]]:
    """LLM 준비 전에도 재현 가능한 설명과 검증된 원문 근거를 제공한다."""

    evidence_pages = list(item["evidence_pages"])
    if item["status"] == "MET":
        evidence = ""
        for page, text in pages:
            if page not in evidence_pages:
                continue
            index = min((text.casefold().find(keyword.casefold()) for keyword in item["required_keywords"] if text.casefold().find(keyword.casefold()) >= 0), default=-1)
            if index >= 0:
                evidence = text[max(0, index - 120):index + 240].strip()
                break
        return (f"필수 키워드 {', '.join(item['required_keywords'])}가 확인되어 배점이 반영되었습니다.", evidence, evidence_pages)
    missing = ", ".join(item["missing_keywords"])
    return (f"필수 키워드({missing})를 문서에서 찾지 못해 배점이 반영되지 않았습니다. 원문을 검토하세요.", "", [])


@dataclass
class Project:
    project_id: str
    name: str
    documents: dict[str, list[str]] = field(default_factory=lambda: {"RFP": [], "A": [], "B": []})
    rfp_analysis: dict[str, Any] | None = None
    created_at: str = field(default_factory=_now)


@dataclass
class Evaluation:
    evaluation_id: str
    project_id: str
    status: str = "RUNNING"
    result: dict[str, Any] | None = None
    error: str | None = None
    stage: str = "QUEUED"
    progress_message: str = "평가 대기 중"
    processing: dict[str, Any] = field(default_factory=dict)
    comment_status: str = "NOT_STARTED"
    comment_progress: int = 0
    created_at: str = field(default_factory=_now)
    finished_at: str | None = None


@dataclass
class BDocumentCheckJob:
    job_id: str
    status: str = "RUNNING"
    processing: dict[str, Any] = field(default_factory=dict)
    results: list[dict[str, Any]] | None = None
    error: str | None = None


@dataclass
class RfpAnalysisJob:
    job_id: str
    project_id: str
    status: str = "RUNNING"
    processing: dict[str, Any] = field(default_factory=dict)
    result: dict[str, Any] | None = None
    error: str | None = None


PROJECTS: dict[str, Project] = {}
EVALUATIONS: dict[str, Evaluation] = {}
BDOCUMENT_JOBS: dict[str, BDocumentCheckJob] = {}
RFP_ANALYSIS_JOBS: dict[str, RfpAnalysisJob] = {}
LOCK = threading.Lock()


def _project_payload(project: Project) -> dict[str, Any]:
    return {
        "id": project.project_id,
        "name": project.name,
        "documents": project.documents,
        "has_rfp_analysis": project.rfp_analysis is not None,
        "created_at": project.created_at,
    }


def _save_project(project: Project) -> None:
    LocalStore().save_project(
        project_id=project.project_id, name=project.name, created_at=project.created_at,
        documents=project.documents, rfp_analysis=project.rfp_analysis,
    )


def _restore_projects() -> None:
    for record in LocalStore().load_projects():
        project = Project(
            project_id=str(record["id"]), name=str(record["name"]),
            documents=dict(record["documents"]), rfp_analysis=record["rfp_analysis"],
            created_at=str(record["created_at"]),
        )
        PROJECTS[project.project_id] = project


def _analyze_rfp(document_path: str, on_progress: Any = None) -> dict[str, Any]:
    """RFP 체계규격 표를 심의용 구조화 규칙으로 변환한다."""

    store = LocalStore()
    # 규칙 추출 정규화/패턴이 바뀌면 기존 분석 JSON을 재사용하면 안 된다.
    # OCR 페이지 캐시는 그대로 재사용하므로 재분석 비용은 작다.
    cache_version = 5
    if cached := store.load_rfp_analysis(document_path, version=cache_version):
        if on_progress is not None:
            page_count = len(cached.get("page_sources", {}))
            on_progress(page_count, page_count, 0)
        cached["cache_hit"] = True
        return cached
    extraction = extract_pdf_pages_cached(document_path, mode="fallback", on_progress=on_progress)
    page_records = extraction.pages
    page_texts = [(page.page, page.text) for page in page_records]
    source_rows = extract_specification_source_rows(document_path)
    if not source_rows:
        source_rows = extract_specification_source_rows_from_text_pages(page_texts)
    requirements = build_specification_requirements(source_rows)
    evaluation_rules = extract_evaluation_rules(page_texts)
    submission_rows = extract_required_document_rows(document_path)
    review_catalog = build_rfp_review_catalog(page_texts, requirements, evaluation_rules, submission_rows)
    review_count = sum(item.review_required for item in requirements)
    result = {
        "document_path": document_path,
        "analyzed_at": _now(),
        "source_row_count": len(source_rows),
        "requirement_count": len(requirements),
        "review_required_count": review_count,
        "requirements": [item.to_dict() for item in requirements],
        "evaluation_rules": [rule.to_dict() for rule in evaluation_rules],
        "review_catalog": review_catalog,
        "page_sources": {str(page.page): page.source for page in page_records},
        "notice": (
            "체계규격 표 후보를 추출했습니다. 표식 또는 조건이 불명확한 항목은 검토 필요로 표시됩니다."
            if requirements
            else "체계규격 표 후보를 찾지 못했습니다. 텍스트 PDF의 표 구조 또는 OCR 결과를 확인하세요."
        ),
    }
    store.save_rfp_analysis(document_path, result, version=cache_version)
    result["cache_hit"] = False
    return result


def _run_rfp_analysis(job: RfpAnalysisJob, document_path: str) -> None:
    try:
        def progress(processed_pages: int, page_count: int, ocr_page_count: int) -> None:
            job.processing = {"processed_pages": processed_pages, "page_count": page_count, "ocr_page_count": ocr_page_count}

        result = _analyze_rfp(document_path, on_progress=progress)
        job.processing = {**job.processing, "cache_hit": bool(result.get("cache_hit"))}
        with LOCK:
            project = PROJECTS.get(job.project_id)
            if project is None:
                raise ValueError("사업을 찾을 수 없습니다.")
            project.rfp_analysis = result
            _save_project(project)
        job.result = result
        job.status = "COMPLETED"
    except Exception as error:
        job.status = "FAILED"
        job.error = str(error)
        print(traceback.format_exc())


def _evaluation_payload(evaluation: Evaluation) -> dict[str, Any]:
    return {"id": evaluation.evaluation_id, "project_id": evaluation.project_id, "status": evaluation.status, "result": evaluation.result, "error": evaluation.error, "stage": evaluation.stage, "progress_message": evaluation.progress_message, "processing": evaluation.processing, "comment_status": evaluation.comment_status, "comment_progress": evaluation.comment_progress, "created_at": evaluation.created_at, "finished_at": evaluation.finished_at}


def _adjustment_results(rules: Any, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """RFP의 명시 규칙을 실제 평가 항목과 연결하되, 불확실한 규칙은 확정하지 않는다."""

    if not isinstance(rules, list):
        return []
    general_missing = [item for item in items if item.get("importance") == "general" and item.get("status") == "MISSING"]
    required_missing = [item for item in items if item.get("importance") == "required" and item.get("status") == "MISSING"]
    total_score = sum(int(item.get("score", 0)) for item in items)

    def related_item(item: dict[str, Any], score_delta: float | None = None) -> dict[str, Any]:
        pages = list(item.get("citation_pages") or item.get("evidence_pages") or [])
        keywords = ", ".join(str(value) for value in item.get("missing_keywords") or item.get("required_keywords") or [])
        comparison = item.get("comparison") if isinstance(item.get("comparison"), dict) else None
        if comparison:
            proposal_basis = str(comparison.get("summary", ""))
            excerpt = str(item.get("evidence", ""))
        elif item.get("status") == "MISSING":
            proposal_basis = f"제안서에서 평가 항목 ‘{item.get('name', '')}’의 요구 표현({keywords or '원문 확인 필요'})을 확인하지 못했습니다."
            excerpt = ""
        else:
            proposal_basis = f"제안서 {', '.join(f'{page}쪽' for page in pages) or '원문'}에서 평가 항목 ‘{item.get('name', '')}’을 확인했습니다."
            excerpt = str(item.get("evidence", ""))
        return {
            "name": item["name"], "status": item["status"], "score_delta": score_delta,
            "evidence_pages": pages, "proposal_basis": proposal_basis, "proposal_excerpt": excerpt,
        }

    results: list[dict[str, Any]] = []
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        effect = str(rule.get("effect", ""))
        value = rule.get("value")
        try: amount = float(value) if value is not None else None
        except (TypeError, ValueError): amount = None
        related: list[dict[str, Any]] = []
        status, applied_delta = "REVIEW_REQUIRED", None
        rule_type = str(rule.get("rule_type", rule.get("name", "")))
        if effect == "deduction_candidate" and rule_type == "general_item_deduction" and amount is not None:
            related = [related_item(item, amount) for item in general_missing]
            status = "APPLIED" if related else "NOT_APPLIED"
            applied_delta = round(amount * len(related), 2)
        elif effect == "disqualification_candidate":
            related = [related_item(item) for item in required_missing]
            status = "TRIGGERED" if related else "NOT_APPLIED"
        elif effect == "pass_threshold" and amount is not None:
            passed = total_score >= amount
            related = [{
                "name": "정량 평가 합계", "status": "MET" if passed else "MISSING", "score_delta": None, "evidence_pages": [],
                "proposal_basis": f"제안서 평가 결과는 {total_score}점이며, RFP 합격 기준 {amount:g}점에 {'도달했습니다' if passed else '도달하지 못했습니다'}.",
                "proposal_excerpt": "",
            }]
            status = "NOT_APPLIED" if total_score >= amount else "TRIGGERED"
        results.append({
            "name": str(rule.get("name", "평가 규칙")), "rule_type": rule_type,
            "effect": effect, "value": amount, "cap": rule.get("cap"), "status": status, "applied_delta": applied_delta,
            "rfp_page": int(rule.get("evidence", {}).get("page", 0) or 0),
            "rfp_excerpt": str(rule.get("condition_summary", rule.get("evidence", {}).get("text", ""))), "related_items": related,
        })
    return results


def _run_evaluation(evaluation: Evaluation, document_path: str, rubric_payload: Any, render_scale: float = 1.5) -> None:
    try:
        evaluation.stage = "TEXT_EXTRACTION"
        evaluation.progress_message = "PDF 텍스트를 추출하고 필요한 페이지만 OCR 처리 중…"
        def update_extraction_progress(processed_pages: int, total_pages: int, ocr_page_count: int) -> None:
            evaluation.processing = {
                "processed_pages": processed_pages,
                "page_count": total_pages,
                "ocr_page_count": ocr_page_count,
                "cache_hit": False,
            }
            evaluation.progress_message = f"{total_pages}쪽 중 {processed_pages}쪽 처리 중… (OCR {ocr_page_count}쪽)"

        extraction = extract_pdf_pages_cached(document_path, mode="fallback", render_scale=render_scale, on_progress=update_extraction_progress)
        page_records = extraction.pages
        pages = [(page.page, page.text) for page in page_records]
        evaluation.processing = {"page_count": len(pages), "ocr_page_count": extraction.ocr_page_count, "cache_hit": extraction.cache_hit, "text_extraction_seconds": extraction.elapsed_seconds}
        if not any(text.strip() for _, text in pages):
            raise ValueError("텍스트와 OCR 결과를 추출하지 못했습니다. PDF 품질 또는 OCR 모델을 확인하세요.")
        evaluation.stage = "RULE_EVALUATION"
        evaluation.progress_message = "평가 기준을 적용하고 근거 페이지를 찾는 중…"
        rubric = parse_rubric(rubric_payload)
        items = score_rubric(pages, rubric)
        for item in items:
            comment, evidence, _ = _comment(item, pages)
            item["comment"] = comment
            item["evidence"] = evidence
            page_records = tuple(PageText(page, text, len(text)) for page, text in pages)
            item["citation_pages"] = citation_pages(evidence, page_records)
            item["comment_source"] = "rule_based_pending"
        score = sum(int(item["score"]) for item in items)
        maximum = sum(int(item["max_score"]) for item in items)
        evaluation.result = {
            "document_path": document_path,
            "page_count": len(pages),
            "result": {"score": score, "max_score": maximum},
            "item_results": items,
            "adjustment_results": _adjustment_results(rubric_payload.get("adjustment_rules") if isinstance(rubric_payload, dict) else None, items),
            "citation_verified_items": sum(bool(item["citation_pages"]) for item in items),
            "comment_source": "rule_based_pending",
            "notice": "규칙 기반 판정과 근거 페이지를 먼저 생성했습니다. AI 심사 코멘트는 필요할 때 별도로 생성할 수 있습니다.",
        }
        evaluation.status = "COMPLETED"
        evaluation.stage = "COMPLETED"
        evaluation.progress_message = "규칙 기반 평가 완료"
        LocalStore().save_evaluation(
            evaluation_id=evaluation.evaluation_id,
            project_id=evaluation.project_id,
            document_path=document_path,
            result=evaluation.result,
            finished_at=_now(),
        )
    except Exception as error:  # API boundary: UI가 에러를 표시할 수 있도록 보존한다.
        evaluation.status = "FAILED"
        evaluation.error = str(error)
        print(traceback.format_exc())
    finally:
        evaluation.finished_at = _now()


def _run_comments(evaluation: Evaluation) -> None:
    """규칙 판정 이후에만 느린 로컬 LLM 코멘트를 생성한다."""

    try:
        if evaluation.result is None:
            raise ValueError("완료된 평가 결과가 없습니다.")
        evaluation.comment_status = "RUNNING"
        items = evaluation.result["item_results"]
        pages = [(page.page, page.text) for page in extract_pdf_pages_cached(evaluation.result["document_path"], mode="fallback").pages]
        for index, item in enumerate(items, start=1):
            evaluation.progress_message = f"AI 심사 코멘트 생성 중… ({index}/{len(items)})"
            try:
                comment, evidence = LOCAL_MODEL.generate_comment(item, pages)
                item["comment"] = comment
                item["evidence"] = evidence
                item["citation_pages"] = citation_pages(evidence, tuple(PageText(page, text, len(text)) for page, text in pages))
                item["comment_source"] = "local_openvino_gemma"
            except LocalModelError as error:
                print(f"[sidecar] {error}")
                item["comment_source"] = "rule_based_fallback"
            evaluation.comment_progress = index
        evaluation.comment_status = "COMPLETED"
        evaluation.progress_message = "AI 심사 코멘트 생성 완료"
        evaluation.result["comment_source"] = "local_openvino_gemma" if all(item["comment_source"] == "local_openvino_gemma" for item in items) else "rule_based_fallback"
    except Exception as error:
        evaluation.comment_status = "FAILED"
        evaluation.error = str(error)
        print(traceback.format_exc())


def _run_b_document_check(job: BDocumentCheckJob, paths: list[str], documents: list[dict[str, Any]]) -> None:
    try:
        pages: list[tuple[int, str]] = []
        offset = 0
        total_pages = 0
        for path in paths:
            import fitz
            pdf = fitz.open(path); total_pages += pdf.page_count; pdf.close()
        for path in paths:
            def progress(done: int, total: int, ocr: int) -> None:
                job.processing = {"processed_pages": offset + done, "page_count": total_pages, "ocr_page_count": ocr}
            extracted = extract_pdf_pages_cached(path, mode="fallback", on_progress=progress)
            pages.extend((offset + page.page, page.text) for page in extracted.pages)
            offset += len(extracted.pages)
        job.results = find_required_documents(documents, pages)
        job.status = "COMPLETED"
    except Exception as error:
        job.status = "FAILED"; job.error = str(error); print(traceback.format_exc())


class Handler(BaseHTTPRequestHandler):
    server_version = "ProposalEvaluationSidecar/0.1"

    def log_message(self, format: str, *args: object) -> None:
        print(f"[sidecar] {self.address_string()} - {format % args}")

    def _json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(data)

    def _body(self) -> dict[str, Any]:
        size = int(self.headers.get("Content-Length", "0"))
        value = json.loads(self.rfile.read(size).decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError("JSON 객체가 필요합니다.")
        return value

    def _error(self, message: str, status: HTTPStatus = HTTPStatus.BAD_REQUEST) -> None:
        self._json({"error": message}, status)

    def _pdf(self, path: Path) -> None:
        data = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/pdf")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Content-Disposition", "inline")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(data)

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        parts = [part for part in urlparse(self.path).path.split("/") if part]
        if parts == ["health"]:
            self._json({"status": "ok", "service": "proposal-evaluation-sidecar"})
            return
        if len(parts) == 2 and parts[0] == "rfp-analysis":
            with LOCK:
                job = RFP_ANALYSIS_JOBS.get(parts[1])
            if job is None:
                self._error("RFP 분석 작업을 찾을 수 없습니다.", HTTPStatus.NOT_FOUND)
            else:
                self._json({"id": job.job_id, "status": job.status, "processing": job.processing, "result": job.result, "error": job.error})
            return
        if parts == ["projects"]:
            with LOCK:
                projects = [_project_payload(project) for project in sorted(PROJECTS.values(), key=lambda item: item.created_at, reverse=True)]
            self._json({"projects": projects})
            return
        if len(parts) == 2 and parts[0] == "projects":
            with LOCK:
                project = PROJECTS.get(parts[1])
            if project is None:
                self._error("사업을 찾을 수 없습니다.", HTTPStatus.NOT_FOUND)
            else:
                payload = _project_payload(project)
                payload["rfp_analysis"] = project.rfp_analysis
                self._json(payload)
            return
        if len(parts) == 3 and parts[0] == "projects" and parts[2] == "rfp-analysis":
            with LOCK:
                project = PROJECTS.get(parts[1])
            if project is None:
                self._error("사업을 찾을 수 없습니다.", HTTPStatus.NOT_FOUND)
            elif project.rfp_analysis is None:
                self._error("RFP 분석 결과가 없습니다.", HTTPStatus.NOT_FOUND)
            else:
                self._json(project.rfp_analysis)
            return
        if len(parts) == 2 and parts[0] == "evaluations":
            with LOCK:
                evaluation = EVALUATIONS.get(parts[1])
            if evaluation is None:
                self._error("평가 작업을 찾을 수 없습니다.", HTTPStatus.NOT_FOUND)
            else:
                self._json(_evaluation_payload(evaluation))
            return
        if len(parts) == 2 and parts[0] == "b-document-check":
            with LOCK:
                job = BDOCUMENT_JOBS.get(parts[1])
            if job is None: self._error("B권 확인 작업을 찾을 수 없습니다.", HTTPStatus.NOT_FOUND)
            else: self._json({"id": job.job_id, "status": job.status, "processing": job.processing, "results": job.results, "error": job.error})
            return
        if len(parts) == 3 and parts[0] == "evaluations" and parts[2] == "document":
            with LOCK:
                evaluation = EVALUATIONS.get(parts[1])
            document_path = evaluation.result.get("document_path") if evaluation and evaluation.result else None
            if not document_path or not Path(document_path).is_file():
                self._error("평가 원본 PDF를 찾을 수 없습니다.", HTTPStatus.NOT_FOUND)
            else:
                self._pdf(Path(document_path))
            return
        if len(parts) == 3 and parts[0] == "projects" and parts[2] == "b-document":
            with LOCK:
                project = PROJECTS.get(parts[1])
            path = project.documents["B"][0] if project and project.documents["B"] else None
            if not path or not Path(path).is_file(): self._error("B권 원본 PDF를 찾을 수 없습니다.", HTTPStatus.NOT_FOUND)
            else: self._pdf(Path(path))
            return
        if len(parts) == 3 and parts[0] == "projects" and parts[2] == "a-document":
            with LOCK:
                project = PROJECTS.get(parts[1])
            path = project.documents["A"][0] if project and project.documents["A"] else None
            if not path or not Path(path).is_file(): self._error("A권 원본 PDF를 찾을 수 없습니다.", HTTPStatus.NOT_FOUND)
            else: self._pdf(Path(path))
            return
        if len(parts) == 4 and parts[0] == "projects" and parts[2] == "b-page":
            with LOCK: project = PROJECTS.get(parts[1])
            path = project.documents["B"][0] if project and project.documents["B"] else None
            try: page_number = int(parts[3])
            except ValueError: page_number = 0
            if not path or page_number < 1: self._error("B권 페이지를 찾을 수 없습니다.", HTTPStatus.NOT_FOUND); return
            import fitz
            document = fitz.open(path)
            try:
                if page_number > document.page_count: self._error("B권 페이지를 찾을 수 없습니다.", HTTPStatus.NOT_FOUND); return
                data = document.load_page(page_number - 1).get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False).tobytes("png")
            finally: document.close()
            self.send_response(HTTPStatus.OK); self.send_header("Content-Type", "image/png"); self.send_header("Content-Length", str(len(data))); self.send_header("Access-Control-Allow-Origin", "*"); self.end_headers(); self.wfile.write(data)
            return
        if len(parts) == 4 and parts[0] == "projects" and parts[2] == "a-page":
            with LOCK: project = PROJECTS.get(parts[1])
            path = project.documents["A"][0] if project and project.documents["A"] else None
            try: page_number = int(parts[3])
            except ValueError: page_number = 0
            if not path or page_number < 1: self._error("A권 페이지를 찾을 수 없습니다.", HTTPStatus.NOT_FOUND); return
            import fitz
            document = fitz.open(path)
            try:
                if page_number > document.page_count: self._error("A권 페이지를 찾을 수 없습니다.", HTTPStatus.NOT_FOUND); return
                data = document.load_page(page_number - 1).get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False).tobytes("png")
            finally: document.close()
            self.send_response(HTTPStatus.OK); self.send_header("Content-Type", "image/png"); self.send_header("Content-Length", str(len(data))); self.send_header("Access-Control-Allow-Origin", "*"); self.end_headers(); self.wfile.write(data)
            return
        if len(parts) == 4 and parts[0] == "projects" and parts[2] == "rfp-page":
            with LOCK: project = PROJECTS.get(parts[1])
            path = project.documents["RFP"][0] if project and project.documents["RFP"] else None
            try: page_number = int(parts[3])
            except ValueError: page_number = 0
            if not path or page_number < 1: self._error("제안요청서 페이지를 찾을 수 없습니다.", HTTPStatus.NOT_FOUND); return
            import fitz
            document = fitz.open(path)
            try:
                if page_number > document.page_count: self._error("제안요청서 페이지를 찾을 수 없습니다.", HTTPStatus.NOT_FOUND); return
                data = document.load_page(page_number - 1).get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False).tobytes("png")
            finally: document.close()
            self.send_response(HTTPStatus.OK); self.send_header("Content-Type", "image/png"); self.send_header("Content-Length", str(len(data))); self.send_header("Access-Control-Allow-Origin", "*"); self.end_headers(); self.wfile.write(data)
            return
        self._error("찾을 수 없습니다.", HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802
        parts = [part for part in urlparse(self.path).path.split("/") if part]
        try:
            body = self._body()
            if parts == ["projects"]:
                name = str(body.get("name", "")).strip()
                if not name:
                    raise ValueError("사업명이 필요합니다.")
                project = Project(project_id=uuid.uuid4().hex, name=name)
                with LOCK:
                    PROJECTS[project.project_id] = project
                _save_project(project)
                self._json(_project_payload(project), HTTPStatus.CREATED)
                return
            if len(parts) == 3 and parts[0] == "projects" and parts[2] == "documents":
                kind = str(body.get("kind", ""))
                paths = body.get("paths")
                if kind not in {"RFP", "A", "B"} or not isinstance(paths, list):
                    raise ValueError("문서 종류(RFP, A, B)와 PDF 경로 목록이 필요합니다.")
                pdf_paths = [str(Path(value).expanduser().resolve()) for value in paths]
                if any(not Path(path).is_file() or Path(path).suffix.lower() != ".pdf" for path in pdf_paths):
                    raise ValueError("모든 문서는 존재하는 PDF 파일이어야 합니다.")
                with LOCK:
                    project = PROJECTS.get(parts[1])
                    if project is None:
                        raise ValueError("사업을 찾을 수 없습니다.")
                    project.documents[kind] = pdf_paths
                    _save_project(project)
                self._json(_project_payload(project))
                return
            if len(parts) == 3 and parts[0] == "projects" and parts[2] == "rfp-analysis":
                with LOCK:
                    project = PROJECTS.get(parts[1])
                    if project is None:
                        raise ValueError("사업을 찾을 수 없습니다.")
                    rfp_paths = list(project.documents["RFP"])
                if not rfp_paths:
                    raise ValueError("분석할 RFP PDF를 먼저 등록하세요.")
                job = RfpAnalysisJob(uuid.uuid4().hex, project.project_id)
                with LOCK:
                    RFP_ANALYSIS_JOBS[job.job_id] = job
                threading.Thread(target=_run_rfp_analysis, args=(job, rfp_paths[0]), daemon=True).start()
                self._json({"id": job.job_id, "status": job.status, "processing": job.processing, "result": None, "error": None}, HTTPStatus.ACCEPTED)
                return
            if len(parts) == 3 and parts[0] == "projects" and parts[2] == "b-document-check":
                with LOCK:
                    project = PROJECTS.get(parts[1])
                if project is None or project.rfp_analysis is None:
                    raise ValueError("RFP 분석을 먼저 완료하세요.")
                b_paths = project.documents["B"]
                if not b_paths:
                    raise ValueError("확인할 B권 PDF를 먼저 등록하세요.")
                documents = project.rfp_analysis["review_catalog"]["required_documents"]
                job = BDocumentCheckJob(uuid.uuid4().hex)
                with LOCK: BDOCUMENT_JOBS[job.job_id] = job
                threading.Thread(target=_run_b_document_check, args=(job, b_paths, documents), daemon=True).start()
                self._json({"id": job.job_id, "status": job.status, "processing": job.processing, "results": None}, HTTPStatus.ACCEPTED)
                return
            if len(parts) == 3 and parts[0] == "projects" and parts[2] == "evaluations":
                with LOCK:
                    project = PROJECTS.get(parts[1])
                if project is None:
                    self._error("사업을 찾을 수 없습니다.", HTTPStatus.NOT_FOUND)
                    return
                document_path = (project.documents["A"] or project.documents["B"] or project.documents["RFP"])
                if not document_path:
                    raise ValueError("평가할 A권 또는 B권 PDF를 먼저 등록하세요.")
                evaluation = Evaluation(evaluation_id=uuid.uuid4().hex, project_id=project.project_id)
                render_scale = float(body.get("ocr_render_scale", 1.5))
                if render_scale not in {1.0, 1.25, 1.5}:
                    raise ValueError("OCR 렌더 배율은 1.0, 1.25, 1.5 중 하나여야 합니다.")
                with LOCK:
                    EVALUATIONS[evaluation.evaluation_id] = evaluation
                threading.Thread(target=_run_evaluation, args=(evaluation, document_path[0], body.get("rubric"), render_scale), daemon=True).start()
                self._json(_evaluation_payload(evaluation), HTTPStatus.ACCEPTED)
                return
            if len(parts) == 3 and parts[0] == "evaluations" and parts[2] == "comments":
                with LOCK:
                    evaluation = EVALUATIONS.get(parts[1])
                if evaluation is None:
                    self._error("평가 작업을 찾을 수 없습니다.", HTTPStatus.NOT_FOUND)
                    return
                if evaluation.status != "COMPLETED":
                    raise ValueError("규칙 기반 평가가 완료된 뒤 AI 코멘트를 생성할 수 있습니다.")
                if evaluation.comment_status == "RUNNING":
                    self._json(_evaluation_payload(evaluation), HTTPStatus.ACCEPTED)
                    return
                evaluation.comment_progress = 0
                evaluation.comment_status = "RUNNING"
                threading.Thread(target=_run_comments, args=(evaluation,), daemon=True).start()
                self._json(_evaluation_payload(evaluation), HTTPStatus.ACCEPTED)
                return
            self._error("찾을 수 없습니다.", HTTPStatus.NOT_FOUND)
        except (ValueError, json.JSONDecodeError) as error:
            self._error(str(error))


def main() -> None:
    parser = argparse.ArgumentParser(description="Proposal Evaluation local sidecar")
    parser.add_argument("--port", type=int, default=8788)
    args = parser.parse_args()
    _restore_projects()
    print(f"Proposal evaluation sidecar: http://127.0.0.1:{args.port}")
    ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
