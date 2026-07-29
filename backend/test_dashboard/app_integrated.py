"""Test 01~05를 한 포트에서 실행·검토하는 단일 로컬 대시보드 진입점."""

from __future__ import annotations

import html
import json
import os
import sys
import threading
import urllib.parse
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from . import server


SUPPORTED = {"test_01_pdf_chunking_memory_poc", "test_02_quant_blind_pdf_text_poc", "test_03_quant_b_documents_poc", "test_04_section_structurer_poc", "test_05_actual_vllm_gemma_document_evaluation_poc", "dashboard_job_failure"}
TEST_FILES = {"test_01": "tests/poc/test_01_pdf_chunking_poc.py", "test_02": "tests/poc/test_02_quant_blind_hybrid_poc.py", "test_03": "tests/poc/test_03_quant_b_documents_poc.py", "test_04": "tests/poc/test_04_section_structurer_poc.py", "test_05": "tests/poc/test_05_token_budget_and_llm_json_poc.py"}


def _options() -> str:
    return "".join(f"<option value='{html.escape(str(p.relative_to(server.PROJECT_ROOT)), quote=True)}'>{html.escape(p.name)}</option>" for p in sorted(server.FIXTURES_PDF_ROOT.glob("*.pdf"))) or "<option disabled selected>PDF fixture 없음</option>"


def _load(path: Path) -> dict[str, Any] | None:
    try: value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError): return None
    if value.get("test") not in SUPPORTED: return None
    value["id"] = path.parent.name if path.name == "summary.json" else path.stem
    return value


def _runs() -> list[dict[str, Any]]:
    values = []
    for path in server.RESULTS_ROOT.rglob("*.json"):
        item = _load(path)
        if item: values.append({"id": item["id"], "test": item["test"], "created_at": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(), "page_count": item.get("page_count"), "status": item.get("status")})
    return sorted(values, key=lambda value: value["created_at"], reverse=True)


def _run(run_id: str) -> dict[str, Any]:
    for path in server.RESULTS_ROOT.rglob("*.json"):
        item = _load(path)
        if item and item["id"] == run_id: return item
    raise FileNotFoundError(run_id)


def _job(request: dict[str, Any]) -> server.Job:
    test_id = str(request.get("test_id"))
    if test_id not in TEST_FILES: raise ValueError("지원하지 않는 테스트입니다.")
    env: dict[str, str] = {}
    pdf = server._safe_pdf_path(str(request.get("pdf_path", "")))
    env = {"test_01": {"POC_PDF_PATH": str(pdf), "POC_CHUNK_SIZES": str(request.get("chunk_sizes", "30,50")), "POC_REPEAT": "1", "POC_RENDER_SCALE": "1.0"}, "test_02": {"POC_BLIND_PDF_PATH": str(pdf)}, "test_03": {"POC_B_DOCUMENT_PDF_PATH": str(pdf)}, "test_04": {"POC_SECTION_PDF_PATH": str(pdf)}, "test_05": {"POC_LLM_PDF_PATH": str(pdf), "POC_LLM_RUBRIC_JSON": str(request.get("rubric", "")), "POC_LLM_OCR_MODE": str(request.get("ocr_mode", "all"))}}[test_id]
    job = server.Job(job_id=datetime.now(timezone.utc).strftime("job_%Y%m%dT%H%M%S%fZ"), test_id=test_id, command=[sys.executable, "-m", "pytest", "-m", "poc", TEST_FILES[test_id], "-s"], environment=env)
    with server.JOBS_LOCK: server.JOBS[job.job_id] = job
    threading.Thread(target=server._run_job, args=(job,), daemon=True).start()
    return job


