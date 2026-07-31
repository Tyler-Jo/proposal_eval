import React, { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { invoke } from "@tauri-apps/api/core";
import { open } from "@tauri-apps/plugin-dialog";
import { api, type AdjustmentResult, type BDocumentCheck, type BDocumentCheckJob, type Evaluation, type EvaluationResult, type RfpAnalysis, type RfpAnalysisJob, type RfpCatalogItem, type StoredProject } from "./api";
import { DEMO_MODE, demoBDocumentCheck, demoEvaluation, demoFiles, demoPreview, demoRfpAnalysis } from "./demo";
import "./styles.css";

type View = "welcome" | "projects" | "create" | "setup" | "upload" | "quant" | "qual" | "pdf" | "results";
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

function rubricFromCatalog(catalog?: CatalogDraft) {
  const items = [...(catalog?.qualitative_evaluation_items ?? []), ...(catalog?.quantitative_evaluation_items ?? [])]
    .filter(item => item.source !== "evaluation_rule" && (item.source !== "manual" || item.name.trim()))
    .slice(0, 8)
    .map((item, index) => {
      const keywords = item.name.match(/[가-힣A-Za-z0-9]{2,}/g)?.filter(word => !["제안", "평가", "항목", "사업", "수행", "계획", "능력"].includes(word)) ?? [];
      return { id: `rfp-${index}`, name: item.name, source: item.source, importance: item.importance, condition: item.condition, rfp_requirement: item.rfp_requirement, max_score: 10, required_keywords: keywords.slice(0, 2).length ? keywords.slice(0, 2) : [item.name.replace(/\s+/g, "")] };
    });
  if (!items.length) throw new Error("RFP에서 검토한 평가 항목이 없습니다. 평가 항목을 추가하거나 RFP 추출 결과를 확인해 주세요.");
  return { items, adjustment_rules: (catalog?.quantitative_evaluation_items ?? []).filter(item => item.source === "evaluation_rule") };
}

function formatEvidenceText(text: string): string {
  const heading = /^(?:[ⅠⅡⅢⅣⅤ]+[.)]?|\d+(?:\.\d+)*[.)]?|[□•])\s*/;
  const lines = text.split(/\r?\n/).map(line => line.trim()).filter(Boolean);
  const paragraphs: string[] = [];
  let current = "";
  for (const line of lines) {
    if (heading.test(line) && current) {
      paragraphs.push(current);
      current = line;
    } else {
      current = current ? `${current} ${line}` : line;
    }
  }
  if (current) paragraphs.push(current);
  return paragraphs.join("\n\n");
}

function RfpProgress({ job }: { job?: RfpAnalysisJob }) {
  const total = job?.processing.page_count ?? 0;
  const done = job?.processing.processed_pages ?? 0;
  const percent = total ? Math.round((done / total) * 100) : 0;
  return <section className="analysis-progress" aria-live="polite">
    <div className="analysis-progress-label"><span>{total ? `RFP ${total}쪽 중 ${done}쪽 분석` : "RFP 분석을 준비하는 중…"}</span>{total ? <b>{percent}%</b> : null}</div>
    <div className="analysis-progress-track" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={percent}><i style={{ width: `${percent}%` }}/></div>
    <small>{job?.processing.cache_hit ? "저장된 텍스트·OCR 캐시를 재사용했습니다." : `OCR ${job?.processing.ocr_page_count ?? 0}쪽 처리`}</small>
  </section>;
}

function documentCategory(document: BDocumentCheck["results"][number]): "REQUIRED" | "GENERAL" {
  if (document.category) return document.category;
  // 실행 중인 이전 sidecar가 category를 아직 보내지 않아도 화면이 비지 않도록
  // 문서명으로 동일한 보수적 분류를 적용한다.
  return /(인증|필증|확약|서약)/.test(document.name) ? "REQUIRED" : "GENERAL";
}

function Stepper({ active }: { active: number }) {
  const labels = ["제안요청서 등록", "제안서 업로드", "평가 / 피드백", "완료"];
  return <div className="stepper">{labels.map((label, i) => <React.Fragment key={label}>
    <div className={"step " + (i <= active ? "active" : "")}><span>{i < active ? "✓" : i + 1}</span><b>{label}</b></div>{i < 3 && <i className={i < active ? "filled" : ""} />}
  </React.Fragment>)}</div>;
}

function Shell({ children, title, active, goHome, onPdfView }: { children: React.ReactNode; title: string; active?: number; goHome: () => void; onPdfView?: () => void }) {
  return <main className="app-shell"><aside><button className="mark" onClick={goHome}>A</button><div className="rail-brand">Arvis<br/>Check</div><span className="offline">● OFFLINE</span></aside><section className="workspace"><header><div><h1>{title}</h1><p>제안서 평가 AI 시스템</p></div><div className="header-actions">{onPdfView && <button className="secondary header-pdf" onClick={onPdfView}>PDF 원문보기</button>}<button className="ghost" onClick={goHome}>내 사업</button></div></header>{active !== undefined && <div className="step-row"><Stepper active={active} /></div>}{children}</section></main>;
}

function UploadBox({ kind, onUpload, onRemove, files }: { kind: FileKind; onUpload: (paths: string[]) => void; onRemove: (file: Uploaded) => void; files: Uploaded[] }) {
  const ref = useRef<HTMLInputElement>(null);
  const related = files.filter(file => file.kind === kind);
  const choose = async () => {
    try {
      const selected = await open({ multiple: kind !== "RFP", filters: [{ name: "PDF", extensions: ["pdf"] }] });
      if (selected) onUpload(Array.isArray(selected) ? selected : [selected]);
    } catch { ref.current?.click(); }
  };
  return <><button className="dropzone" onClick={choose}><span className="plus">＋</span><strong>{kind === "RFP" ? "RFP 파일을 끌어다 놓거나 클릭" : `${kind} 제안서를 끌어다 놓거나 클릭`}</strong><small>PDF · 파일당 300MB 이내</small></button><input ref={ref} hidden type="file" accept="application/pdf" multiple={kind !== "RFP"} onChange={e => {
    const selected = [...(e.target.files ?? [])];
    if (DEMO_MODE && selected.length) onUpload(selected.map(file => `demo://${file.name}`));
    else if (selected.length) window.alert("브라우저에서는 로컬 파일 경로를 백엔드에 전달할 수 없습니다. Arvis Check 데스크톱 창에서 파일을 선택해 주세요.");
    e.target.value = "";
  }} />
  {related.length > 0 && <div className="file-grid">{related.map((file, i) => <article className="file-card" key={file.name + i}><span>PDF</span><div><b>{file.name}</b><small>{file.pages}쪽 · 등록됨</small></div><em>완료</em><button type="button" className="remove-file" onClick={() => onRemove(file)} aria-label={`${file.name} 등록 해제`}>×</button></article>)}</div>}</>;
}

