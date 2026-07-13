"""
Apify 공통 클라이언트 — 토큰 검증 + Actor 실행(소량 제한).
비용 방지: max_items 를 항상 낮게. APIFY_TOKEN 없으면 비활성.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402

BASE = "https://api.apify.com/v2"


def is_enabled() -> bool:
    return bool(config.USE_APIFY and config.APIFY_TOKEN)


def validate_token() -> dict | None:
    """GET users/me — 토큰 유효성/요금제 확인(무료)."""
    if not config.APIFY_TOKEN:
        return None
    import requests
    try:
        r = requests.get(f"{BASE}/users/me", params={"token": config.APIFY_TOKEN}, timeout=20)
        if r.status_code != 200:
            return None
        return r.json().get("data")
    except Exception:  # noqa: BLE001
        return None


def run_actor(actor: str, run_input: dict, max_items: int = 20,
              timeout_secs: int = 180) -> tuple[list, dict]:
    """
    run-sync-get-dataset-items 로 Actor 실행 후 데이터셋 반환.
    반환: (items, meta) — meta={actor, run_id, item_count, error}
    """
    meta = {"actor": actor, "run_id": "(sync)", "item_count": 0, "error": None}
    if not is_enabled() or not actor:
        meta["error"] = "apify disabled or no actor"
        return [], meta
    import requests
    url = f"{BASE}/acts/{actor}/run-sync-get-dataset-items"
    try:
        r = requests.post(url, params={"token": config.APIFY_TOKEN, "maxItems": max_items,
                                       "timeout": timeout_secs},
                          json=run_input, timeout=timeout_secs + 30)
        meta["run_id"] = r.headers.get("X-Apify-Pagination-Total", "(sync)")
        if r.status_code not in (200, 201):
            meta["error"] = f"HTTP {r.status_code}: {r.text[:160]}"
            return [], meta
        items = r.json()
        if not isinstance(items, list):
            items = []
        meta["item_count"] = len(items)
        return items, meta
    except Exception as e:  # noqa: BLE001
        meta["error"] = f"{type(e).__name__}: {str(e)[:160]}"
        return [], meta
