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

from ocr import OcrUnavailableError, extract_pdf_pages_with_ocr
from rfp import (build_rfp_review_catalog, build_specification_requirements, extract_evaluation_rules,
                 extract_required_document_rows, extract_specification_source_rows, extract_specification_source_rows_from_text_pages)

from .local_llm import LOCAL_MODEL, LocalModelError


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _comment(item: dict[str, Any], pages: list[tuple[int, str]]) -> tuple[str, str, list[int]]:
    """LLM 준비 전에도 재현 가능한 설명과 검증된 원문 근거를 제공한다."""

    evidence_pages = list(item["evidence_pages"])
    if item["status"] == "MET":
        evidence = next((text for page, text in pages if page in evidence_pages and text.strip()), "")[:300]
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
    created_at: str = field(default_factory=_now)
    finished_at: str | None = None


PROJECTS: dict[str, Project] = {}
EVALUATIONS: dict[str, Evaluation] = {}
LOCK = threading.Lock()


def _project_payload(project: Project) -> dict[str, Any]:
    return {
        "id": project.project_id,
        "name": project.name,
        "documents": project.documents,
        "has_rfp_analysis": project.rfp_analysis is not None,
        "created_at": project.created_at,
    }


def _analyze_rfp(document_path: str) -> dict[str, Any]:
    """RFP 체계규격 표를 심의용 구조화 규칙으로 변환한다."""

    page_records = extract_pdf_pages_with_ocr(document_path, mode="fallback")
    page_texts = [(page.page, page.text) for page in page_records]
    source_rows = extract_specification_source_rows(document_path)
    if not source_rows:
        source_rows = extract_specification_source_rows_from_text_pages(page_texts)
    requirements = build_specification_requirements(source_rows)
    evaluation_rules = extract_evaluation_rules(page_texts)
    submission_rows = extract_required_document_rows(document_path)
    review_catalog = build_rfp_review_catalog(page_texts, requirements, evaluation_rules, submission_rows)
    review_count = sum(item.review_required for item in requirements)
    return {
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


def _evaluation_payload(evaluation: Evaluation) -> dict[str, Any]:
    return {"id": evaluation.evaluation_id, "project_id": evaluation.project_id, "status": evaluation.status, "result": evaluation.result, "error": evaluation.error, "created_at": evaluation.created_at, "finished_at": evaluation.finished_at}


def _run_evaluation(evaluation: Evaluation, document_path: str, rubric_payload: Any) -> None:
    try:
        page_records = extract_pdf_pages_with_ocr(document_path, mode="fallback")
        pages = [(page.page, page.text) for page in page_records]
        if not any(text.strip() for _, text in pages):
            raise ValueError("텍스트와 OCR 결과를 추출하지 못했습니다. PDF 품질 또는 OCR 모델을 확인하세요.")
        rubric = parse_rubric(rubric_payload)
        items = score_rubric(pages, rubric)
        comment_sources: list[str] = []
        for item in items:
            try:
                comment, evidence = LOCAL_MODEL.generate_comment(item, pages)
                comment_source = "local_openvino_gemma"
            except LocalModelError as error:
                print(f"[sidecar] {error}")
                comment, evidence, _ = _comment(item, pages)
                comment_source = "rule_based_fallback"
            item["comment"] = comment
            item["evidence"] = evidence
            page_records = tuple(PageText(page, text, len(text)) for page, text in pages)
            item["citation_pages"] = citation_pages(evidence, page_records)
            item["comment_source"] = comment_source
            comment_sources.append(comment_source)
        score = sum(int(item["score"]) for item in items)
        maximum = sum(int(item["max_score"]) for item in items)
        evaluation.result = {
            "document_path": document_path,
            "page_count": len(pages),
            "result": {"score": score, "max_score": maximum},
            "item_results": items,
            "citation_verified_items": sum(bool(item["citation_pages"]) for item in items),
            "comment_source": "local_openvino_gemma" if all(source == "local_openvino_gemma" for source in comment_sources) else "rule_based_fallback",
            "notice": "로컬 OpenVINO Gemma가 심사 코멘트와 인용을 생성했습니다. 점수와 근거 페이지는 배점표 규칙으로 산정됩니다." if all(source == "local_openvino_gemma" for source in comment_sources) else "로컬 모델을 사용할 수 없어 규칙 기반 코멘트를 표시합니다. 점수와 근거 페이지는 배점표 규칙으로 산정되었습니다.",
        }
        evaluation.status = "COMPLETED"
    except Exception as error:  # API boundary: UI가 에러를 표시할 수 있도록 보존한다.
        evaluation.status = "FAILED"
        evaluation.error = str(error)
        print(traceback.format_exc())
    finally:
        evaluation.finished_at = _now()


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
        if len(parts) == 2 and parts[0] == "projects":
            with LOCK:
                project = PROJECTS.get(parts[1])
            if project is None:
                self._error("사업을 찾을 수 없습니다.", HTTPStatus.NOT_FOUND)
            else:
                self._json(_project_payload(project))
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
                self._json(_project_payload(project), HTTPStatus.CREATED)
                return
            if len(parts) == 3 and parts[0] == "projects" and parts[2] == "documents":
                kind = str(body.get("kind", ""))
                paths = body.get("paths")
                if kind not in {"RFP", "A", "B"} or not isinstance(paths, list) or not paths:
                    raise ValueError("문서 종류(RFP, A, B)와 PDF 경로가 필요합니다.")
                pdf_paths = [str(Path(value).expanduser().resolve()) for value in paths]
                if any(not Path(path).is_file() or Path(path).suffix.lower() != ".pdf" for path in pdf_paths):
                    raise ValueError("모든 문서는 존재하는 PDF 파일이어야 합니다.")
                with LOCK:
                    project = PROJECTS.get(parts[1])
                    if project is None:
                        raise ValueError("사업을 찾을 수 없습니다.")
                    project.documents[kind] = pdf_paths
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
                analysis = _analyze_rfp(rfp_paths[0])
                with LOCK:
                    project.rfp_analysis = analysis
                self._json(analysis)
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
                with LOCK:
                    EVALUATIONS[evaluation.evaluation_id] = evaluation
                threading.Thread(target=_run_evaluation, args=(evaluation, document_path[0], body.get("rubric")), daemon=True).start()
                self._json(_evaluation_payload(evaluation), HTTPStatus.ACCEPTED)
                return
            self._error("찾을 수 없습니다.", HTTPStatus.NOT_FOUND)
        except (ValueError, json.JSONDecodeError) as error:
            self._error(str(error))


def main() -> None:
    parser = argparse.ArgumentParser(description="Proposal Evaluation local sidecar")
    parser.add_argument("--port", type=int, default=8788)
    args = parser.parse_args()
    print(f"Proposal evaluation sidecar: http://127.0.0.1:{args.port}")
    ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