function App() {
  const [view, setView] = useState<View>("welcome");
  const [projectName, setProjectName] = useState("");
  const [files, setFiles] = useState<Uploaded[]>([]);
  const [isEvaluating, setEvaluating] = useState(false);
  const [isPreparingRfp, setPreparingRfp] = useState(false);
  const [rfpAnalysisProgress, setRfpAnalysisProgress] = useState<RfpAnalysisJob>();
  const [backendError, setBackendError] = useState("");
  const [evaluation, setEvaluation] = useState<EvaluationResult>();
  const [evaluationId, setEvaluationId] = useState<string>();
  const [evaluationProgress, setEvaluationProgress] = useState<Evaluation>();
  const [isGeneratingComments, setGeneratingComments] = useState(false);
  const [projectId, setProjectId] = useState<string>();
  const [rfpAnalysis, setRfpAnalysis] = useState<RfpAnalysis>();
  const [catalogDraft, setCatalogDraft] = useState<CatalogDraft>();
  const [ocrRenderScale, setOcrRenderScale] = useState(1.5);
  const [bDocumentCheck, setBDocumentCheck] = useState<BDocumentCheck>();
  const [isCheckingBDocuments, setCheckingBDocuments] = useState(false);
  const [bDocumentProgress, setBDocumentProgress] = useState<BDocumentCheckJob>();
  const [pdfVolume, setPdfVolume] = useState<"A" | "B">("A");
  const [pdfPage, setPdfPage] = useState<number>();
  const [pdfReturnView, setPdfReturnView] = useState<"quant" | "qual">("quant");
  const [savedProjects, setSavedProjects] = useState<StoredProject[]>([]);
  const loadDemoDocuments = () => { setFiles(demoFiles); setProjectName(name => name || "2026 정보화 사업 (데모)"); setBackendError(""); };
  const addFiles = (kind: FileKind) => (paths: string[]) => {
    setFiles(old => [...old, ...paths.map((path, i) => ({ name: path.split(/[\\/]/).pop() ?? path, pages: 30 + i * 4, kind, path }))]);
  };
  const removeFile = (file: Uploaded) => setFiles(old => old.filter(item => item !== file));
  const openPdf = (volume: "A" | "B", returnView: "quant" | "qual", page?: number) => {
    setPdfVolume(volume); setPdfPage(page); setPdfReturnView(returnView); setView("pdf");
  };
  const ensureBackend = async () => {
    try { await api.health(); } catch {
        try {
          await invoke("start_backend");
        } catch (sidecarError) {
          const message = sidecarError instanceof Error ? sidecarError.message : String(sidecarError);
          const isDesktop = Boolean((window as Window & { __TAURI_INTERNALS__?: unknown }).__TAURI_INTERNALS__);
          throw new Error(isDesktop ? `백엔드를 시작하지 못했습니다: ${message}` : "브라우저에서는 백엔드를 자동으로 시작할 수 없습니다. Arvis Check 데스크톱 창에서 실행해 주세요.");
        }
        let ready = false;
        for (let attempt = 0; attempt < 20; attempt += 1) {
          await new Promise(resolve => window.setTimeout(resolve, 250));
          try { await api.health(); ready = true; break; } catch { /* sidecar 초기화 대기 */ }
        }
        if (!ready) throw new Error("백엔드가 5초 안에 시작되지 않았습니다. Python sidecar 설정을 확인하세요.");
    }
  };
  const resumeProject = async (stored: StoredProject) => {
    setBackendError("");
    try {
      await ensureBackend();
      const project = await api.project(stored.id);
      const restoredFiles: Uploaded[] = (["RFP", "A", "B"] as const).flatMap(kind => project.documents[kind].map(path => ({ path, name: path.split(/[\\/]/).pop() ?? path, pages: 0, kind: kind === "RFP" ? "RFP" : kind === "A" ? "A권" : "B권" })));
      setProjectId(project.id); setProjectName(project.name); setFiles(restoredFiles);
      if (project.rfp_analysis) { setRfpAnalysis(project.rfp_analysis); setCatalogDraft(draftFromAnalysis(project.rfp_analysis)); setView("setup"); }
      else setView("create");
    } catch (error) { setBackendError(error instanceof Error ? error.message : "사업을 열지 못했습니다."); }
  };
  useEffect(() => {
    if (view !== "projects") return;
    if (DEMO_MODE) { setSavedProjects([]); setBackendError(""); return; }
    let cancelled = false;
    void (async () => {
      try {
        await ensureBackend();
        const response = await api.projects();
        if (!cancelled) setSavedProjects(response.projects);
      } catch (error) {
        if (!cancelled) setBackendError(error instanceof Error ? error.message : "사업 목록을 불러오지 못했습니다.");
      }
    })();
    return () => { cancelled = true; };
  }, [view]);
  const prepareRfp = async () => {
    const rfpPaths = files.filter(file => file.kind === "RFP" && file.path).map(file => file.path!);
    if (!rfpPaths.length) { setBackendError("분석할 제안요청서(RFP) PDF를 먼저 선택해 주세요."); return; }
    setBackendError(""); setPreparingRfp(true);
    try {
      if (DEMO_MODE) {
        setProjectId("demo-project"); setRfpAnalysis(demoRfpAnalysis); setCatalogDraft(draftFromAnalysis(demoRfpAnalysis)); setView("setup");
        return;
      }
      await ensureBackend();
      const project = await api.createProject(projectName);
      await api.registerDocuments(project.id, "RFP", rfpPaths);
      for (const [kind, apiKind] of [["A권", "A"], ["B권", "B"]] as const) {
        const paths = files.filter(file => file.kind === kind && file.path).map(file => file.path!);
        if (paths.length) await api.registerDocuments(project.id, apiKind, paths);
      }
      setProjectId(project.id); setView("setup");
      let job = await api.analyzeRfp(project.id); setRfpAnalysisProgress(job);
      while (job.status === "RUNNING") { await new Promise(resolve => window.setTimeout(resolve, 350)); job = await api.rfpAnalysis(job.id); setRfpAnalysisProgress(job); }
      if (job.status === "FAILED" || !job.result) throw new Error(job.error ?? "RFP 분석에 실패했습니다.");
      const analysis = job.result;
      setRfpAnalysis(analysis); setCatalogDraft(draftFromAnalysis(analysis));
    } catch (error) { setBackendError(error instanceof Error ? error.message : "RFP 분석에 실패했습니다."); }
    finally { setPreparingRfp(false); }
  };
  const reanalyzeRfp = async () => {
    if (!projectId) return;
    setBackendError(""); setPreparingRfp(true);
    try {
      if (DEMO_MODE) { setRfpAnalysis(demoRfpAnalysis); setCatalogDraft(draftFromAnalysis(demoRfpAnalysis)); return; }
      await ensureBackend();
      let job = await api.analyzeRfp(projectId); setRfpAnalysisProgress(job);
      while (job.status === "RUNNING") { await new Promise(resolve => window.setTimeout(resolve, 350)); job = await api.rfpAnalysis(job.id); setRfpAnalysisProgress(job); }
      if (job.status === "FAILED" || !job.result) throw new Error(job.error ?? "RFP 재분석에 실패했습니다.");
      const analysis = job.result;
      setRfpAnalysis(analysis); setCatalogDraft(draftFromAnalysis(analysis));
    } catch (error) { setBackendError(error instanceof Error ? error.message : "RFP 재분석에 실패했습니다."); }
    finally { setPreparingRfp(false); }
  };
  const beginEvaluation = async () => {
    setBackendError(""); setEvaluating(true);
    try {
      if (!projectId) throw new Error("제안요청서 분석을 먼저 완료해 주세요.");
      if (DEMO_MODE) { setEvaluation(demoEvaluation); setEvaluationId("demo-evaluation"); setView("quant"); return; }
      await ensureBackend();
      for (const [kind, apiKind] of [["A권", "A"], ["B권", "B"]] as const) {
        const paths = files.filter(file => file.kind === kind && file.path).map(file => file.path!);
        await api.registerDocuments(projectId, apiKind, paths);
      }
      const rubric = rubricFromCatalog(catalogDraft);
      let job = await api.evaluate(projectId, rubric, ocrRenderScale); setEvaluationProgress(job);
      while (job.status === "RUNNING") { await new Promise(resolve => window.setTimeout(resolve, 700)); job = await api.evaluation(job.id); setEvaluationProgress(job); }
      if (job.status === "FAILED" || !job.result) throw new Error(job.error ?? "평가에 실패했습니다.");
      setEvaluation(job.result); setEvaluationId(job.id); setView("quant");
    } catch (error) { setBackendError(error instanceof Error ? error.message : "백엔드 연결에 실패했습니다."); }
    finally { setEvaluating(false); }
  };
  const generateComments = async () => {
    if (!evaluationId) return;
    setGeneratingComments(true); setBackendError("");
    try {
      let job = await api.generateComments(evaluationId); setEvaluationProgress(job);
      while (job.comment_status === "RUNNING") { await new Promise(resolve => window.setTimeout(resolve, 700)); job = await api.evaluation(job.id); setEvaluationProgress(job); }
      if (job.comment_status === "FAILED") throw new Error(job.error ?? "AI 코멘트 생성에 실패했습니다.");
      if (job.result) setEvaluation(job.result);
    } catch (error) { setBackendError(error instanceof Error ? error.message : "AI 코멘트 생성에 실패했습니다."); }
    finally { setGeneratingComments(false); }
  };
  const checkBDocuments = async () => {
    if (!projectId) return;
    setCheckingBDocuments(true); setBackendError("");
    try { if (DEMO_MODE) { setBDocumentCheck(demoBDocumentCheck); return; } let job = await api.checkBDocuments(projectId); setBDocumentProgress(job); while (job.status === "RUNNING") { await new Promise(resolve => window.setTimeout(resolve, 700)); job = await api.bDocumentCheck(job.id); setBDocumentProgress(job); } if (job.status === "FAILED") throw new Error(job.error ?? "B권 확인에 실패했습니다."); setBDocumentCheck({ document_count: job.results?.length ?? 0, results: job.results ?? [] }); }
    catch (error) { setBackendError(error instanceof Error ? error.message : "B권 필수 서류 확인에 실패했습니다."); }
    finally { setCheckingBDocuments(false); }
  };
  const updateCatalogItem = (section: CatalogSection, localId: string, patch: Partial<EditableCatalogItem>) => setCatalogDraft(old => old ? ({ ...old, [section]: old[section].map(item => item.localId === localId ? { ...item, ...patch } : item) }) : old);
  const removeCatalogItem = (section: CatalogSection, localId: string) => setCatalogDraft(old => old ? ({ ...old, [section]: old[section].filter(item => item.localId !== localId) }) : old);
  const addCatalogItem = (section: CatalogSection, groupPage?: number) => setCatalogDraft(old => {
    if (!old) return old;
    const prefix = section === "required_documents" ? "필수 서류" : section === "quantitative_evaluation_items" ? "정량 평가 항목" : "정성 평가 항목";
    const item: EditableCatalogItem = { localId: `${section}-${Date.now()}`, name: `새 ${prefix}`, importance: section === "quantitative_evaluation_items" ? "general" : undefined, source: "manual", evidence: { page: groupPage ?? 0, text: "평가위원이 추가한 항목" }, review_required: true };
    return { ...old, [section]: [...old[section], item] };
  });

  if (view === "welcome") return <div className="welcome"><div className="welcome-side"><div className="wordmark">Arvis<br/>Check</div><div className="abstract">✦<br/>⌁<br/>✧</div><span>OFFLINE AI PROPOSAL REVIEW</span></div><div className="welcome-main"><div className="welcome-card"><div className="welcome-icon">＋</div><label>WELCOME TO ARVIS CHECK</label><h1>첫 사업을 시작해 보세요</h1><p>제안요청서를 등록하고 제안서를 올리면<br/>Arvis Check가 정량·정성 평가를 도와드립니다.</p><button className="primary big" onClick={() => setView("projects")}>사업 시작하기 <span>→</span></button></div></div></div>;

  if (view === "projects") return <Shell title="내 사업" goHome={() => setView("projects")}><div className="content"><div className="section-title"><span>{savedProjects.length ? `${savedProjects.length}건의 사업` : "등록된 사업이 없습니다."}</span><button className="primary" onClick={() => setView("create")}>＋ 사업 추가</button></div><div className="project-grid">{savedProjects.map(project => <button className="project-card" key={project.id} onClick={() => void resumeProject(project)}><span className="project-icon">▤</span><b>{project.name}</b><small>RFP {project.documents.RFP.length} · A권 {project.documents.A.length} · B권 {project.documents.B.length}</small><em className={project.has_rfp_analysis ? "ready" : "warn"}>{project.has_rfp_analysis ? "검토 가능" : "문서 등록 중"}</em></button>)}<button className="add-project" onClick={() => setView("create")}>＋<b>{savedProjects.length ? "사업 추가" : "첫 사업 추가"}</b></button></div>{backendError && <p className="backend-error">{backendError}</p>}</div></Shell>;

  if (view === "create") return <Shell title="새 사업 만들기" goHome={() => setView("projects")}><div className="center-content"><div className="form-card document-start"><label>사업명</label><input value={projectName} onChange={e => setProjectName(e.target.value)} placeholder="사업명을 작성하세요"/>{DEMO_MODE && <button type="button" className="demo-load" onClick={loadDemoDocuments}>데모 문서 불러오기</button>}<div className="document-start-head"><b>평가 문서 등록</b><small>제안요청서와 제안서를 함께 등록할 수 있습니다. A·B권은 나중에 보완해도 됩니다.</small></div><div className="document-start-grid"><section><h2>제안요청서 <small>RFP · 평가 기준 추출</small></h2><UploadBox kind="RFP" files={files} onUpload={addFiles("RFP")} onRemove={removeFile} /></section><section><h2>기본 제안서 <small>A권 · 정성 평가</small></h2><UploadBox kind="A권" files={files} onUpload={addFiles("A권")} onRemove={removeFile} /></section><section><h2>제안회사 소개 · 증빙자료 <small>B권 · 정량 평가</small></h2><UploadBox kind="B권" files={files} onUpload={addFiles("B권")} onRemove={removeFile} /></section></div><div className="actions"><button className="secondary" onClick={() => setView("projects")}>취소</button><button className="primary" disabled={isPreparingRfp} onClick={prepareRfp}>{isPreparingRfp ? "RFP 분석 중…" : "문서 등록 및 다음 →"}</button></div>{backendError && <p className="backend-error">{backendError}</p>}</div></div></Shell>;

  if (view === "setup") {
    const draft = catalogDraft;
    return <Shell title={projectName} active={1} goHome={() => setView("projects")}><div className="content narrow">
      <article className="info-card"><span className="doc-icon">▤</span><div><b>제안요청서 자동 분석</b><small>{files.find(file => file.kind === "RFP")?.name} · {rfpAnalysis?.notice ?? "분석 결과 없음"}</small></div><button className="link" disabled={isPreparingRfp} onClick={reanalyzeRfp}>{isPreparingRfp ? "재분석 중…" : "RFP 다시 분석"}</button></article>
      {isPreparingRfp && <RfpProgress job={rfpAnalysisProgress}/>}
      {!isPreparingRfp && <><EditableSettings title="필수 서류" empty="추출된 필수 서류가 없습니다. RFP 원문을 검토하세요." items={draft?.required_documents ?? []} onUpdate={(id, patch) => updateCatalogItem("required_documents", id, patch)} onRemove={id => removeCatalogItem("required_documents", id)} onAdd={() => addCatalogItem("required_documents")}/><QuantitativeSettings items={draft?.quantitative_evaluation_items ?? []} onUpdate={(id, patch) => updateCatalogItem("quantitative_evaluation_items", id, patch)} onRemove={id => removeCatalogItem("quantitative_evaluation_items", id)} onAdd={page => addCatalogItem("quantitative_evaluation_items", page)}/><EditableSettings title="정성 평가 항목" empty="추출된 정성 평가 항목이 없습니다. 상대평가 표기를 확인하세요." items={draft?.qualitative_evaluation_items ?? []} onUpdate={(id, patch) => updateCatalogItem("qualitative_evaluation_items", id, patch)} onRemove={id => removeCatalogItem("qualitative_evaluation_items", id)} onAdd={() => addCatalogItem("qualitative_evaluation_items")}/><label className="ocr-quality">OCR 품질/속도 <select value={ocrRenderScale} disabled={isEvaluating} onChange={event => setOcrRenderScale(Number(event.target.value))}><option value={1}>고속 · 1.0배</option><option value={1.25}>균형 · 1.25배</option><option value={1.5}>정확도 우선 · 1.5배</option></select><small>작은 글자·표·도장이 많으면 1.5배를 사용하세요. 배율별 결과는 별도 캐시됩니다.</small></label><div className="actions"><button className="secondary" disabled={isEvaluating} onClick={() => setView("create")}>문서 수정</button><button className="primary" disabled={isEvaluating} onClick={beginEvaluation}>{isEvaluating ? evaluationProgress?.progress_message ?? "문서를 분석하는 중…" : "평가 시작 →"}</button></div>{isEvaluating && <section className="analysis-progress" aria-live="polite"><div className="analysis-progress-label"><span>{evaluationProgress?.processing?.page_count ? `${evaluationProgress.processing.page_count}쪽 중 ${evaluationProgress.processing.processed_pages ?? 0}쪽 처리` : "처리 단계를 확인하는 중…"}</span></div><div className="analysis-progress-track" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={evaluationProgress?.processing?.page_count ? Math.round(((evaluationProgress.processing.processed_pages ?? 0) / evaluationProgress.processing.page_count) * 100) : 0}><i style={{ width: `${evaluationProgress?.processing?.page_count ? Math.round(((evaluationProgress.processing.processed_pages ?? 0) / evaluationProgress.processing.page_count) * 100) : 0}%` }}/></div></section>}</>}
      {backendError && <p className="backend-error">{backendError}</p>}
    </div></Shell>;
  }

  if (view === "upload") { const processing = evaluationProgress?.processing; const total = processing?.page_count ?? 0; const processed = processing?.processed_pages ?? 0; const percent = total ? Math.round((processed / total) * 100) : 0; return <Shell title={projectName} active={1} goHome={() => setView("projects")}><div className="content narrow"><div className="document-confirm-head"><div><h2>등록 문서 확인</h2><p>평가에 사용할 문서를 확인하고, 필요하면 추가하거나 등록을 해제하세요.</p></div><span>{files.length}개 파일 등록됨</span></div><article className="info-card registration-summary"><span className="doc-icon">PDF</span><div><b>제안요청서</b><small>{files.filter(file => file.kind === "RFP").map(file => file.name).join(", ") || "등록되지 않음"}</small></div></article><div className="upload-columns"><section><h2>기본 제안서 <small>A권 · 정성 평가</small></h2><UploadBox kind="A권" files={files} onUpload={addFiles("A권")} onRemove={removeFile} /></section><section><h2>제안회사 소개 · 증빙자료 <small>B권 · 정량 평가</small></h2><UploadBox kind="B권" files={files} onUpload={addFiles("B권")} onRemove={removeFile} /></section></div><label className="ocr-quality">OCR 품질/속도 <select value={ocrRenderScale} disabled={isEvaluating} onChange={event => setOcrRenderScale(Number(event.target.value))}><option value={1}>고속 · 1.0배</option><option value={1.25}>균형 · 1.25배</option><option value={1.5}>정확도 우선 · 1.5배</option></select><small>작은 글자·표·도장이 많으면 1.5배를 사용하세요. 배율별 결과는 별도 캐시됩니다.</small></label><div className="actions"><button className="secondary" onClick={() => setView("setup")}>이전</button><button className="primary" disabled={isEvaluating} onClick={beginEvaluation}>{isEvaluating ? evaluationProgress?.progress_message ?? "문서를 분석하는 중…" : "평가 시작 →"}</button></div>{isEvaluating && <section className="analysis-progress" aria-live="polite"><div className="analysis-progress-label"><span>{total ? `${total}쪽 중 ${processed}쪽 처리 · OCR ${processing?.ocr_page_count ?? 0}쪽` : "처리 단계를 확인하는 중…"}</span>{total > 0 && <b>{percent}%</b>}</div><div className="analysis-progress-track" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={percent}><i style={{ width: `${percent}%` }}/></div><small>{processing?.cache_hit ? "저장된 추출 결과를 재사용했습니다." : evaluationProgress?.progress_message}</small></section>}{backendError && <p className="backend-error">{backendError}</p>}</div></Shell>; }

  if (view === "quant") return <Feedback type="quant" projectName={projectName} projectId={projectId} result={evaluation} evaluationId={evaluationId} bDocumentCheck={bDocumentCheck} adjustmentResults={evaluation?.adjustment_results ?? []} bDocumentProgress={bDocumentProgress} isCheckingBDocuments={isCheckingBDocuments} onCheckBDocuments={checkBDocuments} goHome={() => setView("projects")} navigate={setView} onOpenPdf={(volume, page) => openPdf(volume, "quant", page)} onGenerateComments={generateComments} commentStatus={evaluationProgress?.comment_status} isGeneratingComments={isGeneratingComments}/>;
  if (view === "qual") return <Feedback type="qual" projectName={projectName} projectId={projectId} result={evaluation} evaluationId={evaluationId} goHome={() => setView("projects")} navigate={setView} onOpenPdf={(volume, page) => openPdf(volume, "qual", page)} onGenerateComments={generateComments} commentStatus={evaluationProgress?.comment_status} isGeneratingComments={isGeneratingComments}/>;
  if (view === "pdf") return <PdfViewer projectName={projectName} projectId={projectId} volume={pdfVolume} page={pdfPage} onVolumeChange={setPdfVolume} goBack={() => setView(pdfReturnView)} goHome={() => setView("projects")}/>;
  return <Results projectName={projectName} result={evaluation} goHome={() => setView("projects")} navigate={setView}/>;
}

