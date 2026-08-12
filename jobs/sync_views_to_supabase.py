# -*- coding: utf-8 -*-
"""조회수 수집 결과를 Supabase로 동기화 — 이전된 브랜드 광고분만.

동기화 대상
  ① video_view_state   : 영상(social_id)별 **최신 조회수** — 표가 아직 없으면 조용히 건너뜀
  ② ad_library_ads     : yt_views / yt_likes / yt_comments (카드·상세가 읽는 현재값)
  ③ ad_view_snapshots  : 오늘 새로 저장된 추이 스냅샷

`jobs/snapshot_views.py` 끝에서 자동 호출되며, 단독 실행도 된다.
  python jobs/sync_views_to_supabase.py
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
LIVE = ROOT / "data" / "series_archive.db"
BATCH = 500


def _base() -> str:
    return (config.secret("SUPABASE_URL") or "").rstrip("/")


def _h() -> dict:
    k = config.secret("SUPABASE_SERVICE_KEY") or config.secret("SUPABASE_KEY")
    return {"apikey": k, "Authorization": f"Bearer {k}", "Content-Type": "application/json",
            "Prefer": "resolution=merge-duplicates,return=minimal"}


def _supabase_ad_ids() -> set:
    """Supabase 에 있는 광고 ID 전량. ⚠️PostgREST 는 1,000행에서 잘리므로 Range 로 페이지네이션."""
    import requests
    out, start = set(), 0
    while True:
        r = requests.get(f"{_base()}/rest/v1/ad_library_ads?select=id",
                         headers={**_h(), "Range": f"{start}-{start + 999}"}, timeout=120)
        if r.status_code not in (200, 206):
            raise RuntimeError(f"광고 ID 조회 {r.status_code}: {r.text[:120]}")
        page = r.json()
        out |= {x["id"] for x in page}
        if len(page) < 1000:
            return out
        start += 1000


def _post(table: str, rows: list[dict], on_conflict: str) -> int:
    import requests
    sent = 0
    for i in range(0, len(rows), BATCH):
        chunk = rows[i:i + BATCH]
        r = requests.post(f"{_base()}/rest/v1/{table}?on_conflict={on_conflict}",
                          headers=_h(), data=json.dumps(chunk, default=str).encode(), timeout=180)
        if r.status_code in (200, 201, 204):
            sent += len(chunk)
        elif "does not exist" in (r.text or "") or r.status_code == 404:
            print(f"  [{table}] 표가 아직 없음 — 건너뜀(DDL 실행 필요)")
            return -1
        else:
            print(f"  [{table}] 실패 {r.status_code}: {r.text[:160]}")
    return sent


def sync(verbose: bool = True) -> dict:
    if not _base() or not (config.secret("SUPABASE_SERVICE_KEY") or config.secret("SUPABASE_KEY")):
        if verbose:
            print("Supabase 설정 없음 — 동기화 생략")
        return {}
    con = sqlite3.connect(str(LIVE))
    con.row_factory = sqlite3.Row
    have = _supabase_ad_ids()

    # ① 최신 조회수(영상 단위) — 이전된 광고가 쓰는 영상만
    vids = set()
    for r in con.execute("SELECT id, video_url FROM ad_library_ads WHERE video_url LIKE '%youtube%'"):
        if r["id"] in have:
            import services.youtube as YT
            v = YT.extract_video_id(r["video_url"] or "")
            if v:
                vids.add(v)
    state_rows = []
    for r in con.execute("SELECT * FROM video_view_state"):
        if r["social_id"] in vids:
            state_rows.append(dict(r))
    n_state = _post("video_view_state", state_rows, "social_id") if state_rows else 0

    # ② 광고 현재값
    ad_rows = [{"id": r["id"], "yt_views": r["yt_views"], "yt_likes": r["yt_likes"],
                "yt_comments": r["yt_comments"]}
               for r in con.execute("SELECT id, yt_views, yt_likes, yt_comments FROM ad_library_ads "
                                    "WHERE yt_views > 0") if r["id"] in have]
    n_ads = _post("ad_library_ads", ad_rows, "id") if ad_rows else 0

    # ②-1 last_seen_at 동기화 — **60일 retention 의 판단 근거**.
    #     로컬 크롤이 "오늘도 살아있다"고 갱신한 값을 Supabase 에 반영하지 않으면,
    #     실제로는 게재 중인 광고가 Supabase 에서 오래된 것으로 보여 60일 뒤 지워진다.
    seen_rows = [{"id": r["id"], "last_seen_at": r["last_seen_at"]}
                 for r in con.execute("SELECT id, last_seen_at FROM ad_library_ads "
                                      "WHERE last_seen_at >= date('now','-3 day')")
                 if r["id"] in have]
    n_seen = _post("ad_library_ads", seen_rows, "id") if seen_rows else 0

    # ③ 오늘 저장된 스냅샷
    snap_rows = [{"ad_id": r["ad_id"], "snapshot_date": r["snapshot_date"], "views": r["views"],
                  "likes": r["likes"], "comments": r["comments"], "created_at": r["created_at"],
                  "view_snapshot_source": "live"}
                 for r in con.execute("SELECT * FROM ad_view_snapshots "
                                      "WHERE snapshot_date = date('now','localtime')")
                 if r["ad_id"] in have]
    n_snap = _post("ad_view_snapshots", snap_rows, "ad_id,snapshot_date") if snap_rows else 0
    con.close()

    if verbose:
        print(f"Supabase 동기화 — 최신조회수 {n_state} · 광고현재값 {n_ads} · last_seen {n_seen} · "
              f"오늘스냅샷 {n_snap} (대상 광고 {len(have):,}건 기준)")
    return {"view_state": n_state, "ads": n_ads, "last_seen": n_seen, "snapshots": n_snap}


if __name__ == "__main__":
    sync()
