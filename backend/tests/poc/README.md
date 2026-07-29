# Test 01 — PDF 청크·메모리 POC 실행법

이 POC는 제품 `src/` 코드에 의존하지 않는다. 실제 PDF를 PyMuPDF로 청크 단위 렌더링하고, 각 청크의 RSS 메모리와 처리 시간을 `tests/results/`에 기록한다.

## 필요 패키지

```bash
pip install pytest pymupdf psutil
```

## Windows PowerShell 예시

```powershell
$env:POC_PDF_PATH = 'D:\secure-data\representative-30-pages.pdf'
$env:POC_CHUNK_SIZES = '30,50'
$env:POC_REPEAT = '3'
$env:POC_RENDER_SCALE = '1.0'
pytest -m poc tests/poc/test_01_pdf_chunking_poc.py -s
```

50~100페이지 검증 뒤, 같은 명령으로 300페이지 이상 PDF를 지정한다. POC에서 허용 가능한 GC 후 RSS 증가량이 확정되면 아래 값을 추가해 자동 실패 기준으로 사용한다.

```powershell
$env:POC_MAX_POST_GC_GROWTH_MB = '500'
```

## 산출물

각 실행은 `tests/results/test_01_<UTC timestamp>/`에 다음 파일을 만든다.

* `chunks.csv`: 청크별 RSS 전/최대/GC 후 값과 렌더링 시간
* `summary.json`: 입력 PDF 해시, 환경, 설정, 집계 메트릭

입력 PDF 및 결과물은 `.gitignore`로 제외된다. 실제 제안서·RFP·개인정보가 포함된 문서는 저장소에 넣지 않는다.