type SettingEditorProps = { title: string; empty: string; items: EditableCatalogItem[]; onUpdate: (id: string, patch: Partial<EditableCatalogItem>) => void; onRemove: (id: string) => void; onAdd: () => void };
function EditableSettings({ title, empty, items, onUpdate, onRemove, onAdd }: SettingEditorProps) { return <article className="settings"><h2>{title}</h2><p>제안요청서에서 추출한 기준입니다. 항목명을 직접 수정하거나 추가할 수 있습니다.</p><div className="setting-items">{items.length ? items.map(item => <EditableItemRow key={item.localId} item={item} onUpdate={onUpdate} onRemove={onRemove}/>) : <div className="empty-item">{empty}</div>}<button type="button" onClick={onAdd}>＋ 항목 추가</button></div></article>; }
function EditableItemRow({ item, onUpdate, onRemove, showImportance = false }: { item: EditableCatalogItem; onUpdate: (id: string, patch: Partial<EditableCatalogItem>) => void; onRemove: (id: string) => void; showImportance?: boolean }) { return <div className="editable-item"><input aria-label="항목명" value={item.name} onChange={event => onUpdate(item.localId, { name: event.target.value })}/>{showImportance && <select aria-label="항목 구분" value={item.importance ?? "general"} onChange={event => onUpdate(item.localId, { importance: event.target.value })}><option value="required">필수</option><option value="general">일반</option><option value="unknown">검토 필요</option></select>}<small>{item.source === "manual" ? "직접 추가" : `RFP ${item.evidence.page ? `${item.evidence.page}쪽` : "추출"}`}</small><button type="button" className="delete-item" onClick={() => onRemove(item.localId)} aria-label="항목 삭제">×</button></div>; }
function QuantitativeSettings({ items, onUpdate, onRemove, onAdd }: { items: EditableCatalogItem[]; onUpdate: (id: string, patch: Partial<EditableCatalogItem>) => void; onRemove: (id: string) => void; onAdd: (page?: number) => void }) { const [open, setOpen] = useState<Record<string, boolean>>({}); const groups = new Map<string, { label: string; page?: number; items: EditableCatalogItem[] }>(); for (const item of items) { const page = item.evidence.page || undefined; const label = item.source === "evaluation_rule" ? `평가 규칙 · ${page ?? "원문"}쪽` : item.source === "manual" ? "직접 추가 항목" : `체계규격 표 · ${page ?? "원문"}쪽`; const group = groups.get(label) ?? { label, page, items: [] }; group.items.push(item); groups.set(label, group); } return <article className="settings"><h2>정량 평가 항목</h2><p>같은 체계규격 표에서 추출된 세부 항목을 묶었습니다. 그룹을 펼쳐 확인·수정하세요.</p><div className="quant-groups">{[...groups.values()].map(group => <section className="quant-group" key={group.label}><button type="button" className="group-toggle" onClick={() => setOpen(old => ({ ...old, [group.label]: !old[group.label] }))}><span>{open[group.label] ? "⌄" : "›"}</span><b>{group.label}</b><em>{group.items.length}개 항목</em></button>{open[group.label] && <div className="group-items">{group.items.map(item => <EditableItemRow key={item.localId} item={item} onUpdate={onUpdate} onRemove={onRemove} showImportance/>)}<button type="button" className="add-in-group" onClick={() => onAdd(group.page)}>＋ 이 표에 항목 추가</button></div>}</section>)}</div><button type="button" className="manual-add" onClick={() => onAdd()}>＋ 별도 항목 추가</button></article>; }

