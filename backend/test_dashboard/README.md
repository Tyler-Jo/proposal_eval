# Test Dashboard

Test 01·02 POC를 로컬 브라우저에서 실행하고 결과를 확인하는 오프라인 대시보드다. 제품 `src/`는 사용하지 않으며, `tests/results/`의 artifact만 읽고 새 POC는 `pytest`로 실행한다.

## 실행

```bash
uv run python -m test_dashboard.server
```

브라우저에서 `http://127.0.0.1:8765`을 연다.

## 현재 지원 범위

* Test 01: fixture PDF 선택, 30/50 페이지 청크 설정, 반복 실행, 청크별 RSS·시간 표 및 막대 그래프
* Test 02: fixture PDF 선택, 블라인드 후보 실행, PDF 페이지 이미지 위 bbox overlay 및 후보 목록

입력 PDF는 보안상 `tests/fixtures/pdf/` 아래의 파일만 선택할 수 있다. 결과는 `tests/results/`에 저장되며 Git에 포함되지 않는다.
