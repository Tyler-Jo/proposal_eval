## 3. 단위 테스트(Unit Test) 상세 요구사항 (pytest 기반)

정량 평가의 오탐(False Positive)과 미탐(False Negative) 문제를 해결하고, 평가위원의 수동 검증(Human-in-the-loop)을 지원하기 위해 개선된 단위 테스트 스크립트 모음입니다.

### Test 01. 대용량 PDF 청크 분할 및 메모리 점유율 테스트 (`test_01_pdf_chunking.py`)
* **목적:** 수백 페이지 스캔 PDF 처리 시 RAM 폭발 방지 및 가비지 컬렉션(`gc.collect()`) 검증
* **요구사항:**
  * 대용량 PDF를 `chunk_page_size` (예: 30장, 50장) 단위로 끊어서 읽는 스트리밍 파싱을 수행한다.
  * `psutil` 모듈로 각 청크 처리 전후의 RAM 사용량을 측정하고, `gc.collect()` 호출 후 메모리가 안정적으로 비워지는지(`assert`) 검증한다.

### Test 02. [개선] 정량 평가 - 감점 오탐 검증 & 시각적 근거(Bounding Box) 반환 테스트 (`test_02_quant_blind.py`)
* **목적:** 업체명/로고 노출 감점 시 오탐(False Positive) 확인을 위한 Bounding Box 좌표 생성 및 화이트리스트(예외 처리) 로직 검증
* **요구사항:**
  * **화이트리스트 필터링:** '발주기관명', '참조 기술 규격 내 타사명' 등 감점 예외 단어 사전(Whitelisting)을 적용하여 오탐으로 인한 무분별한 감점이 발생하지 않는지 검증한다.
  * **근거 좌표 반환:** 위반 검출 시 UI에서 Highlight 및 오탐 판별을 할 수 있도록 Bounding Box 좌표(`bbox: [x0, y0, x1, y1]`)와 페이지 번호, 해당 텍스트 이미지 캡처 데이터를 포함하는 결과 객체를 생성하는지 검증한다.
  * **상태값 반환:** 생성된 감점 객체 기본 상태가 `"status": "PENDING_REVIEW"`(검토 대기)로 설정되어, 향후 UI에서 사용자가 "승인(CONFIRMED)" 또는 "오탐 취소(DISMISSED)"로 변경 가능하도록 구조화되어 있는지 `assert`한다.

### Test 03. [개선] 정량 평가 - 필수 서류 미탐(False Negative) 및 수동 감점 인터페이스 테스트 (`test_03_quant_documents.py`)
* **목적:** B 제안서 필수 서류 누락 검사 및 자동 검출 실패 시 사용자의 수동 감점 추가(Manual Override) 데이터 구조 검증
* **요구사항:**
  * RFP 필수 서류 룰셋과 B 제안서 OCR 결과를 비교하여 누락 서류 리스트를 추출한다.
  * AI가 미탐(False Negative)하여 사용자가 UI에서 직접 감점 항목을 수동 추가할 경우, 시스템 파이프라인이 수동 등록된 감점 항목(`"is_manual": True`)을 기존 자동 검출 항목과 병합하여 최종 감점 총점을 재계산하는지 검증한다.

### Test 04. 정성 평가 - 목차/섹션 구조화 인덱싱 테스트 (`test_04_section_structurer.py`)
* **목적:** 수백 페이지 통문서를 LLM에 넘기지 않기 위해 목차/헤더 기준으로 섹션별 데이터베이스(JSON)화
* **요구사항:**
  * 텍스트를 파싱하여 `{ "section_id": "2.1", "title": "보안 대책", "content": "...", "page_range": [45, 52] }` 형태의 트리로 분할되는지 검증한다.

### Test 05. 토큰 제약 관리 & LLM 정형 JSON 출력 테스트 (`test_05_token_budget_and_llm_json.py`)
* **목적:** 입력 텍스트가 Context Window를 초과하지 않도록 자동 잘라내기(Truncation/Windowing) 후 LLM이 정형 JSON을 출력하는지 검증
* **요구사항:**
  * `TokenBudgetManager`를 통해 입력 텍스트가 지정된 최대 토큰 수(예: 3,000 토큰)를 초과할 경우, 슬라이딩 윈도우로 안전하게 자르는지 검증한다.
  * 자른 컨텍스트를 LLM에 전달했을 때, 응답 결과가 `json.loads()` 가능한 형태이며 `"score"`, `"reason"`, `"evidence"` 키를 필수로 포함하는지 `assert`한다.

### Test 06. 정성 평가 - Chat-RAG 및 근거 페이지(Citation) 링크 테스트 (`test_06_rag_citation.py`)
* **목적:** 대화형 질문 시 답변과 함께 원본 제안서의 정확한 페이지 번호(Citation) 반환 검증
* **요구사항:**
  * 질문과 연관된 섹션만 타겟팅하여 추출하고, LLM 답변에 인용된 정보의 실제 페이지 번호(`citation_pages: [15, 16]`)가 정확히 매칭되는지 검증한다.

### Test 07. [개선] 법령 및 규정 위배 검증 & 예외 처리 테스트 (`test_07_compliance_audit.py`)
* **목적:** 제안서 내용 중 관련 법령(개인정보보호법, SW진흥법 등) 위반 검출 및 오탐 방지용 문맥 기반 검증
* **요구사항:**
  * 법령 위배 DB/룰셋(JSON)을 기반으로 스캐닝을 수행한다.
  * 단어 단위 검출로 인한 오탐을 줄이기 위해 위반 의심 문단 전체를 컨텍스트로 묶어 severity(`HIGH`, `MEDIUM`, `LOW`) 및 오탐 여부 사유(`audit_note`)를 생성하는지 검증한다.