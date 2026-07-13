"""
수집기 공통 헬퍼.
- 모든 수집기는 collect() -> list[dict] 를 노출하며, 반환 dict 는 normalize_ad 로 표준화된다.
- mock 데이터는 data/mock_ads.json 한 곳에서 읽고, *_offset(오늘로부터 며칠 전) 을 실제 날짜로 변환한다.
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import config
from services.media_utils import normalize_ad
from services.tagging import auto_tag

_MOCK_PATH = Path(config.DATA_DIR) / "mock_ads.json"


def _resolve_offsets(raw: dict) -> dict:
    """first_seen_offset / last_seen_offset(일) → 실제 날짜로 변환."""
    out = dict(raw)
    today = date.today()
    if "first_seen_offset" in out:
        out["first_seen"] = (today - timedelta(days=int(out.pop("first_seen_offset")))).isoformat()
    if "last_seen_offset" in out:
        out["last_seen"] = (today - timedelta(days=int(out.pop("last_seen_offset")))).isoformat()
    return out


def load_mock(platform: str) -> list[dict]:
    """mock_ads.json 에서 해당 플랫폼 광고만 읽어 표준화 + 자동 태깅."""
    if not _MOCK_PATH.exists():
        return []
    rows = json.loads(_MOCK_PATH.read_text(encoding="utf-8"))
    out = []
    for raw in rows:
        if raw.get("platform") != platform:
            continue
        ad = normalize_ad(_resolve_offsets(raw))
        if not ad.get("hook_tags") and not ad.get("format_tags"):
            ad["hook_tags"], ad["format_tags"] = auto_tag(ad)
        out.append(ad)
    return out


def finalize(ads: list[dict]) -> list[dict]:
    """실제 API 결과에도 자동 태깅을 입혀 표준 형태로 마무리."""
    out = []
    for raw in ads:
        ad = normalize_ad(raw)
        ad["hook_tags"], ad["format_tags"] = auto_tag(ad)
        out.append(ad)
    return out