function Feedback({ type, projectName, projectId, result, bDocumentCheck, adjustmentResults = [], isCheckingBDocuments, onCheckBDocuments, goHome, navigate, onOpenPdf }: { type: "quant" | "qual"; projectName: string; projectId?: string; result?: EvaluationResult; evaluationId?: string; bDocumentCheck?: BDocumentCheck; adjustmentResults?: AdjustmentResult[]; bDocumentProgress?: BDocumentCheckJob; isCheckingBDocuments?: boolean; onCheckBDocuments?: () => void; goHome: () => void; navigate: (view: View) => void; onOpenPdf: (volume: "A" | "B", page?: number) => void; onGenerateComments: () => void; commentStatus?: Evaluation["comment_status"]; isGeneratingComments: boolean }) {
  const item = result?.item_results[type === "quant" ? 1 : 0];
  const [tab, setTab] = useState<"required" | "general" | "adjustments">("required");
  const [viewer, setViewer] = useState<{ label: string; url: string; page?: number; image?: boolean }>();
  const tabs = [["required", "필수서류 검증"], ["general", "일반서류 검증"], ["adjustments", "가/감점 확인"]] as const;
  const openEvidence = (page: number) => {
    if (!projectId) return;
    setViewer({ label: `B권 ${page}쪽`, url: DEMO_MODE ? demoPreview : `http://127.0.0.1:8788/projects/${projectId}/b-page/${page}`, page, image: true });
  };

  return <Shell title={projectName} active={2} goHome={goHome} onPdfView={() => onOpenPdf(viewer?.image ? "B" : "A", viewer?.page)}>
    <div className="feedback">
      <article className="document">
        <small>분석 문서 · {result?.page_count ?? "-"}페이지</small>
        <nav className="evaluation-mode-tabs" aria-label="평가 유형">
          <button type="button" className={type === "quant" ? "active" : ""} onClick={() => navigate("quant")}>정량평가</button>
          <button type="button" className={type === "qual" ? "active" : ""} onClick={() => navigate("qual")}>정성평가</button>
        </nav>
        <h2>{type === "quant" ? "정량평가" : item?.name ?? "정성평가"}</h2>
        {type === "quant" ? <>
          <nav className="quant-tabs" aria-label="정량평가 검증 항목">
            {tabs.map(([id, label]) => <button key={id} type="button" className={tab === id ? "active" : ""} onClick={() => setTab(id)}><b>{label}</b></button>)}
          </nav>
          {tab === "required" && <DocumentVerificationPanel title="B권 필수 서류 확인" empty="필수 서류 확인을 실행하면 RFP의 필수 제출·인증·확약 항목을 B권에서 확인합니다." documents={bDocumentCheck?.results.filter(doc => documentCategory(doc) === "REQUIRED") ?? []} isChecking={isCheckingBDocuments} onCheck={onCheckBDocuments} viewer={viewer} onSelect={openEvidence}/>}
          {tab === "general" && <DocumentVerificationPanel title="B권 일반 서류 확인" empty="RFP에서 일반 제출·증빙 항목을 찾지 못했습니다. RFP 검토 화면에서 항목을 추가할 수 있습니다." documents={bDocumentCheck?.results.filter(doc => documentCategory(doc) === "GENERAL") ?? []} isChecking={isCheckingBDocuments} onCheck={onCheckBDocuments} viewer={viewer} onSelect={openEvidence}/>}
          {tab === "adjustments" && <AdjustmentPanel projectId={projectId} results={adjustmentResults} onOpenPdf={onOpenPdf}/>}
        </> : <section className="evidence"><b>실제 원문 근거</b><p>{formatEvidenceText(item?.evidence ?? "원문 근거가 없습니다.")}</p></section>}
      </article>
      <div className="feedback-actions">
        <button className="primary" onClick={goHome}>결과 보기</button>
      </div>
    </div>
  </Shell>;
}

