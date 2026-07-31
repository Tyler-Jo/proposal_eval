# Arvis Check 작업 하네스

이 문서는 작업 재개 시 가장 먼저 읽는 실행·검증 컨텍스트다. 구현 코드가 문서와 다르면 코드를 기준으로 확인한 뒤 이 문서를 갱신한다.

## 시스템 경계

```text
Tauri React UI (src/)
  └─ invoke: start_backend
       └─ Python sidecar (backend/.venv/bin/python -m api.server :8788)
            ├─ PDF 페이지 텍스트·루브릭 평가
            ├─ OpenVINO Gemma: 코멘트·인용 생성
            └─ POC/test_dashboard: 검증 하네스
```

## 핵심 경로

| 경로 | 역할 |
|---|---|
| `src/main.tsx` | UI 흐름과 API 작업 폴링 |
| `src/api.ts` | sidecar HTTP 클라이언트 계약 |
| `src-tauri/src/main.rs` | Python sidecar 시작 및 수명 관리 |
| `src-tauri/capabilities/default.json` | PDF 네이티브 선택 권한 |
| `backend/api/server.py` | 프로젝트·문서·평가 API |
| `backend/api/local_llm.py` | `/home/user/develop/model/ko-gemma-2-9b-it-int4-ov` OpenVINO Gemma 어댑터 |
| `backend/test_dashboard/document_evaluation.py` | 페이지, 루브릭, 인용의 공통 계약 |
| `backend/tests/` | 단위 테스트 및 POC |
| `backend/test_dashboard/` | 수동 POC 실행·검토 대시보드 |

## API 계약

| 메서드 | 경로 | 목적 |
|---|---|---|
| `GET` | `/health` | sidecar 상태 확인 |
| `POST` | `/projects` | 사업 생성 (`name`) |
| `POST` | `/projects/{id}/documents` | PDF 경로 등록 (`kind`: `RFP`/`A`/`B`, `paths`) |
| `POST` | `/projects/{id}/evaluations` | 배점표 평가 작업 시작 (`rubric`) |
| `GET` | `/evaluations/{id}` | `RUNNING`/`COMPLETED`/`FAILED` 결과 조회 |

평가 응답의 불변 원칙:

* 점수는 `required_keywords`, `max_score` 규칙으로만 계산한다.
* 모델은 `comment`, `evidence`만 작성한다.
* `citation_pages`는 입력 페이지에서 검증된 번호만 포함한다.
* 모델 실패는 작업 실패로 숨기지 않고 `comment_source: rule_based_fallback`으로 기록한다.

## 개발 명령

```bash
# UI 개발 실행
npm run tauri dev

# UI 빌드
npm run build

# Rust sidecar 경로 검증
cargo check --manifest-path src-tauri/Cargo.toml

# 빠른 문서 평가 테스트
cd backend && .venv/bin/python -m pytest tests/test_05_document_evaluation.py -q

# sidecar 단독 실행
cd backend && .venv/bin/python -m api.server --port 8788

# POC 대시보드
cd backend && uv run python -m test_dashboard.app_integrated
```

## 현재 제약

* sidecar API는 현재 텍스트 레이어 PDF를 처리한다. 다음 우선순위는 RFP 평가표·체계규격의 좌표 기반 구조화이며, PaddleOCR은 텍스트가 없거나 손상된 페이지·영역을 보완하는 후속 단계다.
* 로컬 OpenVINO Gemma의 첫 요청은 모델 로드·컴파일 시간이 필요하며, 이후 같은 sidecar 프로세스에서 재사용한다.
* 현재 Tauri sidecar 경로는 개발 환경의 `backend/.venv/bin/python`이다. Windows 배포에는 별도 Python sidecar 패키징이 필요하다.
* 실제 제안서·RFP·개인정보 포함 원본은 저장소에 추가하지 않는다.

## 변경 전 확인 목록

1. 점수 계산을 모델 출력으로 변경하지 않는다.
2. OCR/LLM 엔진을 바꿔도 API 응답의 페이지·인용·출처 필드는 유지한다.
3. POC 결과의 결함은 먼저 작은 fixture와 단위 테스트로 재현한다.
4. `backend/` 경로나 sidecar 실행 명령을 바꾸면 `src-tauri/src/main.rs`, 이 문서, `docs/project_plan.md`를 함께 갱신한다.
5. 완료 전 최소 `npm run build`, 관련 Python 테스트, `cargo check`을 실행한다.
