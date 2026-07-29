import React, { useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { invoke } from "@tauri-apps/api/core";
import { open } from "@tauri-apps/plugin-dialog";
import { api, type EvaluationResult, type RfpAnalysis, type RfpCatalogItem } from "./api";
import "./styles.css";

type View = "welcome" | "projects" | "create" | "setup" | "upload" | "quant" | "qual" | "results";
type FileKind = "RFP" | "A권" | "B권";
type Uploaded = { name: string; pages: number; kind: FileKind; path?: string };
type CatalogSection = "required_documents" | "quantitative_evaluation_items" | "qualitative_evaluation_items";
type EditableCatalogItem = RfpCatalogItem & { localId: string };
type CatalogDraft = Record<CatalogSection, EditableCatalogItem[]>;

function draftFromAnalysis(analysis: RfpAnalysis): CatalogDraft {
  const catalog = analysis.review_catalog;
  return {
    required_documents: catalog.required_documents.map((item, index) => ({ ...item, localId: `required-${index}` })),
    quantitative_evaluation_items: catalog.quantitative_evaluation_items.map((item, index) => ({ ...item, localId: `quant-${index}` })),
    qualitative_evaluation_items: catalog.qualitative_evaluation_items.map((item, index) => ({ ...item, localId: `qual-${index}` })),
  };
}

const projects = [
  ["💻", "노트북 조립 사업", "제안 5건", "진행 중", "warn"],
  ["🛠", "유지보수 사업", "제안요청서 등록 중", "준비 중", "ready"],
  ["📁", "클라우드 전환", "평가 완료 · 제안 8건", "완료", "done"],
] as const;

function Stepper({ active }: { active: number }) {
  const labels = ["제안요청서 등록", "제안서 업로드", "평가 / 피드백", "완료"];
  return <div className="stepper">{labels.map((label, i) => <React.Fragment key={label}>
    <div className={"step " + (i <= active ? "active" : "")}><span>{i < active ? "✓" : i + 1}</span><b>{label}</b></div>{i < 3 && <i className={i < active ? "filled" : ""} />}
  </React.Fragment>)}</div>;
}

function Shell({ children, title, active, goHome }: { children: React.ReactNode; title: string; active?: number; goHome: () => void }) {
  return <main className="app-shell"><aside><button className="mark" onClick={goHome}>A</button><div className="rail-brand">ARVIS<br/>CHECK</div><span className="offline">● OFFLINE</span></aside><section className="workspace"><header><div><h1>{title}</h1><p>제안서 평가 AI 시스템</p></div><button className="ghost" onClick={goHome}>내 사업</button></header>{active !== undefined && <div className="step-row"><Stepper active={active} /></div>}{children}</section></main>;
}

function UploadBox({ kind, onUpload, files }: { kind: FileKind; onUpload: (paths: string[]) => void; files: Uploaded[] }) {
  const ref = useRef<HTMLInputElement>(null);
  const related = files.filter(file => file.kind === kind);
  const choose = async () => {
    try {
      const selected = await open({ multiple: kind !== "RFP", filters: [{ name: "PDF", extensions: ["pdf"] }] });
      if (selected) onUpload(Array.isArray(selected) ? selected : [selected]);
    } catch { ref.current?.click(); }
  };
  return <><button className="dropzone" onClick={choose}><span className="plus">＋</span><strong>{kind === "RFP" ? "RFP 파일을 끌어다 놓거나 클릭" : `${kind} 제안서를 끌어다 놓거나 클릭`}</strong><small>PDF · 파일당 300MB 이내</small></button><input ref={ref} hidden type="file" accept="application/pdf" multiple={kind !== "RFP"} onChange={e => {
    if ((e.target.files?.length ?? 0) > 0) window.alert("브라우저에서는 로컬 파일 경로를 백엔드에 전달할 수 없습니다. AVIS Check 데스크톱 창에서 파일을 선택해 주세요.");
    e.target.value = "";
  }} />
  {related.length > 0 && <div className="file-grid">{related.map((file, i) => <article className="file-card" key={file.name + i}><span>PDF</span><div><b>{file.name}</b><small>{file.pages}쪽 · 등록됨</small></div><em>완료</em></article>)}</div>}</>;
}

function App() {
  const [view, setView] = useState<View>("welcome");
  const [projectName, setProjectName] = useState("노트북 조립 사업");
  const [files, setFiles] = useState<Uploaded[]>([]);
  const [isEvaluating, setEvaluating] = useState(false);
  const [isPreparingRfp, setPreparingRfp] = useState(false);
  const [backendError, setBackendError] = useState("");
  const [evaluation, setEvaluation] = useState<EvaluationResult>();
  const [projectId, setProjectId] = useState<string>();
  const [rfpAnalysis, setRfpAnalysis] = useState<RfpAnalysis>();
  const [catalogDraft, setCatalogDraft] = useState<CatalogDraft>();
  const addFiles = (kind: FileKind) => (paths: string[]) => {
    setFiles(old => [...old, ...paths.map((path, i) => ({ name: path.split(/[\\/]/).pop() ?? path, pages: 30 + i * 4, kind, path }))]);
  };
  const ensureBackend = async () => {
    try { await api.health(); } catch {
        try {
          await invoke("start_backend");
        } catch (sidecarError) {
          const message = sidecarError instanceof Error ? sidecarError.message : String(sidecarError);
          const isDesktop = Boolean((window as Window & { __TAURI_INTERNALS__?: unknown }).__TAURI_INTERNALS__);
          throw new Error(isDesktop ? `백엔드를 시작하지 못했습니다: ${message}` : "브라우저에서는 백엔드를 자동으로 시작할 수 없습니다. AVIS Check 데스크톱 창에서 실행해 주세요.");
        }
        let ready = false;
        for (let attempt = 0; attempt < 20; attempt += 1) {
          await new Promise(resolve => window.setTimeout(resolve, 250));
          try { await api.health(); ready = true; break; } catch { /* sidecar 초기화 대기 */ }
        }
        if (!ready) throw new Error("백엔드가 5초 안에 시작되지 않았습니다. Python sidecar 설정을 확인하세요.");
    }
  };
  const prepareRfp = async () => {
    const rfpPaths = files.filter(file => file.kind === "RFP" && file.path).map(file => file.path!);
    if (!rfpPaths.length) { setBackendError("분석할 제안요청서(RFP) PDF를 먼저 선택해 주세요."); return; }
    setBackendError(""); setPreparingRfp(true);
    try {
      await ensureBackend();
      const project = await api.createProject(projectName);
      await api.registerDocuments(project.id, "RFP", rfpPaths);
      const analysis = await api.analyzeRfp(project.id);
      setProjectId(project.id); setRfpAnalysis(analysis); setCatalogDraft(draftFromAnalysis(analysis)); setView("setup");
    } catch (error) { setBackendError(error instanceof Error ? error.message : "RFP 분석에 실패했습니다."); }
    finally { setPreparingRfp(false); }
  };
  const beginEvaluation = async () => {
    setBackendError(""); setEvaluating(true);
    try {
      if (!projectId) throw new Error("제안요청서 분석을 먼저 완료해 주세요.");
      await ensureBackend();
      for (const [kind, apiKind] of [["A권", "A"], ["B권", "B"]] as const) {
        const paths = files.filter(file => file.kind === kind && file.path).map(file => file.path!);
        if (paths.length) await api.registerDocuments(projectId, apiKind, paths);
      }
      const rubric = { items: [{ id: "strategy", name: "수행 전략 및 창의성", max_score: 40, required_keywords: ["수행", "계획"] }, { id: "delivery", name: "사업 수행 능력", max_score: 30, required_keywords: ["일정", "관리"] }] };
      let job = await api.evaluate(projectId, rubric);
      while (job.status === "RUNNING") { await new Promise(resolve => window.setTimeout(resolve, 700)); job = await api.evaluation(job.id); }
      if (job.status === "FAILED" || !job.result) throw new Error(job.error ?? "평가에 실패했습니다.");
      setEvaluation(job.result); setView("quant");
    } catch (error) { setBackendError(error instanceof Error ? error.message : "백엔드 연결에 실패했습니다."); }
    finally { setEvaluating(false); }
  };
  const updateCatalogItem = (section: CatalogSection, localId: string, patch: Partial<EditableCatalogItem>) => setCatalogDraft(old => old ? ({ ...old, [section]: old[section].map(item => item.localId === localId ? { ...item, ...patch } : item) }) : old);
  const removeCatalogItem = (section: CatalogSection, localId: string) => setCatalogDraft(old => old ? ({ ...old, [section]: old[section].filter(item => item.localId !== localId) }) : old);
  const addCatalogItem = (section: CatalogSection, groupPage?: number) => setCatalogDraft(old => {
    if (!old) return old;
    const prefix = section === "required_documents" ? "필수 서류" : section === "quantitative_evaluation_items" ? "정량 평가 항목" : "정성 평가 항목";
    const item: EditableCatalogItem = { localId: `${section}-${Date.now()}`, name: `새 ${prefix}`, importance: section === "quantitative_evaluation_items" ? "general" : undefined, source: "manual", evidence: { page: groupPage ?? 0, text: "평가위원이 추가한 항목" }, review_required: true };
    return { ...old, [section]: [...old[section], item] };
  });

  if (view === "welcome") return <div className="welcome"><div className="welcome-side"><div className="wordmark">ARVIS<br/>CHECK</div><div className="abstract">✦<br/>⌁<br/>✧</div><span>OFFLINE AI PROPOSAL REVIEW</span></div><div className="welcome-main"><div className="welcome-card"><div className="welcome-icon">＋</div><label>WELCOME TO AVIS_CHECK</label><h1>첫 사업을 시작해 보세요</h1><p>제안요청서를 등록하고 제안서를 올리면<br/>AVIS가 정량·정성 평가를 도와드립니다.</p><button className="primary big" onClick={() => setView("projects")}>사업 시작하기 <span>→</span></button></div></div></div>;

  if (view === "projects") return <Shell title="내 사업" goHome={() => setView("projects")}><div className="content"><div className="section-title"><span>총 3건 진행중</span><button className="primary" onClick={() => setView("create")}>＋ 사업 추가</button></div><div className="project-grid">{projects.map(([icon, name, desc, status, tone]) => <button className="project-card" key={name} onClick={() => { setProjectName(name); setView(name === "노트북 조립 사업" ? "setup" : "create"); }}><span className="project-icon">{icon}</span><b>{name}</b><small>{desc}</small><em className={tone}>{status}</em></button>)}<button className="add-project" onClick={() => setView("create")}>＋<b>사업 추가</b></button></div></div></Shell>;

  if (view === "create") return <Shell title="새 사업 만들기" goHome={() => setView("projects")}><div className="center-content"><div className="form-card"><label>사업명</label><input value={projectName} onChange={e => setProjectName(e.target.value)} placeholder="사업명을 작성하세요"/><label>제안요청서 업로드</label><UploadBox kind="RFP" files={files} onUpload={addFiles("RFP")} /><div className="actions"><button className="secondary" onClick={() => setView("projects")}>취소</button><button className="primary" disabled={isPreparingRfp} onClick={prepareRfp}>{isPreparingRfp ? "RFP 분석 중…" : "분석 후 다음 →"}</button></div>{backendError && <p className="backend-error">{backendError}</p>}</div></div></Shell>;

  if (view === "setup") { const draft = catalogDraft; return <Shell title={projectName} active={0} goHome={() => setView("projects")}><div className="content narrow"><article className="info-card"><span className="doc-icon">▤</span><div><b>제안요청서 자동 분석</b><small>{files.find(file => file.kind === "RFP")?.name} · {rfpAnalysis?.notice ?? "분석 결과 없음"}</small></div></article><EditableSettings title="필수 서류" empty="추출된 필수 서류가 없습니다. RFP 원문을 검토하세요." items={draft?.required_documents ?? []} onUpdate={(id, patch) => updateCatalogItem("required_documents", id, patch)} onRemove={id => removeCatalogItem("required_documents", id)} onAdd={() => addCatalogItem("required_documents")}/><QuantitativeSettings items={draft?.quantitative_evaluation_items ?? []} onUpdate={(id, patch) => updateCatalogItem("quantitative_evaluation_items", id, patch)} onRemove={id => removeCatalogItem("quantitative_evaluation_items", id)} onAdd={page => addCatalogItem("quantitative_evaluation_items", page)}/><EditableSettings title="정성 평가 항목" empty="추출된 정성 평가 항목이 없습니다. 상대평가 표기를 확인하세요." items={draft?.qualitative_evaluation_items ?? []} onUpdate={(id, patch) => updateCatalogItem("qualitative_evaluation_items", id, patch)} onRemove={id => removeCatalogItem("qualitative_evaluation_items", id)} onAdd={() => addCatalogItem("qualitative_evaluation_items")}/><div className="actions"><button className="secondary" onClick={() => setView("create")}>이전</button><button className="primary" onClick={() => setView("upload")}>다음</button></div></div></Shell>; }

  if (view === "upload") return <Shell title={projectName} active={1} goHome={() => setView("projects")}><div className="content narrow"><div className="upload-columns"><section><h2>기본 제안서 <small>A권 · 정성 평가</small></h2><UploadBox kind="A권" files={files} onUpload={addFiles("A권")} /></section><section><h2>제안회사 소개 · 증빙자료 <small>B권 · 정량 평가</small></h2><UploadBox kind="B권" files={files} onUpload={addFiles("B권")} /></section></div><div className="actions"><button className="secondary" onClick={() => setView("setup")}>이전</button><button className="primary" disabled={isEvaluating} onClick={beginEvaluation}>{isEvaluating ? "문서를 분석하는 중…" : "평가 시작 →"}</button></div>{backendError && <p className="backend-error">{backendError}</p>}</div></Shell>;

  if (view === "quant") return <Feedback type="quant" projectName={projectName} result={evaluation} goHome={() => setView("projects")} navigate={setView}/>;
  if (view === "qual") return <Feedback type="qual" projectName={projectName} result={evaluation} goHome={() => setView("projects")} navigate={setView}/>;
  return <Results projectName={projectName} result={evaluation} goHome={() => setView("projects")} navigate={setView}/>;
}

type SettingEditorProps = { title: string; empty: string; items: EditableCatalogItem[]; onUpdate: (id: string, patch: Partial<EditableCatalogItem>) => void; onRemove: (id: string) => void; onAdd: () => void };
function EditableSettings({ title, empty, items, onUpdate, onRemove, onAdd }: SettingEditorProps) { return <article className="settings"><h2>{title}</h2><p>제안요청서에서 추출한 기준입니다. 항목명을 직접 수정하거나 추가할 수 있습니다.</p><div className="setting-items">{items.length ? items.map(item => <EditableItemRow key={item.localId} item={item} onUpdate={onUpdate} onRemove={onRemove}/>) : <div className="empty-item">{empty}</div>}<button type="button" onClick={onAdd}>＋ 항목 추가</button></div></article>; }
function EditableItemRow({ item, onUpdate, onRemove, showImportance = false }: { item: EditableCatalogItem; onUpdate: (id: string, patch: Partial<EditableCatalogItem>) => void; onRemove: (id: string) => void; showImportance?: boolean }) { return <div className="editable-item"><input aria-label="항목명" value={item.name} onChange={event => onUpdate(item.localId, { name: event.target.value })}/>{showImportance && <select aria-label="항목 구분" value={item.importance ?? "general"} onChange={event => onUpdate(item.localId, { importance: event.target.value })}><option value="required">필수</option><option value="general">일반</option><option value="unknown">검토 필요</option></select>}<small>{item.source === "manual" ? "직접 추가" : `RFP ${item.evidence.page ? `${item.evidence.page}쪽` : "추출"}`}</small><button type="button" className="delete-item" onClick={() => onRemove(item.localId)} aria-label="항목 삭제">×</button></div>; }
function QuantitativeSettings({ items, onUpdate, onRemove, onAdd }: { items: EditableCatalogItem[]; onUpdate: (id: string, patch: Partial<EditableCatalogItem>) => void; onRemove: (id: string) => void; onAdd: (page?: number) => void }) { const [open, setOpen] = useState<Record<string, boolean>>({}); const groups = new Map<string, { label: string; page?: number; items: EditableCatalogItem[] }>(); for (const item of items) { const page = item.evidence.page || undefined; const label = item.source === "evaluation_rule" ? `평가 규칙 · ${page ?? "원문"}쪽` : item.source === "manual" ? "직접 추가 항목" : `체계규격 표 · ${page ?? "원문"}쪽`; const group = groups.get(label) ?? { label, page, items: [] }; group.items.push(item); groups.set(label, group); } return <article className="settings"><h2>정량 평가 항목</h2><p>같은 체계규격 표에서 추출된 세부 항목을 묶었습니다. 그룹을 펼쳐 확인·수정하세요.</p><div className="quant-groups">{[...groups.values()].map(group => <section className="quant-group" key={group.label}><button type="button" className="group-toggle" onClick={() => setOpen(old => ({ ...old, [group.label]: !old[group.label] }))}><span>{open[group.label] ? "⌄" : "›"}</span><b>{group.label}</b><em>{group.items.length}개 항목</em></button>{open[group.label] && <div className="group-items">{group.items.map(item => <EditableItemRow key={item.localId} item={item} onUpdate={onUpdate} onRemove={onRemove} showImportance/>)}<button type="button" className="add-in-group" onClick={() => onAdd(group.page)}>＋ 이 표에 항목 추가</button></div>}</section>)}</div><button type="button" className="manual-add" onClick={() => onAdd()}>＋ 별도 항목 추가</button></article>; }

function Feedback({ type, projectName, result, goHome, navigate }: { type: "quant" | "qual"; projectName: string; result?: EvaluationResult; goHome: () => void; navigate: (view: View) => void }) {
  const isQuant = type === "quant";
  const item = result?.item_results[isQuant ? 1 : 0];
  return <Shell title={projectName} active={2} goHome={goHome}><div className="feedback"><article className="document"><small>분석 문서 · {result?.page_count ?? "-"}페이지</small><h2>{item?.name ?? (isQuant ? "Ⅲ. 사업 수행 계획" : "Ⅳ. 수행 전략 및 창의성")}</h2>{isQuant ? <p>본 사업은 총 12주에 걸쳐 노트북 500대를 조립·납품하며, <mark className="good">주당 42대 조립 능력을 보유한 자체 라인 2개를 운영</mark>한다.<br/><mark className="bad">품질 검사 인력은 3명으로 RFP 기준(5명)에 미달</mark>하여 해당 항목에서 감점이 발생하였다. 납기 준수율은 최근 3개년 평균 <mark className="good">98.2%로 우수 등급</mark>에 해당한다.</p> : <p>기존 조립 공정에 <mark className="good">AI 기반 불량 예측 검사를 도입</mark>하여 초기 불량률을 30% 절감하는 방안을 제시하였다. 다만 <mark className="bad">제시된 일정과 인력 계획 간 정합성 근거가 부족</mark>하여 실행 가능성 측면에서 보완이 필요하다.</p>}<div className="legend">API 근거 페이지: {item?.citation_pages.join(", ") || item?.evidence_pages.join(", ") || "없음"}</div></article><aside className="feedback-side">{isQuant ? <><span className="tag">정량적 평가</span><h3>{item?.score ?? 0} <small>/ {item?.max_score ?? 30}점</small></h3><Score label="법/법령/훈령 준수" value="100%" width="100%"/><Score label="체계 규격 필수항목 충족" value="+24" width="100%"/><Score label="가점 / 감점 요소" value="+0.1" width="45%"/><button className="secondary" onClick={() => navigate("qual")}>정성적 평가 보기 →</button></> : <><span className="tag">정성적 평가</span><small>{result?.comment_source === "rule_based_fallback" ? "규칙 기반 코멘트" : "AI 코멘트"}</small><div className={item?.status === "MET" ? "comment good-box" : "comment bad-box"}><b>{item?.status === "MET" ? "충족" : "보완 필요"}</b><p>{item?.comment ?? "평가 결과가 없습니다."}</p></div><button className="secondary" onClick={() => navigate("quant")}>정량적 평가 보기 →</button></>}<button className="primary" onClick={() => navigate("results")}>결과 보기</button></aside></div></Shell>;
}

function Score({ label, value, width }: { label: string; value: string; width: string }) { return <div className="score"><div><b>{label}</b><em>{value}</em></div><i><span style={{ width }}/></i></div>; }
function Results({ projectName, result, goHome, navigate }: { projectName: string; result?: EvaluationResult; goHome: () => void; navigate: (view: View) => void }) { const entries = [["1", projectName, "완료", `${result?.result.score ?? 0}/${result?.result.max_score ?? 0}`]]; return <Shell title={projectName} active={3} goHome={goHome}><div className="content"><div className="results-head"><div><h2>채점 결과</h2><p>{result?.notice ?? "실행 결과가 없습니다."}</p></div><button className="secondary" onClick={goHome}>리포트 내보내기</button></div><div className="results-grid">{entries.map(([rank, company, grade, score]) => <button className="result-card" key={company} onClick={() => navigate(rank === "1" ? "qual" : "quant")}><div><span className="rank">{rank}</span><em>{grade} 등급</em></div><h2>{company}</h2><small>제안서 평가 완료</small><strong>{score}<small>점</small></strong><div className="result-tags"><span>정량 24.1 / 30</span><span>정성 70.0 / 70</span></div></button>)}</div></div></Shell>; }

createRoot(document.getElementById("root")!).render(<App />);
