"""SQLite-backed, on-device cache and evaluation store."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from pathlib import Path
from typing import Iterable


def data_directory() -> Path:
    """Return the application-local data directory without using a DB server."""

    if configured := os.environ.get("PROPOSAL_EVALUATION_DATA_DIR"):
        return Path(configured).expanduser().resolve()
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "AVISCheck"
    return Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state")) / "avis-check"


def file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class LocalStore:
    """A small SQLite store; source PDFs are never copied into it."""

    def __init__(self, database_path: str | Path | None = None) -> None:
        self.path = Path(database_path) if database_path else data_directory() / "proposal_evaluation.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS documents (
                    sha256 TEXT PRIMARY KEY,
                    source_path TEXT NOT NULL,
                    file_size INTEGER NOT NULL,
                    modified_ns INTEGER NOT NULL,
                    last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS page_extractions (
                    document_sha256 TEXT NOT NULL REFERENCES documents(sha256) ON DELETE CASCADE,
                    ocr_mode TEXT NOT NULL,
                    minimum_text_chars INTEGER NOT NULL,
                    render_scale REAL NOT NULL,
                    page_number INTEGER NOT NULL,
                    text TEXT NOT NULL,
                    source TEXT NOT NULL,
                    PRIMARY KEY (document_sha256, ocr_mode, minimum_text_chars, render_scale, page_number)
                );
                CREATE TABLE IF NOT EXISTS extraction_sessions (
                    document_sha256 TEXT NOT NULL REFERENCES documents(sha256) ON DELETE CASCADE,
                    ocr_mode TEXT NOT NULL,
                    minimum_text_chars INTEGER NOT NULL,
                    render_scale REAL NOT NULL,
                    page_count INTEGER NOT NULL,
                    completed INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (document_sha256, ocr_mode, minimum_text_chars, render_scale)
                );
                CREATE TABLE IF NOT EXISTS evaluations (
                    evaluation_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    document_path TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    finished_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS rfp_analyses (
                    document_sha256 TEXT PRIMARY KEY REFERENCES documents(sha256) ON DELETE CASCADE,
                    analysis_json TEXT NOT NULL,
                    analysis_version INTEGER NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS projects (
                    project_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    rfp_analysis_json TEXT,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS project_documents (
                    project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
                    document_kind TEXT NOT NULL CHECK(document_kind IN ('RFP', 'A', 'B')),
                    position INTEGER NOT NULL,
                    source_path TEXT NOT NULL,
                    PRIMARY KEY (project_id, document_kind, position)
                );
                """
            )

    def _key(self, pdf_path: str | Path) -> str:
        return file_sha256(pdf_path)

    def _rows(self, digest: str, *, mode: str, minimum_text_chars: int, render_scale: float) -> list[tuple[int, str, str]]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT page_number, text, source FROM page_extractions
                   WHERE document_sha256=? AND ocr_mode=? AND minimum_text_chars=? AND render_scale=?
                   ORDER BY page_number""",
                (digest, mode, minimum_text_chars, render_scale),
            ).fetchall()
        return [(int(row["page_number"]), str(row["text"]), str(row["source"])) for row in rows]

    def load_extraction(self, pdf_path: str | Path, *, mode: str, minimum_text_chars: int, render_scale: float) -> list[tuple[int, str, str]] | None:
        digest = file_sha256(pdf_path)
        with self._connect() as connection:
            session = connection.execute(
                """SELECT completed FROM extraction_sessions
                   WHERE document_sha256=? AND ocr_mode=? AND minimum_text_chars=? AND render_scale=?""",
                (digest, mode, minimum_text_chars, render_scale),
            ).fetchone()
        if session is not None and not session["completed"]:
            return None
        rows = self._rows(digest, mode=mode, minimum_text_chars=minimum_text_chars, render_scale=render_scale)
        return rows or None

    def load_partial_extraction(self, pdf_path: str | Path, *, mode: str, minimum_text_chars: int, render_scale: float) -> list[tuple[int, str, str]]:
        return self._rows(self._key(pdf_path), mode=mode, minimum_text_chars=minimum_text_chars, render_scale=render_scale)

    def save_extraction_chunk(self, pdf_path: str | Path, pages: Iterable[object], *, mode: str, minimum_text_chars: int, render_scale: float, page_count: int) -> None:
        path = Path(pdf_path).expanduser().resolve()
        stat = path.stat()
        digest = file_sha256(path)
        records = [(int(page.page), str(page.text), str(page.source)) for page in pages]
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO documents(sha256, source_path, file_size, modified_ns)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(sha256) DO UPDATE SET source_path=excluded.source_path,
                   file_size=excluded.file_size, modified_ns=excluded.modified_ns, last_seen_at=CURRENT_TIMESTAMP""",
                (digest, str(path), stat.st_size, stat.st_mtime_ns),
            )
            connection.execute(
                """INSERT INTO extraction_sessions VALUES (?, ?, ?, ?, ?, 0, CURRENT_TIMESTAMP)
                   ON CONFLICT(document_sha256, ocr_mode, minimum_text_chars, render_scale)
                   DO UPDATE SET page_count=excluded.page_count, updated_at=CURRENT_TIMESTAMP""",
                (digest, mode, minimum_text_chars, render_scale, page_count),
            )
            connection.executemany(
                """INSERT INTO page_extractions VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(document_sha256, ocr_mode, minimum_text_chars, render_scale, page_number)
                   DO UPDATE SET text=excluded.text, source=excluded.source""",
                [(digest, mode, minimum_text_chars, render_scale, number, text, source) for number, text, source in records],
            )

    def mark_extraction_complete(self, pdf_path: str | Path, *, mode: str, minimum_text_chars: int, render_scale: float) -> None:
        digest = self._key(pdf_path)
        with self._connect() as connection:
            connection.execute(
                """UPDATE extraction_sessions SET completed=1, updated_at=CURRENT_TIMESTAMP
                   WHERE document_sha256=? AND ocr_mode=? AND minimum_text_chars=? AND render_scale=?""",
                (digest, mode, minimum_text_chars, render_scale),
            )

    def save_extraction(self, pdf_path: str | Path, pages: Iterable[object], *, mode: str, minimum_text_chars: int, render_scale: float) -> None:
        records = list(pages)
        self.save_extraction_chunk(pdf_path, records, mode=mode, minimum_text_chars=minimum_text_chars, render_scale=render_scale, page_count=len(records))
        self.mark_extraction_complete(pdf_path, mode=mode, minimum_text_chars=minimum_text_chars, render_scale=render_scale)

    def save_evaluation(self, *, evaluation_id: str, project_id: str, document_path: str, result: dict[str, object], finished_at: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO evaluations VALUES (?, ?, ?, ?, ?)",
                (evaluation_id, project_id, document_path, json.dumps(result, ensure_ascii=False), finished_at),
            )

    def save_project(self, *, project_id: str, name: str, created_at: str, documents: dict[str, list[str]], rfp_analysis: dict[str, object] | None) -> None:
        """Persist the project card and source-PDF locations, never the PDF bytes."""

        with self._connect() as connection:
            connection.execute(
                """INSERT INTO projects(project_id, name, created_at, rfp_analysis_json)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(project_id) DO UPDATE SET name=excluded.name,
                     rfp_analysis_json=excluded.rfp_analysis_json, updated_at=CURRENT_TIMESTAMP""",
                (project_id, name, created_at, json.dumps(rfp_analysis, ensure_ascii=False) if rfp_analysis else None),
            )
            connection.execute("DELETE FROM project_documents WHERE project_id=?", (project_id,))
            rows = [
                (project_id, kind, position, str(Path(path).expanduser().resolve()))
                for kind in ("RFP", "A", "B")
                for position, path in enumerate(documents.get(kind, []))
            ]
            connection.executemany("INSERT INTO project_documents VALUES (?, ?, ?, ?)", rows)

    def load_projects(self) -> list[dict[str, object]]:
        with self._connect() as connection:
            rows = connection.execute("SELECT project_id, name, created_at, rfp_analysis_json FROM projects ORDER BY updated_at DESC, created_at DESC").fetchall()
            document_rows = connection.execute("SELECT project_id, document_kind, source_path FROM project_documents ORDER BY project_id, document_kind, position").fetchall()
        documents: dict[str, dict[str, list[str]]] = {}
        for row in document_rows:
            project_id = str(row["project_id"])
            documents.setdefault(project_id, {"RFP": [], "A": [], "B": []})[str(row["document_kind"])].append(str(row["source_path"]))
        return [{
            "id": str(row["project_id"]), "name": str(row["name"]), "created_at": str(row["created_at"]),
            "documents": documents.get(str(row["project_id"]), {"RFP": [], "A": [], "B": []}),
            "rfp_analysis": json.loads(str(row["rfp_analysis_json"])) if row["rfp_analysis_json"] else None,
        } for row in rows]

    def load_rfp_analysis(self, pdf_path: str | Path, *, version: int) -> dict[str, object] | None:
        with self._connect() as connection:
            row = connection.execute("SELECT analysis_json FROM rfp_analyses WHERE document_sha256=? AND analysis_version=?", (self._key(pdf_path), version)).fetchone()
        return json.loads(str(row["analysis_json"])) if row is not None else None

    def save_rfp_analysis(self, pdf_path: str | Path, analysis: dict[str, object], *, version: int) -> None:
        path = Path(pdf_path).expanduser().resolve()
        stat = path.stat()
        digest = self._key(path)
        with self._connect() as connection:
            connection.execute("""INSERT INTO documents(sha256, source_path, file_size, modified_ns) VALUES (?, ?, ?, ?)
                               ON CONFLICT(sha256) DO UPDATE SET source_path=excluded.source_path, file_size=excluded.file_size,
                               modified_ns=excluded.modified_ns, last_seen_at=CURRENT_TIMESTAMP""", (digest, str(path), stat.st_size, stat.st_mtime_ns))
            connection.execute("INSERT OR REPLACE INTO rfp_analyses VALUES (?, ?, ?, CURRENT_TIMESTAMP)", (digest, json.dumps(analysis, ensure_ascii=False), version))
