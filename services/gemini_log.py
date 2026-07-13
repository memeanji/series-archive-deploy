"""Gemini 호출 로그 — 호출 위치/시각/프롬프트 길이/성공여부/에러코드 기록.
   logs/gemini_calls.log 에 1줄/호출. 어디서 Gemini가 불리는지 추적·감사용.
"""
import os
from datetime import datetime

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_LOG = os.path.join(_ROOT, "logs", "gemini_calls.log")


def log_call(function: str, prompt_len: int = 0, ok: bool = True,
             code: str = "", ad_id: str = "", note: str = "") -> None:
    try:
        os.makedirs(os.path.dirname(_LOG), exist_ok=True)
        line = (f"{datetime.now():%Y-%m-%d %H:%M:%S}\t{function}\tad={ad_id or '-'}\t"
                f"prompt_len={prompt_len}\t{'OK' if ok else 'FAIL'}\tcode={code or '-'}\t{note}\n")
        with open(_LOG, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:  # noqa: BLE001
        pass
