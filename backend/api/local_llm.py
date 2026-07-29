"""로컬 OpenVINO Gemma 어댑터. 네트워크·vLLM 없이 직접 모델을 호출한다."""

from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path
from typing import Any

DEFAULT_MODEL_PATH = "/home/user/develop/model/ko-gemma-2-9b-it-int4-ov"

class LocalModelError(RuntimeError):
    pass

class LocalOpenVinoGemma:
    """모델과 토크나이저를 프로세스당 한 번만 지연 로드한다."""
    def __init__(self, model_path: str | None = None) -> None:
        self.model_path = Path(model_path or os.environ.get("AVIS_LOCAL_MODEL_PATH", DEFAULT_MODEL_PATH))
        self._model: Any | None = None
        self._tokenizer: Any | None = None
        self._lock = threading.Lock()

    def _load(self) -> None:
        if self._model is not None:
            return
        if not (self.model_path / "openvino_model.xml").is_file():
            raise LocalModelError(f"OpenVINO 모델을 찾을 수 없습니다: {self.model_path}")
        try:
            from optimum.intel.openvino import OVModelForCausalLM
            from transformers import AutoTokenizer
        except ImportError as error:
            raise LocalModelError("OpenVINO 모델 런타임이 설치되지 않았습니다. `uv sync`를 완료하세요.") from error
        with self._lock:
            if self._model is None:
                self._tokenizer = AutoTokenizer.from_pretrained(str(self.model_path), trust_remote_code=True)
                self._model = OVModelForCausalLM.from_pretrained(str(self.model_path), device="AUTO", compile=True)

    def generate_comment(self, item: dict[str, Any], pages: list[tuple[int, str]]) -> tuple[str, str]:
        self._load()
        assert self._model is not None and self._tokenizer is not None
        evidence_pages = set(item.get("evidence_pages", []))
        source = "\n\n".join(f"[페이지 {page}]\n{text}" for page, text in pages if page in evidence_pages)[:6000]
        if not source:
            source = "\n\n".join(f"[페이지 {page}]\n{text}" for page, text in pages[:2])[:6000]
        prompt = f"""당신은 공공 제안서 심사 보조자입니다. 점수는 이미 규칙으로 확정됐으므로 절대 변경하지 마세요.
평가 항목: {item['name']}
규칙 결과: {item['score']}/{item['max_score']}점, 상태 {item['status']}
누락 키워드: {', '.join(item['missing_keywords']) or '없음'}
아래 원문만 근거로 2문장 이내의 심사 코멘트와 300자 이하의 연속 원문 인용을 작성하세요.
반드시 JSON 객체만 출력하세요: {{"comment":"...", "evidence":"원문 그대로의 발췌"}}

원문:
{source}"""
        messages = [{"role": "system", "content": "JSON만 출력하는 오프라인 심사 보조 모델입니다."}, {"role": "user", "content": prompt}]
        try:
            rendered = self._tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
            inputs = self._tokenizer(rendered, return_tensors="pt")
            generated = self._model.generate(**inputs, max_new_tokens=400, do_sample=False)
            text = self._tokenizer.decode(generated[0][inputs["input_ids"].shape[-1]:], skip_special_tokens=True).strip()
            value = json.loads(re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE))
            if not isinstance(value.get("comment"), str) or not isinstance(value.get("evidence"), str):
                raise ValueError("comment/evidence 필드가 없습니다.")
            return value["comment"].strip(), value["evidence"].strip()[:300]
        except Exception as error:
            raise LocalModelError(f"로컬 Gemma 생성 실패: {error}") from error

LOCAL_MODEL = LocalOpenVinoGemma()
