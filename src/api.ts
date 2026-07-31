export type Evaluation = { id: string; status: "RUNNING" | "COMPLETED" | "FAILED"; error?: string; result?: EvaluationResult; stage?: string; progress_message?: string; processing?: { processed_pages?: number; page_count?: number; ocr_page_count?: number; cache_hit?: boolean; text_extraction_seconds?: number }; comment_status?: "NOT_STARTED" | "RUNNING" | "COMPLETED" | "FAILED"; comment_progress?: number };
export type EvaluationItem = { id: string; name: string; source?: string; importance?: string; score: number; max_score: number; status: string; evidence_pages: number[]; citation_pages: number[]; missing_keywords: string[]; comparison?: { rfp_requirement: string; proposed: string; summary: string }; comment: string; evidence: string; comment_source: string };
export type AdjustmentResult = { name: string; rule_type: string; effect: string; value?: number | null; cap?: number | null; status: "APPLIED" | "NOT_APPLIED" | "TRIGGERED" | "REVIEW_REQUIRED"; applied_delta?: number | null; rfp_page: number; rfp_excerpt: string; related_items: { name: string; status: string; score_delta?: number | null; evidence_pages: number[]; proposal_basis?: string; proposal_excerpt?: string }[] };
export type EvaluationResult = { page_count: number; result: { score: number; max_score: number }; item_results: EvaluationItem[]; adjustment_results?: AdjustmentResult[]; notice: string; comment_source: string };
export type RfpCatalogItem = { name: string; source?: string; importance?: string; effect?: string; rule_type?: string; value?: number | null; cap?: number | null; condition_summary?: string; condition?: Record<string, unknown>; rfp_requirement?: string; bonus_eligible?: boolean; evidence: { page: number; text: string; bbox?: number[] | null }; review_required: boolean };
export type RfpAnalysis = { notice: string; requirement_count: number; review_required_count: number; page_sources: Record<string, string>; review_catalog: { required_documents: RfpCatalogItem[]; quantitative_evaluation_items: RfpCatalogItem[]; qualitative_evaluation_items: RfpCatalogItem[] } };
export type RfpAnalysisJob = { id: string; status: "RUNNING" | "COMPLETED" | "FAILED"; processing: { processed_pages?: number; page_count?: number; ocr_page_count?: number; cache_hit?: boolean }; result?: RfpAnalysis; error?: string };
export type StoredProject = { id: string; name: string; documents: Record<"RFP" | "A" | "B", string[]>; has_rfp_analysis: boolean; created_at: string };
export type BDocumentCheck = { document_count: number; results: { name: string; category?: "REQUIRED" | "GENERAL"; status: "FOUND" | "REVIEW_REQUIRED" | "MISSING"; page: number | null; evidence: string; matched_tokens: string[] }[] };
export type BDocumentCheckJob = { id: string; status: "RUNNING" | "COMPLETED" | "FAILED"; processing: { processed_pages?: number; page_count?: number; ocr_page_count?: number }; results?: BDocumentCheck["results"]; error?: string };
const BASE = "http://127.0.0.1:8788";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE}${path}`, { ...init, headers: { "Content-Type": "application/json", ...init?.headers } });
  const body = await response.json() as T & { error?: string };
  if (!response.ok) throw new Error(body.error ?? "백엔드 요청에 실패했습니다.");
  return body;
}
export const api = {
  health: () => request<{ status: string }>("/health"),
  createProject: (name: string) => request<{ id: string }>("/projects", { method: "POST", body: JSON.stringify({ name }) }),
  projects: () => request<{ projects: StoredProject[] }>("/projects"),
  project: (id: string) => request<StoredProject & { rfp_analysis?: RfpAnalysis }>(`/projects/${id}`),
  registerDocuments: (projectId: string, kind: "RFP" | "A" | "B", paths: string[]) => request(`/projects/${projectId}/documents`, { method: "POST", body: JSON.stringify({ kind, paths }) }),
  analyzeRfp: (projectId: string) => request<RfpAnalysisJob>(`/projects/${projectId}/rfp-analysis`, { method: "POST", body: JSON.stringify({}) }),
  rfpAnalysis: (id: string) => request<RfpAnalysisJob>(`/rfp-analysis/${id}`),
  checkBDocuments: (projectId: string) => request<BDocumentCheckJob>(`/projects/${projectId}/b-document-check`, { method: "POST", body: JSON.stringify({}) }),
  bDocumentCheck: (id: string) => request<BDocumentCheckJob>(`/b-document-check/${id}`),
  evaluate: (projectId: string, rubric: unknown, ocrRenderScale: number) => request<Evaluation>(`/projects/${projectId}/evaluations`, { method: "POST", body: JSON.stringify({ rubric, ocr_render_scale: ocrRenderScale }) }),
  evaluation: (id: string) => request<Evaluation>(`/evaluations/${id}`),
  generateComments: (id: string) => request<Evaluation>(`/evaluations/${id}/comments`, { method: "POST", body: JSON.stringify({}) }),
};
