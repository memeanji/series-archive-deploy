"""
저장 레이어.
- Supabase 가 .env 에 설정돼 있으면 Supabase(PostgREST)로 쓴다.
- 없으면 data/local_db.json 로컬 파일에 쓴다(개발/mock 모드, 즉시 실행 가능).

두 경우 모두 동일한 함수 인터페이스를 노출한다:
  upsert_ad(ad) -> ad_id
  insert_snapshot(ad_id, snap)
  fetch_all_ads() -> list[dict]
  update_ad_score(ad_id, score)
  log_collection(...)

dedup 기준:
  platform_ad_id 있으면 (platform, platform_ad_id)
  없으면          (platform, dedup_key)
중복이면 last_seen + 지표 + 상태를 갱신, 없으면 신규 insert.
"""
from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timezone
from typing import Any, Optional

import config

_METRIC_KEYS = ("views", "likes", "comments", "shares")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _today() -> str:
    return date.today().isoformat()


def _dedup_match_key(ad: dict) -> tuple[str, str]:
    """(컬럼명, 값) — 이 광고를 식별하는 키."""
    if ad.get("platform_ad_id"):
        return "platform_ad_id", ad["platform_ad_id"]
    return "dedup_key", ad.get("dedup_key") or ""


# ════════════════════════════════════════════════════════════
# 로컬 파일 스토어 (Supabase 미설정 시)
# ════════════════════════════════════════════════════════════
class LocalStore:
    def __init__(self, path):
        self.path = path
        self.db = self._load()

    def _load(self) -> dict:
        if self.path.exists():
            try:
                return json.loads(self.path.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                pass
        return {"ads": {}, "ad_snapshots": [], "collection_logs": []}

    def _save(self) -> None:
        self.path.write_text(
            json.dumps(self.db, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )

    def _find(self, ad: dict) -> Optional[dict]:
        col, val = _dedup_match_key(ad)
        if not val:
            return None
        for row in self.db["ads"].values():
            if row.get("platform") == ad.get("platform") and row.get(col) == val:
                return row
        return None

    def upsert_ad(self, ad: dict) -> str:
        existing = self._find(ad)
        if existing:
            existing["last_seen"] = ad.get("last_seen") or existing.get("last_seen")
            existing["status"] = ad.get("status") or existing.get("status")
            for k in _METRIC_KEYS:
                if ad.get(k) is not None:
                    existing[k] = ad[k]
            for k in ("estimated_running_days", "hook_tags", "format_tags",
                      "thumbnail_url", "video_url", "transcript", "raw_data"):
                if ad.get(k) is not None:
                    existing[k] = ad[k]
            existing["updated_at"] = _now_iso()
            self._save()
            return existing["id"]

        ad_id = str(uuid.uuid4())
        row = dict(ad)
        row["id"] = ad_id
        row.setdefault("first_seen", ad.get("first_seen") or _today())
        row.setdefault("last_seen", ad.get("last_seen") or _today())
        row.setdefault("reference_score", 0)
        row["created_at"] = _now_iso()
        row["updated_at"] = _now_iso()
        self.db["ads"][ad_id] = row
        self._save()
        return ad_id

    def insert_snapshot(self, ad_id: str, snap: dict) -> None:
        # (ad_id, snapshot_date) 중복이면 덮어쓰기
        d = snap.get("snapshot_date") or _today()
        self.db["ad_snapshots"] = [
            s for s in self.db["ad_snapshots"]
            if not (s.get("ad_id") == ad_id and s.get("snapshot_date") == d)
        ]
        self.db["ad_snapshots"].append({
            "id": str(uuid.uuid4()), "ad_id": ad_id, "snapshot_date": d,
            **{k: snap.get(k) for k in ("status", *_METRIC_KEYS, "raw_data")},
            "created_at": _now_iso(),
        })
        self._save()

    def fetch_all_ads(self) -> list[dict]:
        return list(self.db["ads"].values())

    def update_ad_score(self, ad_id: str, score: int) -> None:
        if ad_id in self.db["ads"]:
            self.db["ads"][ad_id]["reference_score"] = score
            self.db["ads"][ad_id]["updated_at"] = _now_iso()
            self._save()

    def log_collection(self, **kw) -> None:
        self.db["collection_logs"].append({"id": str(uuid.uuid4()), **kw, "created_at": _now_iso()})
        self._save()


# ════════════════════════════════════════════════════════════
# Supabase 스토어
# ════════════════════════════════════════════════════════════
class SupabaseStore:
    def __init__(self, url: str, key: str):
        from supabase import create_client  # 지연 import (미설치 시 로컬모드엔 영향 없음)
        self.client = create_client(url, key)

    def upsert_ad(self, ad: dict) -> str:
        col, val = _dedup_match_key(ad)
        q = self.client.table("ads").select("id").eq("platform", ad["platform"])
        existing = q.eq(col, val).limit(1).execute().data if val else []
        if existing:
            ad_id = existing[0]["id"]
            patch = {"last_seen": ad.get("last_seen"), "status": ad.get("status")}
            for k in (*_METRIC_KEYS, "estimated_running_days", "hook_tags",
                      "format_tags", "thumbnail_url", "video_url", "transcript", "raw_data"):
                if ad.get(k) is not None:
                    patch[k] = ad[k]
            self.client.table("ads").update(patch).eq("id", ad_id).execute()
            return ad_id
        res = self.client.table("ads").insert(ad).execute()
        return res.data[0]["id"]

    def insert_snapshot(self, ad_id: str, snap: dict) -> None:
        row = {"ad_id": ad_id, "snapshot_date": snap.get("snapshot_date") or _today(),
               **{k: snap.get(k) for k in ("status", *_METRIC_KEYS, "raw_data")}}
        # (ad_id, snapshot_date) unique → upsert
        self.client.table("ad_snapshots").upsert(row, on_conflict="ad_id,snapshot_date").execute()

    def fetch_all_ads(self) -> list[dict]:
        rows, page, size = [], 0, 1000
        while True:
            chunk = (self.client.table("ads").select("*")
                     .range(page * size, page * size + size - 1).execute().data)
            rows.extend(chunk)
            if len(chunk) < size:
                break
            page += 1
        return rows

    def update_ad_score(self, ad_id: str, score: int) -> None:
        self.client.table("ads").update({"reference_score": score}).eq("id", ad_id).execute()

    def log_collection(self, **kw) -> None:
        self.client.table("collection_logs").insert(kw).execute()


# ── 싱글톤 스토어 선택 ────────────────────────────────────
_store: Any = None


def get_store():
    global _store
    if _store is None:
        if config.USE_SUPABASE:
            _store = SupabaseStore(config.SUPABASE_URL, config.SUPABASE_SERVICE_ROLE_KEY)
            print("  [store] Supabase 모드")
        else:
            _store = LocalStore(config.LOCAL_DB_PATH)
            print(f"  [store] 로컬 모드 → {config.LOCAL_DB_PATH}")
    return _store