function DocumentVerificationPanel({ title, empty, documents, isChecking, onCheck, viewer, onSelect }: { title: string; empty: string; documents: BDocumentCheck["results"]; isChecking?: boolean; onCheck?: () => void; viewer?: { label: string; url: string; page?: number; image?: boolean }; onSelect: (page: number) => void }) {
  return <div className="required-layout">
    <section className="required-docs">
      <div><div><b>{title}</b></div><button className="secondary" onClick={onCheck} disabled={isChecking}>{isChecking ? "확인 중…" : "서류 확인"}</button></div>
      {documents.length ? documents.map(doc => <button className="required-doc-link" key={doc.name} disabled={!doc.page} onClick={() => doc.page && onSelect(doc.page)}><span><b>{doc.name}</b><small>{doc.status === "FOUND" ? "확인됨" : doc.status === "REVIEW_REQUIRED" ? "검토 필요" : "미확인"}</small></span><small>{doc.page ? `B권 ${doc.page}쪽 보기 →` : "근거 없음"}</small></button>) : <p className="tab-hint">{empty}</p>}
    </section>
    <section className="reference-preview">
      <div><b>참조 페이지</b><small>{viewer?.image && viewer.page ? `B권 ${viewer.page}쪽` : "서류를 선택하세요"}</small></div>
      {viewer?.image ? <img src={viewer.url} alt={viewer.label}/> : <p>왼쪽 목록에서 확인된 서류를 선택하면 해당 B권 페이지가 표시됩니다.</p>}
    </section>
  </div>;
}

function adjustmentLabel(result: AdjustmentResult) {
  const kind = result.effect === "bonus_candidate" ? "가점" : result.effect === "deduction_candidate" ? "감점" : result.effect === "disqualification_candidate" ? "불합격 후보" : result.effect === "pass_threshold" ? "합격 기준" : "평가 규칙";
  const applied = result.applied_delta === null || result.applied_delta === undefined ? "" : ` ${result.applied_delta > 0 ? "+" : ""}${result.applied_delta}점`;
  return `${kind}${applied}${result.cap ? ` · 최대 ${result.cap}점` : ""}`;
}

