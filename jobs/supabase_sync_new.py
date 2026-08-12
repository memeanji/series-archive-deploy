# -*- coding: utf-8 -*-
"""크롤 직후 **신규분만** Supabase로 올린다(멱등).

무엇을 올리나
  · 신규 광고      : 로컬에는 있는데 Supabase에 없는 광고(오염 차단 규칙·제외 표시분은 뺀다)
  · 신규 브랜드    : brands 테이블에 새로 생긴 행
  · 신규 썸네일    : 그 광고들이 참조하는 파일 중 **Storage에 아직 없는 것만**
  · 딸린 데이터    : 그 광고의 영상 매칭 + 조회수 스냅샷

무엇을 안 하나
  · 전체 재업로드(이미 올라간 광고·썸네일은 건너뛴다)
  · git 커밋/푸시(이 잡은 git을 전혀 건드리지 않는다)

업서트는 `jobs/supabase_migrate.py` 의 기존 로직을 그대로 재사용하므로 몇 번 돌려도 중복이 안 생긴다.

사용:
  python jobs/supabase_sync_new.py             # DRY-RUN(집계만)
  python jobs/supabase_sync_new.py --apply     # 실제 반영
  옵션: --days N (최근 N일 신규분만) · --workers 8
"""
from __future__ import annotations

import argparse
import importlib.util
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402
import database  # noqa: E402
import services.supabase_read as sr  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
LIVE = ROOT / "data" / "series_archive.db"

# 장기보존 브랜드(= is_preserved=1 로 올려 60일 retention에서 빼는 대상)
_PRESERVED: set = set()
for _p in sorted((ROOT / "data").glob("supabase_migration_plan*.json")):
    if _p.name == "supabase_migration_plan_rest66.json":
        continue                      # 66개는 retention 대상이라 보존 표시 안 함
    import json as _json
    _PRESERVED |= set(_json.loads(_p.read_text(encoding="utf-8"))["brands"])


def _migrate_mod():
    """supabase_migrate 의 업서트/업로드 로직을 그대로 재사용(중복 구현 금지)."""
    spec = importlib.util.spec_from_file_location("sm", ROOT / "jobs" / "supabase_migrate.py")
    mod = importlib.util.module_from_spec(spec)
    argv, sys.argv = sys.argv, ["supabase_migrate"]      # 모듈 최상단이 argv를 보지 않게
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.argv = argv
    return mod


def _storage_objects(sm) -> set:
    """Storage 에 이미 있는 썸네일 파일명 집합(있는 건 다시 안 올린다)."""
    import requests
    out, off = set(), 0
    while True:
        r = requests.post(f"{sm._base()}/storage/v1/object/list/{sm.BUCKET}",
                          headers=sm._h({"Content-Type": "application/json"}),
                          json={"prefix": sm.PREFIX, "limit": 1000, "offset": off}, timeout=90)
        if r.status_code != 200:
            raise RuntimeError(f"Storage 목록 {r.status_code}: {r.text[:120]}")
        items = r.json()
        out |= {x["name"] for x in items}
        if len(items) < 1000:
            return out
        off += 1000


