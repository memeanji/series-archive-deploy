"""
Supabase(외부 Postgres)에 영상 스크립트를 영구 저장/복원.
Cloud는 reboot 시 로컬 DB가 초기화되므로, 생성된 스크립트를 Supabase에 백업해 둔다.
SUPABASE_URL / (SUPABASE_SERVICE_KEY 또는 SUPABASE_KEY) 가 있을 때만 활성화. 없으면 무동작.
키 값은 로그/화면에 노출하지 않음(존재 여부 bool만).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402

TABLE = "ad_scripts"


def _base() -> str:
    u = (config.secret("SUPABASE_URL") or "").rstrip("/")
    if not u:
        return ""
    return u if u.endswith("/rest/v1") else u + "/rest/v1"


def _key() -> str:
    # 서버측이므로 secret(service) 키 우선, 없으면 publishable
    return config.secret("SUPABASE_SERVICE_KEY") or config.secret("SUPABASE_KEY")


def enabled() -> bool:
    return bool(_base() and _key())


def _headers() -> dict:
    k = _key()
    return {"apikey": k, "Authorization": f"Bearer {k}", "Content-Type": "application/json"}


def save_script(ad_id: str, text: str, source: str, status: str,
                brand: str = "", platform: str = "", video_url: str = "") -> None:
    """스크립트 1건 upsert(ad_id 충돌 시 병합). 실패해도 앱 영향 없음."""
    if not enabled() or not ad_id or not (text or "").strip():
        return
    import requests
    body = {"ad_id": ad_id, "script_text": text, "script_source": source,
            "script_status": status, "brand_name": brand, "platform": platform,
            "video_url": video_url}
    try:
        requests.post(f"{_base()}/{TABLE}?on_conflict=ad_id", json=body, timeout=15,
                      headers={**_headers(), "Prefer": "resolution=merge-duplicates"})
    except Exception:  # noqa: BLE001
        pass


def load_all() -> list[dict]:
    """저장된 모든 스크립트 조회 → [{ad_id, script_text, script_source, script_status}]."""
    if not enabled():
        return []
    import requests
    try:
        r = requests.get(
            f"{_base()}/{TABLE}?select=ad_id,script_text,script_source,script_status",
            headers=_headers(), timeout=20)
        return r.json() if r.status_code == 200 and isinstance(r.json(), list) else []
    except Exception:  # noqa: BLE001
        return []