function adjustmentStatus(status: AdjustmentResult["status"]) {
  return status === "APPLIED" ? "감점 적용" : status === "TRIGGERED" ? "조건 발생" : status === "NOT_APPLIED" ? "미적용" : "검토 필요";
}

function proposalBasis(item: AdjustmentResult["related_items"][number]) {
  return item.proposal_basis?.trim() || "이전 형식으로 저장된 평가 결과입니다. RFP를 다시 분석한 뒤 평가를 다시 실행하면 제안서 수치·원문 근거가 표시됩니다.";
}

function AdjustmentPanel({ projectId, results, onOpenPdf }: { projectId?: string; results: AdjustmentResult[]; onOpenPdf: (volume: "A" | "B", page?: number) => void }) {
  const [selected, setSelected] = useState<AdjustmentResult>();
  const [previewKind, setPreviewKind] = useState<"proposal" | "rfp">("proposal");
  const previewUrl = selected && projectId && selected.rfp_page ? `http://127.0.0.1:8788/projects/${projectId}/rfp-page/${selected.rfp_page}` : undefined;
  const proposalPage = selected?.related_items.flatMap(item => item.evidence_pages)[0];
  const proposalPreviewPage = proposalPage ?? 1;
  const proposalPreviewUrl = selected && projectId ? `http://127.0.0.1:8788/projects/${projectId}/a-page/${proposalPreviewPage}` : undefined;
  return <div className="required-layout adjustment-layout">
    <section className="required-docs adjustment-list">
      <div><div><b>가/감점 적용 내역</b><small>RFP 규칙과 실제 평가 항목을 연결한 결과입니다.</small></div></div>
      {results.length ? results.map(result => <button className={`adjustment-rule ${selected?.rule_type === result.rule_type && selected.rfp_page === result.rfp_page ? "selected" : ""}`} type="button" key={`${result.rule_type}-${result.rfp_page}`} onClick={() => { setSelected(result); setPreviewKind("proposal"); }}><span><b>{adjustmentLabel(result)}</b><small>{result.related_items.length ? result.related_items.map(item => `${item.name} (${item.status === "MISSING" ? "미충족" : "충족"})`).join(", ") : "현재 평가 결과만으로 자동 판정할 수 없습니다."}</small></span><em className={result.status.toLowerCase()}>{adjustmentStatus(result.status)}</em><small>RFP {result.rfp_page}쪽 근거 보기 →</small></button>) : <p className="tab-hint">RFP에서 명시적인 가점·감점·불합격 규칙을 추출하지 못했습니다. 제안요청서 검토 화면에서 평가 규칙을 추가해 주세요.</p>}
    </section>
    <section className="reference-preview adjustment-preview">
      <div><b>{previewKind === "proposal" ? "제안서 판정 근거" : "RFP 규칙 원문"}</b><small>{selected ? previewKind === "proposal" ? proposalPage ? `A권 ${proposalPage}쪽` : "A권 1쪽 · 직접 근거 없음" : `RFP ${selected.rfp_page}쪽` : "적용 내역을 선택하세요"}</small></div>
      {selected ? <><div className="adjustment-preview-tabs"><button type="button" className={previewKind === "proposal" ? "active" : ""} onClick={() => setPreviewKind("proposal")}>제안서 근거</button><button type="button" className={previewKind === "rfp" ? "active" : ""} onClick={() => setPreviewKind("rfp")}>RFP 원문</button></div>{previewKind === "proposal" ? proposalPreviewUrl ? <img src={proposalPreviewUrl} alt={`A권 ${proposalPreviewPage}쪽 제안서 근거`}/> : <p className="proposal-missing">{selected.related_items[0] ? proposalBasis(selected.related_items[0]) : "이 규칙은 제안서 자동 판정 대상이 아니므로 평가위원 확인이 필요합니다."}</p> : previewUrl ? <img src={previewUrl} alt={`RFP ${selected.rfp_page}쪽 규칙 원문`}/> : <p className="proposal-missing">RFP 원문 페이지를 찾지 못했습니다.</p>}<p className="rule-excerpt"><b>제안서 판정</b>{selected.related_items.length ? selected.related_items.map(item => `${proposalBasis(item)}${item.score_delta ? ` (${item.score_delta}점)` : ""}`).join("\n\n") : "이 규칙은 제안서 자동 판정 대상이 아니므로 평가위원 확인이 필요합니다."}{selected.related_items.some(item => item.proposal_excerpt) && <>\n\n<b>확인된 원문 발췌</b>{selected.related_items.find(item => item.proposal_excerpt)?.proposal_excerpt}</>}<br/><br/><b>적용한 RFP 규칙</b>{selected.rfp_excerpt}</p><button className="secondary adjustment-pdf-button" onClick={() => onOpenPdf("A", proposalPreviewPage)}>A권 원문에서 보기</button></> : <p>왼쪽의 적용 내역을 선택하면 제안서 판정 근거와 적용한 RFP 규칙을 함께 표시합니다.</p>}
    </section>
  </div>;
}

function PdfViewer({ projectName, projectId, volume, page, onVolumeChange, goBack, goHome }: { projectName: string; projectId?: string; volume: "A" | "B"; page?: number; onVolumeChange: (volume: "A" | "B") => void; goBack: () => void; goHome: () => void }) {
  const url = DEMO_MODE ? undefined : projectId ? `http://127.0.0.1:8788/projects/${projectId}/${volume.toLowerCase()}-document${page ? `#page=${page}` : ""}` : undefined;
  return <Shell title={projectName} active={2} goHome={goHome}>
    <div className="pdf-screen">
      <div className="pdf-screen-toolbar"><div><b>PDF 원문보기</b><small>{volume}권 원문{page ? ` · ${page}쪽` : ""}</small></div><div className="pdf-screen-actions"><div className="pdf-volume-tabs"><button type="button" className={volume === "A" ? "active" : ""} onClick={() => onVolumeChange("A")}>A권 원문</button><button type="button" className={volume === "B" ? "active" : ""} onClick={() => onVolumeChange("B")}>B권 원문</button></div><button type="button" className="secondary" onClick={goBack}>평가로 돌아가기</button></div></div>
      {url ? <iframe className="full-pdf" title={`${volume}권 원문`} src={url}/> : DEMO_MODE ? <img className="full-pdf" src={demoPreview} alt={`${volume}권 데모 원문 미리보기`}/> : <div className="pdf-placeholder">표시할 원문 PDF가 없습니다.</div>}
    </div>
  </Shell>;
}