HTML = """<!doctype html><html lang='ko'><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>Proposal Evaluation Test Dashboard</title><style>body{margin:0;background:#f5f7fb;color:#17233d;font:14px system-ui,sans-serif}header{padding:28px;background:#3158d4;color:#fff}main{max-width:1200px;margin:24px auto;padding:0 20px}.cards{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}.box{background:#fff;border:1px solid #dce3ee;border-radius:10px;padding:16px;margin-bottom:12px}.control{display:flex;gap:8px;flex-wrap:wrap;margin-top:10px}select,input,button{padding:9px;border:1px solid #cad4e4;border-radius:7px}select{min-width:240px;max-width:100%}button{background:#3158d4;color:#fff;font-weight:700;cursor:pointer}.muted{color:#68758c}.history{display:block;width:100%;text-align:left;margin:5px 0;background:#edf2ff;color:#17233d}.stage{position:relative;max-width:900px;background:#333;line-height:0}.stage img{width:100%;height:auto}.bbox{position:absolute;border:2px solid #f05228;background:#f0522838}table{width:100%;border-collapse:collapse}td{padding:7px;border-bottom:1px solid #dce3ee}@media(max-width:720px){.cards{grid-template-columns:1fr}}</style><header><h1>Proposal Evaluation · Test Dashboard</h1><p>Test 01~05 POC 실행 및 검토</p></header><main><div class=cards>
<section class=box><h2>01 · PDF 청크·메모리</h2><p class=muted>목적: 페이지 누락·중복 없이 대용량 PDF의 RSS와 처리 시간이 안정적인지 검증합니다.</p><div class=control><select id=p1>__OPTIONS__</select><input id=chunks value='30,50'><button onclick="run('test_01','p1')">실행</button></div></section>
<section class=box><h2>02 · 블라인드 위반</h2><p class=muted>목적: 업체명·대표이사 노출을 텍스트/PaddleOCR bbox로 탐지합니다.</p><div class=control><select id=p2>__OPTIONS__</select><button onclick="run('test_02','p2')">실행</button></div></section>
<section class=box><h2>03 · B권 증빙·상단 ROI</h2><p class=muted>목적: 목차·상단 25% 영역으로 필수 증빙서류를 판정합니다.</p><div class=control><select id=p3>__OPTIONS__</select><button onclick="run('test_03','p3')">실행</button></div></section>
<section class=box><h2>04 · A권 섹션 구조화</h2><p class=muted>목적: 제안서를 섹션·페이지 범위로 구조화해 RAG 근거를 보존합니다.</p><div class=control><select id=p4>__OPTIONS__</select><button onclick="run('test_04','p4')">실행</button></div></section>
<section class=box><h2>05 · 실제 LLM 토큰·JSON</h2><p class=muted>목적: 실제 Gemma/vLLM의 토큰 윈도우, JSON, 원문 근거를 검증합니다.</p><div class=control><button onclick="run('test_05')">Gemma 실행</button></div></section></div><p id=status class=muted></p><section class=box id=runs><h2>실행 이력</h2></section><section class=box id=detail><p class=muted>실행 이력을 선택하세요.</p></section></main><script>const esc=s=>String(s).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));async function api(u,o){let r=await fetch(u,o),d=await r.json();if(!r.ok)throw Error(d.error||'요청 실패');return d}async function refresh(){let r=(await api('/api/runs')).runs;runs.innerHTML='<h2>실행 이력</h2>'+(r.map(x=>`<button class=history onclick="showRun('${x.id}')">${esc(x.test)} · ${x.page_count||'-'}페이지 · ${new Date(x.created_at).toLocaleString('ko-KR')}</button>`).join('')||'<p class=muted>결과 없음</p>')}async function run(t,id){try{let b={test_id:t};if(id)b.pdf_path=document.getElementById(id).value;if(t==='test_01')b.chunk_sizes=chunks.value;let j=await api('/api/jobs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b)});status.textContent='실행 중';poll(j.job_id)}catch(e){status.textContent=e.message}}async function poll(id){let j=await api('/api/jobs/'+id);if(j.status==='RUNNING')return setTimeout(()=>poll(id),700);status.textContent=j.status+' · '+j.output.split('\\n').slice(-2).join(' ');refresh()}async function showRun(id){let d=await api('/api/runs/'+id);if(d.test.includes('05'))detail.innerHTML=`<h2>Test 05 결과</h2><p>${esc(d.model)} · ${d.token_count}토큰 · ${d.window_count}윈도우 · ${d.elapsed_seconds}초</p><p>점수: ${d.result.score}</p><p>${esc(d.result.reason)}</p><p>근거: ${esc(d.result.evidence)}</p>`;else if(d.test.includes('02')){let p=[...new Set((d.findings||[]).map(x=>x.page))];detail.innerHTML=`<h2>Test 02 · 후보 ${(d.findings||[]).length}건</h2><select id=pg>${p.map(x=>`<option>${x}</option>`).join('')}</select><div id=view></div>`;async function draw(n){let i=await api(`/api/page?run=${encodeURIComponent(id)}&page=${n}`),f=d.findings.filter(x=>x.page===n);view.innerHTML=`<div class=stage><img src='${i.image_url}'>${f.map(x=>{let[a,b,c,e]=x.bbox;return `<i class=bbox title='${esc(x.detected_text)}' style='left:${a/i.width*100}%;top:${b/i.height*100}%;width:${(c-a)/i.width*100}%;height:${(e-b)/i.height*100}%'></i>`}).join('')}</div>`}if(p.length){draw(p[0]);pg.onchange=e=>draw(+e.target.value)}}else detail.innerHTML=`<h2>${esc(d.test)}</h2><pre>${esc(JSON.stringify(d,null,2))}</pre>`;detail.scrollIntoView({behavior:'smooth',block:'start'})}refresh();</script></html>"""