def run(apply: bool = False, days: int = 0, workers: int = 8) -> dict:
    sm = _migrate_mod()
    if not sm._base() or not sm._key():
        print("Supabase 설정 없음 — 동기화 생략")
        return {}

    # ── ① Supabase 현황(검증된 페이지네이션 사용) ─────────────────────────
    sb_ids = {x["id"] for x in sr._fetch("ad_library_ads", "select=id")}
    sb_brands = {x["display_name"] for x in sr._fetch("brands", "select=display_name")}

    # ── ② 로컬에서 '아직 안 올라간' 광고 추리기 ───────────────────────────
    con = sqlite3.connect(str(LIVE))
    con.row_factory = sqlite3.Row
    bl = database.load_ingest_blocklist(con)
    where = "COALESCE(is_excluded,0)=0"
    args: list = []
    if days > 0:
        where += " AND substr(COALESCE(first_seen_at,collected_at),1,10) >= date('now',?)"
        args.append(f"-{days} day")
    new_ads, blocked = [], 0
    for r in con.execute(f"SELECT * FROM ad_library_ads WHERE {where}", args):
        d = dict(r)
        if d["id"] in sb_ids:
            continue                                  # 이미 올라감 → 건너뜀(멱등)
        if database._blocked_reason(d, bl):
            blocked += 1
            continue                                  # 오염 차단 규칙
        new_ads.append(d)
    new_ids = {a["id"] for a in new_ads}

    new_brands = [dict(r) for r in con.execute("SELECT * FROM brands")
                  if r["display_name"] not in sb_brands]
    matches = [dict(r) for r in con.execute("SELECT * FROM ad_social_matches")
               if r["ad_id"] in new_ids]
    snaps = [dict(r) for r in con.execute("SELECT * FROM ad_view_snapshots")
             if r["ad_id"] in new_ids]
    cols = {t: [x[1] for x in con.execute(f"PRAGMA table_info({t})")]
            for t in ("ad_library_ads", "ad_social_matches", "ad_view_snapshots", "brands")}
    con.close()

    # ── ③ 썸네일: Storage에 없는 파일만 ───────────────────────────────────
    thumbs = sm.thumb_files(new_ads)                  # ad_id -> [로컬경로]
    have_obj = _storage_objects(sm)
    files, key_of = [], {}
    for aid, fs in thumbs.items():
        name = Path(fs[0]).name
        key_of[aid] = sm.PREFIX + name
        if name not in have_obj:
            files.append(fs[0])

    print(f"[신규 동기화] 광고 {len(new_ads):,} · 브랜드 {len(new_brands)} · 매칭 {len(matches):,} · "
          f"스냅샷 {len(snaps):,} · 썸네일 업로드 대상 {len(files):,}"
          f"{f' (이미 있음 {len(thumbs)-len(files):,})' if thumbs else ''}"
          f"{f' · 오염 차단 {blocked}' if blocked else ''}")
    if not apply:
        print("  [dry-run] 실제 반영은 --apply")
        return {"ads": len(new_ads), "thumbs": len(files), "applied": False}
    if not new_ads and not new_brands:
        return {"ads": 0, "thumbs": 0, "applied": True}

    # ── ④ 업로드 & 업서트(기존 멱등 로직 재사용) ──────────────────────────
    up = sm.upload_thumbs(files, True, workers) if files else {"uploaded": 0, "failed": 0}
    rows = []
    for a in new_ads:
        key = key_of.get(a["id"], "")
        rows.append(sm._clean(a, cols["ad_library_ads"], {
            "storage_path": key,
            "thumbnail_url": sm.public_url(key) if key else (a.get("thumbnail_url") or ""),
            "orig_thumbnail_url": a.get("thumbnail_url") or "",
            "local_thumbnail_path": key,
            "is_preserved": 1 if a.get("brand_name") in _PRESERVED else (a.get("is_preserved") or 0),
        }))
    def _sent(r) -> int:            # sm.upsert 는 {"sent":n,"failed":n} 를 준다
        return int(r.get("sent", 0)) if isinstance(r, dict) else int(r or 0)

    res = {"ads": _sent(sm.upsert("ad_library_ads", rows, "id", True)),
           "brands": _sent(sm.upsert("brands", [sm._clean(b, cols["brands"]) for b in new_brands],
                                     "id", True)) if new_brands else 0,
           "matches": _sent(sm.upsert("ad_social_matches",
                                      [sm._clean(m, cols["ad_social_matches"]) for m in matches],
                                      "ad_id,social_id", True)) if matches else 0,
           "snapshots": _sent(sm.upsert("ad_view_snapshots",
                                        [sm._clean(s, cols["ad_view_snapshots"] + ["view_snapshot_source"],
                                                   {"view_snapshot_source": "live"}) for s in snaps],
                                        "ad_id,snapshot_date", True)) if snaps else 0,
           "thumbs": up.get("uploaded", 0), "thumb_failed": up.get("failed", 0), "applied": True}
    print(f"  반영 완료 — 광고 {res['ads']:,} · 브랜드 {res['brands']} · 매칭 {res['matches']:,} · "
          f"스냅샷 {res['snapshots']:,} · 썸네일 {res['thumbs']:,}(실패 {res['thumb_failed']})")
    return res


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--days", type=int, default=0, help="최근 N일 신규분만(0=Supabase에 없는 것 전부)")
    ap.add_argument("--workers", type=int, default=8)
    a = ap.parse_args()
    run(apply=a.apply, days=a.days, workers=a.workers)


if __name__ == "__main__":
    main()