function LegacyTabsFeedback({ type, projectName, projectId, result, evaluationId, bDocumentCheck, isCheckingBDocuments, onCheckBDocuments, goHome, navigate }: { type: "quant" | "qual"; projectName: string; projectId?: string; result?: EvaluationResult; evaluationId?: string; bDocumentCheck?: BDocumentCheck; bDocumentProgress?: BDocumentCheckJob; isCheckingBDocuments?: boolean; onCheckBDocuments?: () => void; goHome: () => void; navigate: (view: View) => void; onGenerateComments: () => void; commentStatus?: Evaluation["comment_status"]; isGeneratingComments: boolean }) {
  const item = result?.item_results[type === "quant" ? 1 : 0]; const [viewer, setViewer] = useState<{url: string; page: number} | undefined>(evaluationId && item?.evidence_pages[0] ? {url: `http://127.0.0.1:8788/evaluations/${evaluationId}/document`, page: item.evidence_pages[0]} : undefined);
  return <Shell title={projectName} active={2} goHome={goHome}><div className="feedback"><article className="document"><h2>{item?.name ?? "평가 항목"}</h2><p>{formatEvidenceText(item?.evidence ?? "원문 근거가 없습니다.")}</p>{type === "quant" && <section className="required-docs"><div><b>B권 필수 서류 확인</b><button className="secondary" onClick={onCheckBDocuments} disabled={isCheckingBDocuments}>{isCheckingBDocuments ? "확인 중…" : "필수 서류 확인"}</button></div>{bDocumentCheck?.results.map(doc => <button className="required-doc-link" key={doc.name} onClick={() => projectId && doc.page && setViewer({url: `http://127.0.0.1:8788/projects/${projectId}/b-page/${doc.page}`, page: doc.page})}><b>{doc.name}</b><small>{doc.page ? `B권 ${doc.page}쪽` : "미확인"}</small></button>)}</section>}</article><aside className="feedback-side"><b>원본 PDF</b>{viewer ? viewer.url.includes("/b-page/") ? <img className="pinned-pdf" alt={`B권 ${viewer.page}쪽`} src={viewer.url}/> : <iframe className="pinned-pdf" title="원본 PDF" src={`${viewer.url}?viewer_page=${viewer.page}#page=${viewer.page}`}/> : <p>근거 페이지를 선택하세요.</p>}<button className="secondary" onClick={() => navigate(type === "quant" ? "qual" : "quant")}>평가 전환</button><button className="primary" onClick={goHome}>결과 보기</button></aside></div></Shell>;
}

function LegacyPinnedFeedback({ type, projectName, projectId, result, evaluationId, bDocumentCheck, bDocumentProgress, isCheckingBDocuments, onCheckBDocuments, goHome, navigate, onGenerateComments, commentStatus, isGeneratingComments }: { type: "quant" | "qual"; projectName: string; projectId?: string; result?: EvaluationResult; evaluationId?: string; bDocumentCheck?: BDocumentCheck; bDocumentProgress?: BDocumentCheckJob; isCheckingBDocuments?: boolean; onCheckBDocuments?: () => void; goHome: () => void; navigate: (view: View) => void; onGenerateComments: () => void; commentStatus?: Evaluation["comment_status"]; isGeneratingComments: boolean }) {
  const item = result?.item_results[type === "quant" ? 1 : 0];
  const pages = item?.citation_pages.length ? item.citation_pages : item?.evidence_pages ?? [];
  const [page, setPage] = useState(pages[0]);
  const evidence = formatEvidenceText(item?.evidence || "확인 가능한 원문 발췌가 없습니다.");
  const pdfUrl = evaluationId && page ? `http://127.0.0.1:8788/evaluations/${evaluationId}/document#page=${page}` : undefined;
  const bProgress = bDocumentProgress?.processing; return <Shell title={projectName} active={2} goHome={goHome}><div className="feedback"><article className="document"><small>분석 문서 · {result?.page_count ?? "-"}페이지</small><h2>{item?.name ?? "평가 항목"}</h2><section className="evidence"><b>실제 원문 근거</b><p>{evidence}</p><div className="legend">근거 페이지: {pages.map(value => <button key={value} type="button" className={value === page ? "evidence-page selected" : "evidence-page"} onClick={() => setPage(value)}>{value}쪽 보기</button>)}</div></section>{pdfUrl && <iframe className="evidence-pdf" title={`근거 ${page}쪽`} src={pdfUrl}/>} {type === "quant" && <section className="required-docs"><div><b>B권 필수 서류 확인</b><button className="secondary" disabled={isCheckingBDocuments} onClick={onCheckBDocuments}>{isCheckingBDocuments ? "B권 확인 중…" : "필수 서류 확인"}</button></div>{isCheckingBDocuments && <p>{bProgress?.page_count ? `${bProgress.processed_pages ?? 0} / ${bProgress.page_count}쪽 처리 · OCR ${bProgress.ocr_page_count ?? 0}쪽` : "B권 준비 중…"}</p>}{bDocumentCheck?.results.map(document => <article key={document.name}><b>{document.name}</b><em className={document.status.toLowerCase()}>{document.status === "FOUND" ? "확인됨" : document.status === "REVIEW_REQUIRED" ? "검토 필요" : "미확인"}</em><small>{document.page ? `B권 ${document.page}쪽 · ${document.evidence}` : "B권에서 확인하지 못했습니다."}</small></article>)}</section>}</article><aside className="feedback-side"><span className="tag">{type === "quant" ? "정량적 평가" : "정성적 평가"}</span><h3>{item?.score ?? 0} <small>/ {item?.max_score ?? 0}점</small></h3><div className={item?.status === "MET" ? "comment good-box" : "comment bad-box"}><b>{item?.status === "MET" ? "충족" : "보완 필요"}</b><p>{item?.comment ?? "평가 결과가 없습니다."}</p></div><button className="secondary" onClick={() => navigate(type === "quant" ? "qual" : "quant")}>{type === "quant" ? "정성적 평가 보기 →" : "정량적 평가 보기 →"}</button><button className="secondary" disabled={isGeneratingComments || commentStatus === "COMPLETED"} onClick={onGenerateComments}>{isGeneratingComments ? "AI 코멘트 생성 중…" : commentStatus === "COMPLETED" ? "AI 코멘트 생성 완료" : "AI 코멘트 생성"}</button><button className="primary" onClick={() => navigate("results")}>결과 보기</button></aside></div></Shell>;
}

function LegacyBFeedback({ type, projectName, result, evaluationId, goHome, navigate, onGenerateComments, commentStatus, isGeneratingComments }: { type: "quant" | "qual"; projectName: string; result?: EvaluationResult; evaluationId?: string; goHome: () => void; navigate: (view: View) => void; onGenerateComments: () => void; commentStatus?: Evaluation["comment_status"]; isGeneratingComments: boolean }) {
  const item = result?.item_results[type === "quant" ? 1 : 0];
  const pages = item?.citation_pages.length ? item.citation_pages : item?.evidence_pages ?? [];
  const [page, setPage] = useState(pages[0]);
  const evidence = formatEvidenceText(item?.evidence || (item?.status === "MISSING" ? `필수 키워드 ${item.missing_keywords.join(", ")}를 본문 근거에서 찾지 못했습니다.` : "확인 가능한 원문 발췌가 없습니다."));
  const pdfUrl = evaluationId && page ? `http://127.0.0.1:8788/evaluations/${evaluationId}/document#page=${page}` : undefined;
  return <Shell title={projectName} active={2} goHome={goHome}><div className="feedback"><article className="document"><small>분석 문서 · {result?.page_count ?? "-"}페이지</small><h2>{item?.name ?? "평가 항목"}</h2><section className="evidence"><b>실제 원문 근거</b><p>{evidence}</p><div className="legend">근거 페이지: {pages.length ? pages.map(value => <button key={value} type="button" className={value === page ? "evidence-page selected" : "evidence-page"} onClick={() => setPage(value)}>{value}쪽 보기</button>) : "없음"}</div></section>{pdfUrl && <iframe className="evidence-pdf" title={`근거 ${page}쪽`} src={pdfUrl}/>}</article><aside className="feedback-side"><span className="tag">{type === "quant" ? "정량적 평가" : "정성적 평가"}</span><h3>{item?.score ?? 0} <small>/ {item?.max_score ?? 0}점</small></h3><div className={item?.status === "MET" ? "comment good-box" : "comment bad-box"}><b>{item?.status === "MET" ? "충족" : "보완 필요"}</b><p>{item?.comment ?? "평가 결과가 없습니다."}</p></div><button className="secondary" onClick={() => navigate(type === "quant" ? "qual" : "quant")}>{type === "quant" ? "정성적 평가 보기 →" : "정량적 평가 보기 →"}</button><button className="secondary" disabled={isGeneratingComments || commentStatus === "COMPLETED"} onClick={onGenerateComments}>{isGeneratingComments ? "AI 코멘트 생성 중…" : commentStatus === "COMPLETED" ? "AI 코멘트 생성 완료" : "AI 코멘트 생성"}</button><button className="primary" onClick={() => navigate("results")}>결과 보기</button></aside></div></Shell>;
}

