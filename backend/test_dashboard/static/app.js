const state = { runs: [], activeRun: null, fixtures: [] };
const byId = (id) => document.getElementById(id);

async function api(url, options) {
  const response = await fetch(url, options);
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || '요청에 실패했습니다.');
  return payload;
}

function escapeHtml(value) {
  return String(value).replace(/[&<>'"]/g, (char) => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[char]));
}

function formatTestName(test) {
  return test.includes('test_01') ? 'Test 01 · PDF 청크' : 'Test 02 · 블라인드 탐지';
}

function fillFixtures() {
  for (const select of [byId('test-01-pdf'), byId('test-02-pdf')]) {
    select.innerHTML = state.fixtures.map((path) => `<option value="${escapeHtml(path)}">${escapeHtml(path.replace('tests/fixtures/pdf/', ''))}</option>`).join('');
  }
  const proposal = state.fixtures.find((path) => path.includes('제안서'));
  if (proposal) byId('test-02-pdf').value = proposal;
}

async function refreshRuns() {
  const { runs } = await api('/api/runs');
  state.runs = runs;
  byId('run-count').textContent = `${runs.length}건`;
  const list = byId('run-list');
  list.innerHTML = '';
  const template = byId('run-template');
  for (const run of runs) {
    const node = template.content.firstElementChild.cloneNode(true);
    node.querySelector('strong').textContent = formatTestName(run.test);
    node.querySelector('.run-meta').textContent = new Date(run.created_at).toLocaleString('ko-KR');
    node.querySelector('.run-summary').textContent = run.test.includes('test_01')
      ? `최대 RSS ${run.summary.max_rss_mb ?? '-'}MB`
      : `${run.page_count ?? '-'}페이지 · 후보 ${run.finding_count}건`;
    node.classList.toggle('active', state.activeRun?.id === run.id);
    node.addEventListener('click', () => showRun(run.id));
    list.appendChild(node);
  }
}

async function showRun(runId) {
  const run = await api(`/api/runs/${encodeURIComponent(runId)}`);
  state.activeRun = run;
  await refreshRuns();
  const details = byId('details');
  details.className = 'details';
  if (run.test.includes('test_01')) renderTest01(details, run);
  else renderTest02(details, run);
}

function renderTest01(container, run) {
  const chunks = run.chunks || [];
  const maxRss = Math.max(...chunks.map((chunk) => chunk.rss_peak_mb), 1);
  container.innerHTML = `
    <div class="detail-title"><div><h2>Test 01 · PDF 청크 · 메모리</h2><p>${escapeHtml(run.document_path)}</p></div><strong>${run.page_count}페이지</strong></div>
    <div class="chips"><span class="chip">최대 RSS ${run.summary.max_rss_mb}MB</span><span class="chip">총 렌더링 ${run.summary.total_render_seconds}s</span><span class="chip">누락 ${run.summary.failed_pages} · 중복 ${run.summary.duplicate_pages}</span></div>
    <h3>청크별 최대 RSS</h3><div class="metric-bars">${chunks.map((chunk) => `<div class="bar-row"><span>${chunk.chunk_size}p · #${chunk.chunk_number}</span><div class="bar-track"><div class="bar" style="width:${(chunk.rss_peak_mb / maxRss) * 100}%"></div></div><strong>${chunk.rss_peak_mb}MB</strong></div>`).join('')}</div>
    <h3 class="table-heading">측정값</h3><table><thead><tr><th>청크</th><th>페이지</th><th>RSS 전/GC 후</th><th>시간</th></tr></thead><tbody>${chunks.map((chunk) => `<tr><td>${chunk.chunk_size}p · #${chunk.chunk_number}</td><td>${chunk.start_page}–${chunk.end_page}</td><td>${chunk.rss_before_mb} / ${chunk.rss_after_gc_mb}MB</td><td>${chunk.render_seconds}s</td></tr>`).join('')}</tbody></table>`;
}

function renderTest02(container, run) {
  const findings = run.findings || [];
  const pages = [...new Set(findings.map((item) => item.page))].sort((a, b) => a - b);
  container.innerHTML = `
    <div class="detail-title"><div><h2>Test 02 · 블라인드 위반 후보</h2><p>${escapeHtml(run.document_path)}</p></div><strong>${findings.length}건</strong></div>
    <div class="chips"><span class="chip">${run.page_count}페이지</span><span class="chip">${run.elapsed_seconds}s</span><span class="chip">${run.rss_mb}MB</span><span class="chip">모두 PENDING_REVIEW</span></div>
    <label class="page-select">후보 페이지 <select id="finding-page">${pages.map((page) => `<option value="${page}">${page}페이지 (${findings.filter((item) => item.page === page).length}건)</option>`).join('')}</select></label>
    <div id="pdf-viewer" class="viewer"></div>`;
  byId('finding-page').addEventListener('change', (event) => showFindingPage(run, Number(event.target.value)));
  showFindingPage(run, pages[0]);
}

async function showFindingPage(run, page) {
  const info = await api(`/api/runs/${encodeURIComponent(run.id)}/page-info/${page}`);
  const pageFindings = run.findings.filter((finding) => finding.page === page);
  const overlays = pageFindings.map((finding, index) => {
    const [x0, y0, x1, y1] = finding.bbox;
    const left = x0 / info.width * 100, top = y0 / info.height * 100;
    const width = (x1 - x0) / info.width * 100, height = (y1 - y0) / info.height * 100;
    return `<span class="bbox" title="${escapeHtml(finding.detected_text)}" style="left:${left}%;top:${top}%;width:${width}%;height:${height}%" data-index="${index}"></span>`;
  }).join('');
  byId('pdf-viewer').innerHTML = `<div class="page-stage"><img src="${info.image_url}" alt="${page}페이지" />${overlays}</div><div class="finding-list">${pageFindings.map((finding) => `<div class="finding"><strong>${escapeHtml(finding.detected_text)}</strong><small>${finding.rule_id}</small><small>${finding.status} · 감점 ${finding.penalty_score}</small></div>`).join('')}</div>`;
}

async function runTest(testId) {
  const button = document.querySelector(`[data-test="${testId}"]`);
  button.disabled = true;
  try {
    const request = testId === 'test_01'
      ? { test_id: testId, pdf_path: byId('test-01-pdf').value, chunk_sizes: byId('chunk-sizes').value, repeat: Number(byId('repeat').value), render_scale: 1.0 }
      : { test_id: testId, pdf_path: byId('test-02-pdf').value };
    const job = await api('/api/jobs', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(request) });
    await pollJob(job.job_id);
  } catch (error) {
    byId('job-status').textContent = `실행 실패: ${error.message}`;
  } finally { button.disabled = false; }
}

async function pollJob(jobId) {
  const job = await api(`/api/jobs/${encodeURIComponent(jobId)}`);
  byId('job-status').textContent = `${formatTestName(job.test_id.replace('_', '_'))} ${job.status}…`;
  if (job.status === 'RUNNING') return setTimeout(() => pollJob(jobId), 800);
  byId('job-status').textContent = `${job.status}: ${job.output.split('\n').slice(-3).join(' ')}`;
  await refreshRuns();
}

document.querySelectorAll('[data-test]').forEach((button) => button.addEventListener('click', () => runTest(button.dataset.test)));
byId('refresh-button').addEventListener('click', refreshRuns);

(async () => {
  try {
    state.fixtures = (await api('/api/fixtures')).fixtures;
    fillFixtures();
    await refreshRuns();
  } catch (error) { byId('job-status').textContent = `초기화 실패: ${error.message}`; }
})();
