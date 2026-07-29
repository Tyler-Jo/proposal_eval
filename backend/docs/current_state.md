# 현재 상태

## 현재 단계

프로젝트는 `src/` 구현 전의 `tests/` 기반 기능별 POC 단계다. 기본 단위 테스트와 실제 PDF/OCR/LLM POC는 marker로 분리한다.

## 완료

* [x] 프로젝트 명세 정리
* [x] Test 01~07 요구사항 정리
* [x] 작은 fixture에서 300페이지 이상 PDF POC까지의 단계적 검증 계획 수립
* [x] Codex 작업 컨텍스트 문서(`AGENTS.md`) 구성
* [x] Test 01~05 실제 PDF/OCR/LLM POC 초안 및 통합 대시보드 구성
* [x] 대시보드 진입점을 `test_dashboard/app_integrated.py`로 통합하고 임시 대시보드 파일 및 프로세스 정리
* [x] Test 05를 선택 PDF의 페이지별 텍스트·토큰 윈도우·근거 페이지를 보존하는 실제 문서 평가 POC로 확장
* [x] Test 05 점수를 AI 평균이 아닌 배점표 필수 키워드 규칙으로 산정하고, AI는 항목별 코멘트·인용만 생성하도록 전환
* [x] Tauri UI가 호출할 로컬 Python sidecar API를 추가하고, 사업·PDF 등록·배점표 평가·작업 조회를 연결
* [x] 텍스트 레이어 RFP PDF의 체계규격 표 후보를 추출하고, 필수(`**`)·일반(`∙`)·상위규격 가점 표식을 구조화하는 `rfp` 모듈을 추가
* [x] 수치 비교, OR 조건, 괄호 구성 조건을 구조화하고 복합·모호한 항목을 `review_required`로 보수적으로 분류
* [x] `POST /projects/{id}/rfp-analysis`, `GET /projects/{id}/rfp-analysis` API를 추가
* [x] RFP 본문에서 합격 기준, 필수항목 불합격, 일반항목 감점, 상위규격 가점, 작성기준 감점 상한을 구조화해 RFP 분석 API에 포함
* [x] 일반 평가표를 체계규격으로 오인하지 않도록 독립된 규격 열 제목과 체계규격 표 문맥에서만 표식 fallback을 적용
* [x] Sidecar에 로컬 PaddleOCR fallback을 연결했다. 텍스트가 부족한 PDF 페이지만 1.5배 렌더링 후 OCR하고, 페이지별 텍스트 출처를 RFP 분석 결과에 보존한다.
* [x] Tauri의 RFP 등록 흐름에서 RFP 분석 API를 호출해 필수 서류·정량·정성 평가 후보를 정적 예시가 아닌 실제 추출 결과로 표시한다.
* [x] 필수 서류는 문서명과 제출·첨부 의무가 함께 명시된 문장만 후보로 추출하도록 제한했다. 미제출 감점, 인증 설명, 표 헤더, 과거 서술은 제외한다.
* [x] 수정본 RFP 검증을 통해 B권 `Ⅱ. 증빙자료`의 `항목 | 작성방법` 표를 필수 서류의 최우선 원본으로 사용하도록 보완했다. 같은 형식의 A권 작성 기준 표는 제외한다.

## 다음 우선순위

1. RFP의 세부 평가항목·배점·제출서류·상대평가·작성기준 구간 추출 정확도를 실제 RFP로 검증하고 보완한다.
2. A·B권에서 체계규격 대응 근거를 탐색하고 명확한 수치·수량 조건의 판정 후보를 생성한다.
3. 로컬 SQLite에 RFP 분석, AI 후보, 평가위원 검토값 및 변경 이력을 저장한다.
4. Test 06(RAG 인용 페이지)와 Test 07(컴플라이언스) POC를 최신 심의 지원 모델에 맞춰 구현한다.

## 확정된 작업 범위

* 현재 목표는 Python 핵심 로직, 단위 테스트, 통합 테스트, 실제 PDF POC다.
* Tauri UI는 개발용 Python sidecar를 자동 실행해 Test 05의 페이지별 PDF 텍스트·배점표 평가 API를 호출한다.
* 프론트엔드, OpenVINO 최적화, `.exe` 패키징은 현 단계 범위 밖이다.
* 실제 300페이지 이상 스캔 PDF 검증은 기본 단위 테스트와 분리된 POC로 수행한다.

## 미확정/블로커

* 목표 장비는 Windows, RAM 32GB, Intel Core Ultra 5, Intel Arc Graphics이나 Sidecar의 허용 RSS 및 처리 시간은 미확정
* 배포 후보 OCR 엔진과 LLM 엔진
* 비식별화된 실제 또는 대표성 있는 300페이지 이상 스캔 PDF
* OCR/탐지/인용 정확도의 최종 수치 임계값
* 로컬 Docker vLLM/Gemma endpoint가 connection reset을 반환했으며, 현재 작업 환경에서는 Docker socket 권한이 없음
* OCR은 현재 PaddleOCR CPU 실행이며, 모델 최초 로드 시간과 대형 스캔 PDF의 처리 시간·메모리는 목표 장비에서 별도 POC로 측정해야 한다.
* sidecar는 `/home/user/develop/model/ko-gemma-2-9b-it-int4-ov` OpenVINO Gemma를 지연 로드해 코멘트·인용을 생성하며, 실패 시에만 규칙 기반 fallback을 사용한다.
* RFP 표 추출은 PyMuPDF 표 탐지와 `**`/`∙` 표식 기반 텍스트 fallback을 함께 사용한다. 표 병합, 자동번호, 복합 수치 조건은 검토 필요로 표시하며 자동 판정하지 않는다.
* Test 05는 기본적으로 모든 페이지를 로컬 PaddleOCR로 읽는다. 대시보드에서 텍스트 부족 페이지만 OCR하거나 텍스트 레이어만 사용하는 모드도 선택할 수 있다.
* 대시보드는 실패 작업도 `tests/results/dashboard_job_*.json`에 남긴다. 코드 변경 뒤에는 대시보드 프로세스를 재시작해야 한다.

## 대시보드 실행

* 단일 POC 대시보드: `uv run python -m test_dashboard.app_integrated`
* 주소: `http://127.0.0.1:8769`
* `test_dashboard/server.py`는 통합 대시보드의 공통 경로/Job 유틸리티이므로 유지한다.
* Test 05는 PDF, 배점표 JSON, OCR 모드를 입력한 뒤 실행한다. 결과에는 배점표·OCR 모드·페이지별 텍스트 출처, 항목별 산정점수·규칙 근거 페이지·AI 코멘트·인용이 기록된다.

## 갱신 규칙

작업을 완료하거나 새 블로커를 발견하면 이 문서의 완료, 다음 우선순위, 미확정/블로커를 함께 갱신한다.
