"""Supabase(외부 Postgres)에 북마크(ad_id)를 영구 저장/복원 — 팀 공유(여러 기기/여러 명).
Cloud는 reboot/재배포 시 로컬 DB가 demo.db로 초기화되므로 북마크가 유실됨 → Supabase에 기록해 복원.
SUPABASE_URL + (SUPABASE_SERVICE_KEY 또는 SUPABASE_KEY) 있을 때만 활성. 없으면 무동작(로컬 DB 유지).

Supabase에 아래 테이블 1개 필요:
  create table if not exists ad_bookmarks (
    ad_id text primary key,
    username text,
    created_at timestamptz default now()
  );
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402

TABLE = "ad_bookmarks"


def _base() -> str:
    u = (config.secret("SUPABASE_URL") or "").rstrip("/")
    if not u:
        return ""
    return u if u.endswith("/rest/v1") else u + "/rest/v1"


def _key() -> str:
    return config.secret("SUPABASE_SERVICE_KEY") or config.secret("SUPABASE_KEY")


def enabled() -> bool:
    return bool(_base() and _key())


def _headers() -> dict:
    k = _key()
    return {"apikey": k, "Authorization": f"Bearer {k}", "Content-Type": "application/json"}


def add(ad_id: str, username: str = "") -> None:
    """북마크 추가(ad_id upsert). 실패해도 앱 영향 없음."""
    if not enabled() or not ad_id:
        return
    import requests
    try:
        requests.post(f"{_base()}/{TABLE}?on_conflict=ad_id", timeout=15,
                      json={"ad_id": ad_id, "username": username or ""},
                      headers={**_headers(), "Prefer": "resolution=merge-duplicates"})
    except Exception:  # noqa: BLE001
        pass


def remove(ad_id: str) -> None:
    """북마크 해제."""
    if not enabled() or not ad_id:
        return
    import requests
    try:
        requests.delete(f"{_base()}/{TABLE}?ad_id=eq.{ad_id}", headers=_headers(), timeout=15)
    except Exception:  # noqa: BLE001
        pass


def load_all():
    """북마크된 ad_id 목록. 성공 시 list, 미설정/실패 시 None(→ 복원 시 로컬 유지)."""
    if not enabled():
        return None
    import requests
    try:
        r = requests.get(f"{_base()}/{TABLE}?select=ad_id", headers=_headers(), timeout=20)
        if r.status_code == 200 and isinstance(r.json(), list):
            return [x["ad_id"] for x in r.json() if x.get("ad_id")]
        return None
    except Exception:  # noqa: BLE001
        return None
