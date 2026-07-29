# 🚀 On-Device 사업제안서 정량·정성 평가 시스템 프로젝트 명세서 & 단위 테스트 요구사항

> **[코딩 에이전트 지침]**
> 본 프로젝트는 최종적으로 **Intel NPU 랩탑(Core Ultra) 환경에서 Tauri(UI) + Python(Sidecar) 형태로 동작하는 .exe 앱**이 됩니다.
> 다만 현 단계에서는 NPU 가속 라이브러리(OpenVINO) 및 프론트엔드를 배제하고, **Python 기반의 핵심 AI/파싱 연산 로직과 이에 대한 단위 테스트(Unit Test) 코드만 작성**합니다.
> 백엔드 LLM 및 OCR 추론 엔진은 인터페이스(추상 클래스)로 감싸서 구현하되, 현재 테스트 단계에서는 **Ollama(Qwen2.5:3b) 또는 일반 Python 라이브러리**를 기본 가동 엔진으로 사용합니다.

---

## 1. 프로젝트 핵심 정보 & 프로세스

* **대상 문서:** 발주처 **제안요청서(RFP)** 1건, 입찰 업체별 **제안서 2종 (A: 본 제안서, B: 증빙자료)**
* **문서 특징:** 스캔 이미지 포함, 파일당 수백 페이지 분량
* **평가 구조:**
  1. **정량 평가(B권) (탭 1):** 필수 제출 서류(B) 존재 여부 체크, 감점 요소(A/B) 검출 (업체명 노출, 쪽번호 누락 등)
  2. **정성 평가(A권) (탭 2):** 제안 내용(A) 대상 Chat-RAG, RFP 대비 제안서 비교 분석, 답변 근거 위치(Page/Section) 제공

---

## 2. 개발 대상 모듈 및 인터페이스 구조 (Python)

본 시스템은 인터넷 연결이 차단된 오프라인 환경에서 동작하는 **Tauri UI 기반의 온디바이스(On-Device) 데스크톱 애플리케이션**입니다. 

Tauri(Frontend)가 백그라운드의 Python Sidecar 프로세스(Backend)를 제어하며, IPC 통신을 통해 데이터 연산을 주고받는 아키텍처를 가집니다. 이번 파이썬 백엔드 개발 및 단위 테스트 타겟 구조는 다음과 같습니다.
[ Backend: Python Sidecar Process - On-Device AI/Logic Engine ]
src/
├── parsers/
│   ├── pdf_chunk_parser.py     # 수백 페이지 PDF 청크 분할 및 메모리 관리
│   ├── section_structurer.py   # A 제안서 목차/헤더 기준 섹션 분할
│   ├── b_index_parser.py       # B권 전반부 목차/간지 파싱 모듈
│   └── token_budget_manager.py # LLM Context Window 초과 방지 슬라이딩 윈도우
├── engines/
│   ├── llm_interface.py        # LLM 추상 클래스 및 DevEngine (Ollama/PyTorch)
│   └── ocr_interface.py        # OCR 추상 클래스 및 가변 ROI(Region of Interest) 파서
├── evaluators/
│   ├── quantitative.py         # 정량 평가 (블라인드, B권 증빙 서류 체크)
│   ├── compliance.py           # 법령/규정 위배 여부 검증
│   └── qualitative.py          # 정성 평가 (RFP 매칭, Chat-RAG, JSON 루브릭)
└── tests/                       # [단위 테스트 작성 대상]
├── test_01_pdf_chunking.py
├── test_02_quant_blind.py
├── test_03_quant_b_documents.py  # B권 목차
├── test_04_section_structurer.py
├── test_05_token_budget_and_llm_json.py
├── test_06_rag_citation.py
└── test_07_compliance_audit.py

# 주의 : 개발은 일반 GPU가 달린 데스크탑에서 하되, 실제 운용은 NPU 칩셋이 장착된 랩탑에서 하므로 개발 단계부터 이러한 성능적 제한사항을 고려하여 개발해야한다.