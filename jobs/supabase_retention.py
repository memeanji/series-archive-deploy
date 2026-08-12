# -*- coding: utf-8 -*-
"""Supabase 60일 보관정책 — 오래된 광고를 Supabase(DB+Storage)에서 정리한다.

규칙
  · 대상 = `is_preserved=0` 인 광고 중 **last_seen_at 이 RETENTION_DAYS(기본 60일)보다 오래된** 것.
    → 장기보존 19개 브랜드는 이전 때 `is_preserved=1` 로 넣었으므로 **절대 지워지지 않는다**.
  · 북마크/메모/스크립트가 있는 광고도 보존(사람이 남긴 흔적).
  · 삭제 순서: 자식(ad_view_snapshots → ad_social_matches) → 광고 → Storage 썸네일
    (다른 광고가 같은 썸네일을 참조하면 파일은 남긴다.)
  · **기본 DRY-RUN**. 실제 삭제는 `--apply`.

로컬 retention(`jobs/retention_cleanup.py`)이 끝난 뒤 이어서 호출되며, 단독 실행도 된다.
  python jobs/supabase_retention.py            # 집계만
  python jobs/supabase_retention.py --apply    # 실제 정리
"""
from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402

RETENTION_DAYS = int(config.secret("SUPABASE_RETENTION_DAYS") or 60)
BUCKET = "series-archive"
PAGE = 1000
CHUNK = 200


def _base() -> str:
    return (config.secret("SUPABASE_URL") or "").rstrip("/")


def _h(extra: dict | None = None) -> dict:
    k = config.secret("SUPABASE_SERVICE_KEY") or config.secret("SUPABASE_KEY")
    h = {"apikey": k, "Authorization": f"Bearer {k}", "Content-Type": "application/json"}
    h.update(extra or {})
    return h


def _page(table: str, query: str) -> list[dict]:
    out, start = [], 0
    while True:
        r = requests.get(f"{_base()}/rest/v1/{table}?{query}",
                         headers=_h({"Range": f"{start}-{start + PAGE - 1}"}), timeout=120)
        if r.status_code not in (200, 206):
            raise RuntimeError(f"{table} {r.status_code}: {r.text[:140]}")
        rows = r.json()
        out.extend(rows)
        if len(rows) < PAGE:
            return out
        start += PAGE


def _in(vals) -> str:
    return "(" + ",".join('"' + str(v).replace('"', "") + '"' for v in vals) + ")"


def expired_ads() -> list[dict]:
    """정리 대상 광고 — is_preserved=0 이고 last_seen_at 이 기준일보다 오래된 것."""
    cutoff = (dt.date.today() - dt.timedelta(days=RETENTION_DAYS)).isoformat()
    rows = _page("ad_library_ads",
                 "select=id,brand_name,last_seen_at,collected_at,storage_path,is_preserved,"
                 "is_bookmarked,memo,script_text&is_preserved=eq.0")
    out = []
    for a in rows:
        seen = (a.get("last_seen_at") or a.get("collected_at") or "")[:10]
        if not seen or seen >= cutoff:
            continue
        if a.get("is_bookmarked") or (a.get("memo") or "").strip() or (a.get("script_text") or "").strip():
            continue                      # 사람이 남긴 흔적이 있으면 보존
        out.append(a)
    return out


def _delete(table: str, query: str) -> int:
    r = requests.delete(f"{_base()}/rest/v1/{table}?{query}",
                        headers=_h({"Prefer": "return=minimal"}), timeout=180)
    if r.status_code not in (200, 204):
        print(f"  [{table}] 삭제 실패 {r.status_code}: {r.text[:140]}")
        return 0
    return 1


def run(apply: bool = False) -> dict:
    if not _base():
        print("Supabase 설정 없음 — 종료")
        return {}
    targets = expired_ads()
    ids = [a["id"] for a in targets]
    print(f"[Supabase retention] 기준 {RETENTION_DAYS}일 · 정리 대상 광고 {len(ids):,}건")
    if not ids:
        return {"ads": 0}
    by_brand: dict = {}
    for a in targets:
        by_brand[a.get("brand_name") or "(미상)"] = by_brand.get(a.get("brand_name") or "(미상)", 0) + 1
    for b, n in sorted(by_brand.items(), key=lambda x: -x[1])[:8]:
        print(f"    {b} {n:,}건")

    # 지울 광고가 참조하던 썸네일 중, 남는 광고가 안 쓰는 것만 파일 삭제
    doomed_paths = {(a.get("storage_path") or "") for a in targets} - {""}
    keep_paths = {(a.get("storage_path") or "") for a in
                  _page("ad_library_ads", "select=storage_path&is_preserved=eq.1")} - {""}
    if not apply:
        print(f"  [dry-run] 스냅샷/매칭/광고 삭제 + 썸네일 최대 {len(doomed_paths - keep_paths):,}개 삭제 예정")
        return {"ads": len(ids), "thumbs": len(doomed_paths - keep_paths), "applied": False}

    n_snap = n_match = 0
    for i in range(0, len(ids), CHUNK):
        c = ids[i:i + CHUNK]
        n_snap += _delete("ad_view_snapshots", f"ad_id=in.{_in(c)}")
        n_match += _delete("ad_social_matches", f"ad_id=in.{_in(c)}")
        _delete("ad_library_ads", f"id=in.{_in(c)}")
    # 남은 광고가 여전히 참조하는 썸네일은 제외하고 삭제
    still = {(a.get("storage_path") or "") for a in
             _page("ad_library_ads", "select=storage_path")} - {""}
    drop = sorted(doomed_paths - still)
    n_thumb = 0
    for i in range(0, len(drop), 100):
        r = requests.delete(f"{_base()}/storage/v1/object/{BUCKET}", headers=_h(),
                            json={"prefixes": drop[i:i + 100]}, timeout=120)
        if r.status_code == 200:
            n_thumb += len(drop[i:i + 100])
    print(f"  삭제 완료 — 광고 {len(ids):,} · 썸네일 {n_thumb:,}개 (스냅샷/매칭 동반 삭제)")
    return {"ads": len(ids), "thumbs": n_thumb, "applied": True}


if __name__ == "__main__":
    res = run(apply="--apply" in sys.argv)
    print(json.dumps(res, ensure_ascii=False))
