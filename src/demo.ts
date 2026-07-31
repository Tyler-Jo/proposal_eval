import type { BDocumentCheck, EvaluationResult, RfpAnalysis } from "./api";

export const DEMO_MODE = import.meta.env.MODE === "demo";

export const demoFiles = [
  { name: "2026_정보화사업_제안요청서.pdf", pages: 42, kind: "RFP" as const, path: "demo://rfp.pdf" },
  { name: "기본제안서_A권.pdf", pages: 86, kind: "A권" as const, path: "demo://proposal-a.pdf" },
  { name: "제안회사_소개_및_증빙_B권.pdf", pages: 34, kind: "B권" as const, path: "demo://proposal-b.pdf" },
];

export const demoRfpAnalysis: RfpAnalysis = {
  notice: "데모 데이터로 추출한 검토 기준입니다.",
  requirement_count: 6,
  review_required_count: 1,
  page_sources: { "3": "text", "12": "text" },
  review_catalog: {
    required_documents: [
      { name: "사업자등록증", source: "rfp", evidence: { page: 3, text: "필수 제출 서류" }, review_required: false },
      { name: "납세증명서", source: "rfp", evidence: { page: 3, text: "필수 제출 서류" }, review_required: false },
      { name: "정보보호 관리체계 인증서", source: "rfp", evidence: { page: 4, text: "필수 제출 서류" }, review_required: true },
    ],
    quantitative_evaluation_items: [
      { name: "유사 사업 수행 실적", source: "rfp", importance: "general", evidence: { page: 12, text: "최근 3년 실적" }, review_required: false },
      { name: "전담 인력 보유 현황", source: "rfp", importance: "required", evidence: { page: 13, text: "핵심 인력 배치" }, review_required: false },
      { name: "지체상금 및 감점 기준", source: "evaluation_rule", importance: "general", effect: "deduction_candidate", evidence: { page: 15, text: "평가 규칙" }, review_required: true },
    ],
    qualitative_evaluation_items: [
      { name: "사업 이해도 및 추진 전략", source: "rfp", evidence: { page: 18, text: "정성 평가" }, review_required: false },
      { name: "수행 일정의 실현 가능성", source: "rfp", evidence: { page: 19, text: "정성 평가" }, review_required: false },
    ],
  },
};

export const demoEvaluation: EvaluationResult = {
  page_count: 120,
  result: { score: 82, max_score: 100 },
  notice: "데모 평가 결과입니다. 실제 점수나 문서 분석 결과가 아닙니다.",
  comment_source: "demo",
  item_results: [
    { id: "demo-qual", name: "사업 이해도 및 추진 전략", source: "rfp", score: 44, max_score: 50, status: "MET", evidence_pages: [12, 18], citation_pages: [12, 18], missing_keywords: [], evidence: "제안서는 사업 목표와 단계별 추진 전략을 명확히 제시했습니다. 다만 착수 후 위험 관리 절차의 담당자와 보고 주기를 더 구체화하면 실행 가능성을 높일 수 있습니다.", comment: "핵심 전략은 충족하나 위험 관리 근거를 보완하세요.", comment_source: "demo" },
    { id: "demo-quant", name: "정량 서류 및 수행 역량", source: "rfp", score: 38, max_score: 50, status: "MET", evidence_pages: [7, 22], citation_pages: [7, 22], missing_keywords: [], evidence: "최근 3년의 유사 사업 실적과 전담 인력 구성은 확인되었습니다. 일부 인증 서류의 유효기간은 평가위원의 최종 확인이 필요합니다.", comment: "필수 서류는 대체로 확인되었으며 인증서 유효기간을 검토하세요.", comment_source: "demo" },
  ],
  adjustment_results: [{ name: "지체상금 감점", rule_type: "delay", effect: "deduction_candidate", status: "NOT_APPLIED", applied_delta: 0, rfp_page: 15, rfp_excerpt: "계약 이행 지연 시 평가 기준에 따라 감점할 수 있다.", related_items: [{ name: "수행 일정의 실현 가능성", status: "MET", evidence_pages: [22], proposal_basis: "제안서의 주차별 일정에 주요 산출물과 검수 단계가 제시되어 있습니다.", proposal_excerpt: "착수 후 12주 내 단계별 산출물을 제출합니다." }] }],
};

export const demoBDocumentCheck: BDocumentCheck = {
  document_count: 4,
  results: [
    { name: "사업자등록증", category: "REQUIRED", status: "FOUND", page: 3, evidence: "B권 3쪽에서 확인", matched_tokens: ["사업자등록"] },
    { name: "납세증명서", category: "REQUIRED", status: "FOUND", page: 5, evidence: "B권 5쪽에서 확인", matched_tokens: ["납세증명"] },
    { name: "정보보호 관리체계 인증서", category: "REQUIRED", status: "REVIEW_REQUIRED", page: 14, evidence: "유효기간 확인 필요", matched_tokens: ["인증"] },
    { name: "유사 사업 수행 실적", category: "GENERAL", status: "FOUND", page: 18, evidence: "B권 18쪽에서 확인", matched_tokens: ["수행 실적"] },
  ],
};

export const demoPreview = `data:image/svg+xml,${encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" width="900" height="1200"><rect width="100%" height="100%" fill="#fff"/><rect x="70" y="65" width="760" height="1070" rx="5" fill="#faf9ff" stroke="#c9c2df"/><text x="120" y="150" font-family="Arial, sans-serif" font-size="28" font-weight="700" fill="#332c4d">DEMO PDF PREVIEW</text><text x="120" y="210" font-family="Arial, sans-serif" font-size="18" fill="#625b72">실제 PDF 원문은 데모 모드에서 제공되지 않습니다.</text><line x1="120" y1="260" x2="780" y2="260" stroke="#d9d4ed"/><rect x="120" y="310" width="520" height="18" fill="#e3deff"/><rect x="120" y="350" width="610" height="12" fill="#e9e7f0"/><rect x="120" y="385" width="570" height="12" fill="#e9e7f0"/><rect x="120" y="420" width="640" height="12" fill="#e9e7f0"/></svg>')}`;
