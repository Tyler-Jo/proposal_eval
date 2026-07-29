"""외부 의존성 없는 Test/POC 로컬 웹 대시보드 서버."""

from __future__ import annotations

import json
import mimetypes
import os
import subprocess
import sys
import threading
import traceback
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
STATIC_ROOT = Path(__file__).resolve().parent / "static"
RESULTS_ROOT = PROJECT_ROOT / "tests" / "results"
FIXTURES_PDF_ROOT = PROJECT_ROOT / "tests" / "fixtures" / "pdf"


@dataclass
class Job:
    job_id: str
    test_id: str
    command: list[str]
    environment: dict[str, str]
    status: str = "RUNNING"
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    finished_at: str | None = None
    return_code: int | None = None
    output: str = ""


JOBS: dict[str, Job] = {}
JOBS_LOCK = threading.Lock()


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _safe_pdf_path(value: str) -> Path:
    path = (PROJECT_ROOT / value).resolve() if not Path(value).is_absolute() else Path(value).resolve()
    if not path.is_file() or path.suffix.lower() != ".pdf" or not _is_relative_to(path, FIXTURES_PDF_ROOT):
        raise ValueError("PDF는 tests/fixtures/pdf/ 아래의 PDF여야 합니다.")
    return path


def _load_run(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

    test_name = payload.get("test")
    if test_name not in {"test_01_pdf_chunking_memory_poc", "test_02_quant_blind_pdf_text_poc"}:
        return None
    run_id = path.parent.name if path.name == "summary.json" else path.stem
    return {
        "id": run_id,
        "test": test_name,
        "created_at": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(),
        "path": str(path.relative_to(RESULTS_ROOT)),
        "summary": payload.get("summary", {}),
        "page_count": payload.get("page_count"),
        "finding_count": len(payload.get("findings", [])),
    }


def list_runs() -> list[dict[str, Any]]:
    if not RESULTS_ROOT.exists():
        return []
    runs: list[dict[str, Any]] = []
    for path in RESULTS_ROOT.rglob("*.json"):
        run = _load_run(path)
        if run:
            runs.append(run)
    return sorted(runs, key=lambda item: item["created_at"], reverse=True)


def load_run(run_id: str) -> dict[str, Any]:
    for path in RESULTS_ROOT.rglob("*.json"):
        run = _load_run(path)
        if run and run["id"] == run_id:
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["id"] = run_id
            payload["result_path"] = run["path"]
            return payload
    raise FileNotFoundError(run_id)


def _job_payload(job: Job) -> dict[str, Any]:
    return {
        "job_id": job.job_id,
        "test_id": job.test_id,
        "status": job.status,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
        "return_code": job.return_code,
        "output": job.output,
    }


def _run_job(job: Job) -> None:
    try:
        completed = subprocess.run(
            job.command,
            cwd=PROJECT_ROOT,
            env={**os.environ, **job.environment},
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        job.return_code = completed.returncode
        job.output = completed.stdout[-20_000:]
        job.status = "PASSED" if completed.returncode == 0 else "FAILED"
    except Exception:
        job.status = "FAILED"
        job.output = traceback.format_exc()
    finally:
        job.finished_at = datetime.now(timezone.utc).isoformat()
        if job.status == "FAILED":
            RESULTS_ROOT.mkdir(parents=True, exist_ok=True)
            failure = {"test": "dashboard_job_failure", **_job_payload(job)}
            (RESULTS_ROOT / f"dashboard_job_{job.job_id}.json").write_text(
                json.dumps(failure, ensure_ascii=False, indent=2), encoding="utf-8"
            )


def create_job(request: dict[str, Any]) -> Job:
    test_id = request.get("test_id")
    pdf_path = _safe_pdf_path(str(request.get("pdf_path", "")))
    command = [sys.executable, "-m", "pytest", "-m", "poc"]
    environment: dict[str, str]

    if test_id == "test_01":
        chunk_sizes = str(request.get("chunk_sizes", "30,50"))
        repeat = str(request.get("repeat", "1"))
        render_scale = str(request.get("render_scale", "1.0"))
        command += ["tests/poc/test_01_pdf_chunking_poc.py", "-s"]
        environment = {
            "POC_PDF_PATH": str(pdf_path),
            "POC_CHUNK_SIZES": chunk_sizes,
            "POC_REPEAT": repeat,
            "POC_RENDER_SCALE": render_scale,
        }
    elif test_id == "test_02":
        command += ["tests/poc/test_02_quant_blind_pdf_text_poc.py", "-s"]
        environment = {"POC_BLIND_PDF_PATH": str(pdf_path)}
    else:
        raise ValueError("지원하지 않는 테스트입니다.")

    job_id = datetime.now(timezone.utc).strftime("job_%Y%m%dT%H%M%S%fZ")
    job = Job(job_id=job_id, test_id=test_id, command=command, environment=environment)
    with JOBS_LOCK:
        JOBS[job_id] = job
    threading.Thread(target=_run_job, args=(job,), daemon=True).start()
    return job


class DashboardHandler(BaseHTTPRequestHandler):
    server_version = "ProposalEvaluationTestDashboard/0.1"

    def log_message(self, format: str, *args: object) -> None:
        print(f"[dashboard] {self.address_string()} - {format % args}")

    def _json(self, payload: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _error(self, message: str, status: HTTPStatus = HTTPStatus.BAD_REQUEST) -> None:
        self._json({"error": message}, status)

    def _serve_file(self, path: Path, content_type: str | None = None) -> None:
        if not path.is_file():
            self._error("찾을 수 없습니다.", HTTPStatus.NOT_FOUND)
            return
        data = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type or mimetypes.guess_type(path.name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _page_info(self, run_id: str, page_number: int) -> None:
        try:
            run = load_run(run_id)
            pdf_path = Path(run["document_path"])
            if not pdf_path.is_file():
                raise FileNotFoundError(pdf_path)
            import fitz

            document = fitz.open(pdf_path)
            try:
                if page_number < 1 or page_number > document.page_count:
                    self._error("유효하지 않은 페이지입니다.", HTTPStatus.NOT_FOUND)
                    return
                page = document.load_page(page_number - 1)
                rect = page.rect
                self._json(
                    {
                        "page": page_number,
                        "width": rect.width,
                        "height": rect.height,
                        "image_url": f"/api/runs/{urllib.parse.quote(run_id)}/pages/{page_number}.png",
                    }
                )
            finally:
                document.close()
        except FileNotFoundError:
            self._error("입력 PDF를 찾을 수 없습니다.", HTTPStatus.NOT_FOUND)

    def _page_image(self, run_id: str, page_number: int) -> None:
        try:
            run = load_run(run_id)
            pdf_path = Path(run["document_path"])
            if not pdf_path.is_file():
                raise FileNotFoundError(pdf_path)
            import fitz

            document = fitz.open(pdf_path)
            try:
                if page_number < 1 or page_number > document.page_count:
                    self._error("유효하지 않은 페이지입니다.", HTTPStatus.NOT_FOUND)
                    return
                pixmap = document.load_page(page_number - 1).get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False)
                data = pixmap.tobytes("png")
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            finally:
                document.close()
        except FileNotFoundError:
            self._error("입력 PDF를 찾을 수 없습니다.", HTTPStatus.NOT_FOUND)

    def do_GET(self) -> None:  # noqa: N802
        path = urllib.parse.urlparse(self.path).path
        parts = [urllib.parse.unquote(part) for part in path.split("/") if part]
        if path == "/" or path == "/index.html":
            self._serve_file(STATIC_ROOT / "index.html", "text/html; charset=utf-8")
        elif path in {"/app.js", "/styles.css"}:
            self._serve_file(STATIC_ROOT / path.lstrip("/"))
        elif path == "/api/fixtures":
            fixtures = [str(item.relative_to(PROJECT_ROOT)) for item in sorted(FIXTURES_PDF_ROOT.glob("*.pdf"))]
            self._json({"fixtures": fixtures})
        elif path == "/api/runs":
            self._json({"runs": list_runs()})
        elif len(parts) == 3 and parts[:2] == ["api", "jobs"]:
            with JOBS_LOCK:
                job = JOBS.get(parts[2])
            if job is None:
                self._error("작업을 찾을 수 없습니다.", HTTPStatus.NOT_FOUND)
            else:
                self._json(_job_payload(job))
        elif len(parts) == 3 and parts[:2] == ["api", "runs"]:
            try:
                self._json(load_run(parts[2]))
            except FileNotFoundError:
                self._error("실행 결과를 찾을 수 없습니다.", HTTPStatus.NOT_FOUND)
        elif len(parts) == 6 and parts[:2] == ["api", "runs"] and parts[3] == "page-info":
            self._page_info(parts[2], int(parts[4]))
        elif len(parts) == 6 and parts[:2] == ["api", "runs"] and parts[3] == "pages" and parts[4].endswith(".png"):
            self._page_image(parts[2], int(parts[4].removesuffix(".png")))
        else:
            self._error("찾을 수 없습니다.", HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802
        if urllib.parse.urlparse(self.path).path != "/api/jobs":
            self._error("찾을 수 없습니다.", HTTPStatus.NOT_FOUND)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            request = json.loads(self.rfile.read(length).decode("utf-8"))
            job = create_job(request)
            self._json(_job_payload(job), HTTPStatus.ACCEPTED)
        except (ValueError, json.JSONDecodeError) as error:
            self._error(str(error))


def main() -> None:
    host = os.environ.get("TEST_DASHBOARD_HOST", "127.0.0.1")
    port = int(os.environ.get("TEST_DASHBOARD_PORT", "8765"))
    server = ThreadingHTTPServer((host, port), DashboardHandler)
    print(f"Test Dashboard: http://{host}:{port}")
    print("종료하려면 Ctrl+C를 누르세요.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nTest Dashboard를 종료합니다.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
