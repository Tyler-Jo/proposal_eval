export type Evaluation = { id: string; status: "RUNNING" | "COMPLETED" | "FAILED"; error?: string; result?: EvaluationResult };
export type EvaluationItem = { id: string; name: string; score: number; max_score: number; status: string; evidence_pages: number[]; citation_pages: number[]; comment: string; evidence: string; comment_source: string };
export type EvaluationResult = { page_count: number; result: { score: number; max_score: number }; item_results: EvaluationItem[]; notice: string; comment_source: string };
export type RfpCatalogItem = { name: string; source?: string; importance?: string; effect?: string; evidence: { page: number; text: string; bbox?: number[] | null }; review_required: boolean };
export type RfpAnalysis = { notice: string; requirement_count: number; review_required_count: number; page_sources: Record<string, string>; review_catalog: { required_documents: RfpCatalogItem[]; quantitative_evaluation_items: RfpCatalogItem[]; qualitative_evaluation_items: RfpCatalogItem[] } };
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
  registerDocuments: (projectId: string, kind: "RFP" | "A" | "B", paths: string[]) => request(`/projects/${projectId}/documents`, { method: "POST", body: JSON.stringify({ kind, paths }) }),
  analyzeRfp: (projectId: string) => request<RfpAnalysis>(`/projects/${projectId}/rfp-analysis`, { method: "POST", body: JSON.stringify({}) }),
  evaluate: (projectId: string, rubric: unknown) => request<Evaluation>(`/projects/${projectId}/evaluations`, { method: "POST", body: JSON.stringify({ rubric }) }),
  evaluation: (id: string) => request<Evaluation>(`/evaluations/${id}`),
};