HTML = HTML.replace("<button onclick=\"run('test_05')\">Gemma 실행</button>", "<select id=p5>__OPTIONS__</select><textarea id=p5rubric placeholder='배점표 JSON: {&quot;items&quot;:[{&quot;id&quot;:&quot;schedule&quot;,&quot;name&quot;:&quot;추진 일정&quot;,&quot;max_score&quot;:20,&quot;required_keywords&quot;:[&quot;착수&quot;,&quot;완료&quot;]}]}'></textarea><select id=p5ocr><option value=all>OCR 전체 페이지</option><option value=fallback>텍스트 부족 페이지만 OCR</option><option value=text>텍스트 레이어만</option></select><button onclick=\"run('test_05','p5')\">배점표 평가 실행</button>")
HTML = HTML.replace("${esc(x.test)} · ${x.page_count||'-'}페이지 ·", "${esc(x.test)}${x.status ? ' · '+x.status : ''} · ${x.page_count||'-'}페이지 ·")
HTML = HTML.replace("if(t==='test_01')b.chunk_sizes=chunks.value;", "if(t==='test_01')b.chunk_sizes=chunks.value;if(t==='test_05'){b.rubric=document.getElementById('p5rubric').value;b.ocr_mode=document.getElementById('p5ocr').value;}")
HTML = HTML.replace(
    "if(d.test.includes('05'))detail.innerHTML=`<h2>Test 05 결과</h2><p>${esc(d.model)} · ${d.token_count}토큰 · ${d.window_count}윈도우 · ${d.elapsed_seconds}초</p><p>점수: ${d.result.score}</p><p>${esc(d.result.reason)}</p><p>근거: ${esc(d.result.evidence)}</p>`;",
    "if(d.test.includes('05')){let rows=(d.item_results||[]).map(x=>`<tr><td>${esc(x.name)}</td><td>${x.score}/${x.max_score}</td><td>${esc(x.status)}</td><td>${(x.evidence_pages||[]).join(', ')||'-'}</td><td>${esc(x.comment||'-')}</td><td>${esc(x.evidence||'-')}</td></tr>`).join('');detail.innerHTML=`<h2>Test 05 배점표 평가</h2><p>${esc(d.model)} · ${d.page_count}페이지 · ${d.elapsed_seconds}초</p><p>총점: ${d.result.score}/${d.result.max_score}</p><p>인용 검증: ${d.citation_verified_items||0}건 · 검토 필요: ${d.citation_pending_review_items||0}건</p><table><tr><th>평가 항목</th><th>점수</th><th>규칙 상태</th><th>규칙 근거 페이지</th><th>AI 코멘트</th><th>AI 인용</th></tr>${rows}</table>`;}",
)


class Handler(BaseHTTPRequestHandler):
    def _json(self, value: Any, code: int = 200) -> None:
        data = json.dumps(value, ensure_ascii=False).encode(); self.send_response(code); self.send_header("Content-Type", "application/json; charset=utf-8"); self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)
    def do_GET(self) -> None:  # noqa: N802
        url = urllib.parse.urlparse(self.path); query = urllib.parse.parse_qs(url.query); parts = [p for p in url.path.split("/") if p]
        if url.path == "/":
            data = HTML.replace("__OPTIONS__", _options()).encode(); self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8"); self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)
        elif url.path == "/api/runs": self._json({"runs": _runs()})
        elif len(parts) == 3 and parts[:2] == ["api", "runs"]:
            try: self._json(_run(parts[2]))
            except FileNotFoundError: self._json({"error": "결과 없음"}, 404)
        elif len(parts) == 3 and parts[:2] == ["api", "jobs"]:
            with server.JOBS_LOCK: job = server.JOBS.get(parts[2])
            self._json(server._job_payload(job) if job else {"error": "작업 없음"}, 200 if job else 404)
        elif url.path == "/api/page":
            try:
                import fitz
                run, page_number = _run(query["run"][0]), int(query["page"][0]); doc = fitz.open(run["document_path"]); rect = doc.load_page(page_number - 1).rect; doc.close(); self._json({"width": rect.width, "height": rect.height, "image_url": f"/api/page-image?run={urllib.parse.quote(query['run'][0])}&page={page_number}"})
            except (KeyError, IndexError, ValueError, FileNotFoundError): self._json({"error": "페이지 없음"}, 404)
        elif url.path == "/api/page-image":
            try:
                import fitz
                run, page_number = _run(query["run"][0]), int(query["page"][0]); doc = fitz.open(run["document_path"]); data = doc.load_page(page_number - 1).get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False).tobytes("png"); doc.close(); self.send_response(200); self.send_header("Content-Type", "image/png"); self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)
            except (KeyError, IndexError, ValueError, FileNotFoundError): self._json({"error": "페이지 없음"}, 404)
        else: self._json({"error": "찾을 수 없음"}, 404)
    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/api/jobs": return self._json({"error": "찾을 수 없음"}, 404)
        try: self._json(server._job_payload(_job(json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))).decode()))), HTTPStatus.ACCEPTED)
        except (ValueError, json.JSONDecodeError) as error: self._json({"error": str(error)}, HTTPStatus.BAD_REQUEST)


def main() -> None:
    port = int(os.environ.get("TEST_DASHBOARD_PORT", "8769")); http_server = ThreadingHTTPServer(("127.0.0.1", port), Handler); print(f"Integrated test dashboard: http://127.0.0.1:{port}"); http_server.serve_forever()


if __name__ == "__main__": main()