function LegacyRealFeedback({ type, projectName, result, evaluationId, goHome, navigate, onGenerateComments, commentStatus, isGeneratingComments }: { type: "quant" | "qual"; projectName: string; result?: EvaluationResult; evaluationId?: string; goHome: () => void; navigate: (view: View) => void; onGenerateComments: () => void; commentStatus?: Evaluation["comment_status"]; isGeneratingComments: boolean }) {
  const item = result?.item_results[type === "quant" ? 1 : 0];
  const pages = item?.citation_pages.length ? item.citation_pages : item?.evidence_pages ?? [];
  const source = item?.evidence || (item?.status === "MISSING" ? `필수 키워드 ${item.missing_keywords.join(", ")}를 원문에서 찾지 못했습니다.` : "확인 가능한 원문 발췌가 없습니다.");
  return <Shell title={projectName} active={2} goHome={goHome}><div className="feedback"><article className="document"><small>분석 문서 · {result?.page_count ?? "-"}페이지</small><h2>{item?.name ?? "평가 항목"}</h2><section className="evidence"><b>실제 원문 근거</b><p>{source}</p><div className="legend">근거 페이지: {pages.length ? pages.map(page => evaluationId ? <a key={page} className="evidence-page" href={`http://127.0.0.1:8788/evaluations/${evaluationId}/document#page=${page}`} target="_blank" rel="noreferrer">{page}쪽 보기</a> : <span key={page}>{page}쪽</span>) : "없음"}</div></section></article><aside className="feedback-side"><span className="tag">{type === "quant" ? "정량적 평가" : "정성적 평가"}</span><h3>{item?.score ?? 0} <small>/ {item?.max_score ?? 0}점</small></h3><div className={item?.status === "MET" ? "comment good-box" : "comment bad-box"}><b>{item?.status === "MET" ? "충족" : "보완 필요"}</b><p>{item?.comment ?? "평가 결과가 없습니다."}</p></div><button className="secondary" onClick={() => navigate(type === "quant" ? "qual" : "quant")}>{type === "quant" ? "정성적 평가 보기 →" : "정량적 평가 보기 →"}</button><button className="secondary" disabled={isGeneratingComments || commentStatus === "COMPLETED"} onClick={onGenerateComments}>{isGeneratingComments ? "AI 코멘트 생성 중…" : commentStatus === "COMPLETED" ? "AI 코멘트 생성 완료" : "AI 코멘트 생성"}</button><button className="primary" onClick={() => navigate("results")}>결과 보기</button></aside></div></Shell>;
}

function LegacyFeedback({ type, projectName, result, evaluationId, goHome, navigate, onGenerateComments, commentStatus, isGeneratingComments }: { type: "quant" | "qual"; projectName: string; result?: EvaluationResult; evaluationId?: string; goHome: () => void; navigate: (view: View) => void; onGenerateComments: () => void; commentStatus?: Evaluation["comment_status"]; isGeneratingComments: boolean }) {
  const isQuant = type === "quant";
  const item = result?.item_results[isQuant ? 1 : 0];
  const evidencePages = item?.citation_pages.length ? item.citation_pages : item?.evidence_pages ?? [];
  const documentUrl = (page: number) => evaluationId ? `http://127.0.0.1:8788/evaluations/${evaluationId}/document#page=${page}` : undefined;
  return <Shell title={projectName} active={2} goHome={goHome}><div className="feedback"><article className="document"><small>분석 문서 · {result?.page_count ?? "-"}페이지</small><h2>{item?.name ?? (isQuant ? "Ⅲ. 사업 수행 계획" : "Ⅳ. 수행 전략 및 창의성")}</h2>{isQuant ? <p>본 사업은 총 12주에 걸쳐 노트북 500대를 조립·납품하며, <mark className="good">주당 42대 조립 능력을 보유한 자체 라인 2개를 운영</mark>한다.<br/><mark className="bad">품질 검사 인력은 3명으로 RFP 기준(5명)에 미달</mark>하여 해당 항목에서 감점이 발생하였다. 납기 준수율은 최근 3개년 평균 <mark className="good">98.2%로 우수 등급</mark>에 해당한다.</p> : <p>기존 조립 공정에 <mark className="good">AI 기반 불량 예측 검사를 도입</mark>하여 초기 불량률을 30% 절감하는 방안을 제시하였다. 다만 <mark className="bad">제시된 일정과 인력 계획 간 정합성 근거가 부족</mark>하여 실행 가능성 측면에서 보완이 필요하다.</p>}<div className="legend">API 근거 페이지: {item?.citation_pages.join(", ") || item?.evidence_pages.join(", ") || "없음"}</div></article><aside className="feedback-side">{isQuant ? <><span className="tag">정량적 평가</span><h3>{item?.score ?? 0} <small>/ {item?.max_score ?? 30}점</small></h3><Score label="법/법령/훈령 준수" value="100%" width="100%"/><Score label="체계 규격 필수항목 충족" value="+24" width="100%"/><Score label="가점 / 감점 요소" value="+0.1" width="45%"/><button className="secondary" onClick={() => navigate("qual")}>정성적 평가 보기 →</button></> : <><span className="tag">정성적 평가</span><small>{result?.comment_source === "local_openvino_gemma" ? "AI 코멘트" : "규칙 기반 코멘트"}</small><div className={item?.status === "MET" ? "comment good-box" : "comment bad-box"}><b>{item?.status === "MET" ? "충족" : "보완 필요"}</b><p>{item?.comment ?? "평가 결과가 없습니다."}</p></div><button className="secondary" onClick={() => navigate("quant")}>정량적 평가 보기 →</button></>}<button className="secondary" disabled={isGeneratingComments || commentStatus === "COMPLETED"} onClick={onGenerateComments}>{isGeneratingComments ? "AI 코멘트 생성 중…" : commentStatus === "COMPLETED" ? "AI 코멘트 생성 완료" : "AI 코멘트 생성"}</button><button className="primary" onClick={() => navigate("results")}>결과 보기</button></aside></div></Shell>;
}

function Score({ label, value, width }: { label: string; value: string; width: string }) { return <div className="score"><div><b>{label}</b><em>{value}</em></div><i><span style={{ width }}/></i></div>; }
function Results({ projectName, result, goHome, navigate }: { projectName: string; result?: EvaluationResult; goHome: () => void; navigate: (view: View) => void }) { const items = result?.item_results ?? []; return <Shell title={projectName} active={3} goHome={goHome}><div className="content"><div className="results-head"><div><h2>채점 결과</h2><p>{result?.notice ?? "실행 결과가 없습니다."}</p></div></div><div className="results-grid">{items.map((item, index) => <button className="result-card" key={item.id} onClick={() => navigate(index === 0 ? "qual" : "quant")}><div><span className="rank">{index + 1}</span><em>{item.status === "MET" ? "충족" : "보완 필요"}</em></div><h2>{item.name}</h2><small>근거 페이지: {item.citation_pages.join(", ") || item.evidence_pages.join(", ") || "없음"}</small><strong>{item.score}<small> / {item.max_score}점</small></strong></button>)}</div></div></Shell>; }

createRoot(document.getElementById("root")!).render(<App />);
