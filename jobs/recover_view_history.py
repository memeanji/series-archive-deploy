# -*- coding: utf-8 -*-
"""조회수 추이 복원 — **git 히스토리의 demo.db에 박힌 그날의 조회수**에서 과거 스냅샷을 되살린다.

배경: 스크린샷(static/thumbnails)은 광고 소재/카드 캡처라 조회수가 찍혀 있지 않다(41,543개 확인).
      대신 매일 커밋된 `sample_data/demo.db` 각 버전의 `ad_library_ads.yt_views` 는
      **그 커밋 날짜 시점의 조회수**다. 스냅샷 행(ad_view_snapshots)이 없던 날짜·영상도 여기엔 남아 있다.

규칙(사용자 지정)
  · 동일 영상은 **social_id(=YouTube video_id)** 기준으로 연결
  · 기존 스냅샷과 **같은 (ad_id, 날짜)면 저장하지 않음**
  · 날짜별 조회수가 비정상(감소·0·과도한 급증)인 값은 **제외**
  · 최신 조회수(video_view_state) 구조는 건드리지 않음 — 과거 추이 복원 전용
  · 복원분은 `view_snapshot_source='git_yt_views'` 로 표시해 원본과 구분

사용:
  python jobs/recover_view_history.py                 # DRY-RUN(집계만)
  python jobs/recover_view_history.py --apply         # 로컬 SQLite 반영
  python jobs/recover_view_history.py --apply --push  # + Supabase 업로드(이전된 광고분만)
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402
import database  # noqa: E402
import services.youtube as YT  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
LIVE = ROOT / "data" / "series_archive.db"
TMP = ROOT / "data" / "_hist_views_tmp.db"
OUT = ROOT / "data" / "view_history_recovery.json"

# 신뢰도 검증 파라미터
MAX_DAILY_GROWTH = 3.0      # 하루 사이 조회수가 3배 넘게 뛰면 이상치로 간주(값 오염/영상 교체 의심)
MIN_VIEWS = 1


def _commits() -> list[tuple[str, str]]:
    out = subprocess.run(["git", "log", "--pretty=%h %ad", "--date=short", "--",
                          "sample_data/demo.db"], cwd=ROOT, capture_output=True,
                         text=True, encoding="utf-8", errors="ignore").stdout
    seen, rows = set(), []
    for line in out.splitlines():
        p = line.split()
        if len(p) == 2 and p[1] not in seen:
            seen.add(p[1])
            rows.append((p[1], p[0]))
    return sorted(rows)


def scan_history() -> dict:
    """{video_id: {date: (views, likes, comments)}} — 각 demo.db 버전의 그날 조회수."""
    per: dict = defaultdict(dict)
    commits = _commits()
    print(f"demo.db {len(commits)}개 버전 스캔", flush=True)
    for i, (d, h) in enumerate(commits, 1):
        try:
            with open(TMP, "wb") as f:
                subprocess.run(["git", "show", f"{h}:sample_data/demo.db"], cwd=ROOT,
                               stdout=f, check=True)
            con = sqlite3.connect(str(TMP))
            rows = con.execute(
                "SELECT video_url, yt_views, yt_likes, yt_comments FROM ad_library_ads "
                "WHERE yt_views > 0 AND video_url LIKE '%youtube%'").fetchall()
            con.close()
        except Exception as e:  # noqa: BLE001
            print(f"  [{i}/{len(commits)}] {d} 실패: {e}", flush=True)
            continue
        n = 0
        for url, v, l, c in rows:
            vid = YT.extract_video_id(url or "")
            if not vid or not v:
                continue
            prev = per[vid].get(d)
            if prev is None or v > prev[0]:      # 같은 영상을 여러 광고가 쓰면 최대값
                per[vid][d] = (int(v), int(l or 0), int(c or 0))
                n += 1
        if i % 10 == 0 or i == len(commits):
            print(f"  [{i}/{len(commits)}] {d}: 영상 누적 {len(per):,}", flush=True)
    TMP.unlink(missing_ok=True)
    return per


def main() -> None:
    apply = "--apply" in sys.argv
    push = "--push" in sys.argv

    live = sqlite3.connect(str(LIVE))
    live.row_factory = sqlite3.Row
    # 영상 → 현재 광고 ID 목록 (스냅샷은 ad_id 단위로 저장되므로 매핑 필요)
    vid2ads: dict = defaultdict(list)
    for r in live.execute("SELECT id, video_url FROM ad_library_ads WHERE video_url LIKE '%youtube%'"):
        vid = YT.extract_video_id(r["video_url"] or "")
        if vid:
            vid2ads[vid].append(r["id"])
    # 기존 스냅샷: 중복 방지 + 검증 기준선
    existing = defaultdict(dict)          # ad_id -> {date: views}
    for r in live.execute("SELECT ad_id, snapshot_date, views FROM ad_view_snapshots"):
        existing[r["ad_id"]][r["snapshot_date"]] = int(r["views"] or 0)
    live.close()
    print(f"현재: 영상 {len(vid2ads):,}개 · 스냅샷 보유 광고 {len(existing):,}개")

    hist = scan_history()
    print(f"\n히스토리에서 조회수를 읽은 영상 {len(hist):,}개")

    stats = {"videos_seen": len(hist), "videos_used": 0, "candidates": 0, "dup_skipped": 0,
             "no_ad_match": 0, "rejected_decrease": 0, "rejected_spike": 0, "inserted": 0}
    to_insert = []                        # (ad_id, date, views, likes, comments)
    for vid, by_date in hist.items():
        ads = vid2ads.get(vid)
        if not ads:
            stats["no_ad_match"] += len(by_date)
            continue
        # 기존 스냅샷과 후보를 날짜순으로 합쳐 단조증가 검증(조회수는 줄어들 수 없다)
        base = {}
        for aid in ads:
            for dt_, v in existing.get(aid, {}).items():
                base[dt_] = max(base.get(dt_, 0), v)
        merged = dict(base)
        used_video = False
        for dt_ in sorted(by_date):
            views, likes, comments = by_date[dt_]
            if views < MIN_VIEWS:
                continue
            if dt_ in base:                       # 같은 날짜는 기존 데이터 우선(중복 저장 안 함)
                stats["dup_skipped"] += 1
                continue
            prev_dates = [d for d in sorted(merged) if d < dt_]
            next_dates = [d for d in sorted(merged) if d > dt_]
            prev_v = merged[prev_dates[-1]] if prev_dates else None
            next_v = merged[next_dates[0]] if next_dates else None
            # ① 과거보다 줄거나 미래보다 크면 이상치
            if (prev_v is not None and views < prev_v) or (next_v is not None and views > next_v):
                stats["rejected_decrease"] += 1
                continue
            # ② 하루 사이 3배 넘게 뛰는 값은 신뢰하지 않음
            if prev_v and prev_dates:
                from datetime import date
                d0 = date.fromisoformat(prev_dates[-1])
                d1 = date.fromisoformat(dt_)
                days = max(1, (d1 - d0).days)
                if views > prev_v * (MAX_DAILY_GROWTH ** days):
                    stats["rejected_spike"] += 1
                    continue
            merged[dt_] = views
            used_video = True
            for aid in ads:
                if dt_ in existing.get(aid, {}):
                    continue
                to_insert.append((aid, dt_, views, likes, comments))
            stats["candidates"] += 1
        if used_video:
            stats["videos_used"] += 1

    print(f"\n복원 후보: 영상 {stats['videos_used']:,}개 · 날짜포인트 {stats['candidates']:,}개 "
          f"→ 스냅샷 행 {len(to_insert):,}건")
    print(f"  제외: 기존과 같은 날짜 {stats['dup_skipped']:,} · 현재 광고 없음 {stats['no_ad_match']:,} · "
          f"감소/역전 {stats['rejected_decrease']:,} · 급증 {stats['rejected_spike']:,}")

    if apply and to_insert:
        conn = database.get_conn()
        try:
            conn.execute("ALTER TABLE ad_view_snapshots ADD COLUMN view_snapshot_source TEXT DEFAULT 'live'")
        except Exception:  # noqa: BLE001  (이미 있으면 통과)
            pass
        conn.executemany(
            "INSERT OR IGNORE INTO ad_view_snapshots"
            "(ad_id,snapshot_date,views,likes,comments,created_at,view_snapshot_source) "
            "VALUES(?,?,?,?,?,?,'git_yt_views')",
            [(a, d, v, l, c, database._now()) for a, d, v, l, c in to_insert])
        conn.commit()
        n = conn.execute("SELECT COUNT(*) FROM ad_view_snapshots "
                         "WHERE view_snapshot_source='git_yt_views'").fetchone()[0]
        conn.close()
        stats["inserted"] = n
        print(f"\n로컬 SQLite 반영 완료 — 복원 스냅샷 총 {n:,}건")

    if apply and push:
        import requests
        base = (config.secret("SUPABASE_URL") or "").rstrip("/")
        key = config.secret("SUPABASE_SERVICE_KEY") or config.secret("SUPABASE_KEY")
        h = {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json",
             "Prefer": "resolution=merge-duplicates,return=minimal"}
        # Supabase 에 있는 광고만(=이전된 브랜드).
        #  ⚠️PostgREST 기본 응답은 1,000행에서 잘린다 → Range 헤더로 전량 페이지네이션(안 하면 0건 매칭).
        have, start = set(), 0
        while True:
            rr = requests.get(f"{base}/rest/v1/ad_library_ads?select=id",
                              headers={**h, "Range": f"{start}-{start + 999}"}, timeout=120)
            if rr.status_code not in (200, 206):
                print(f"  광고 ID 조회 실패 {rr.status_code}: {rr.text[:120]}")
                break
            page = rr.json()
            have |= {x["id"] for x in page}
            if len(page) < 1000:
                break
            start += 1000
        print(f"  Supabase 광고 {len(have):,}건 확인")
        rows = [{"ad_id": a, "snapshot_date": d, "views": v, "likes": l, "comments": c,
                 "created_at": database._now(), "view_snapshot_source": "git_yt_views"}
                for a, d, v, l, c in to_insert if a in have]
        sent = 0
        for i in range(0, len(rows), 500):
            rr = requests.post(f"{base}/rest/v1/ad_view_snapshots?on_conflict=ad_id,snapshot_date",
                               headers=h, data=json.dumps(rows[i:i + 500]).encode(), timeout=180)
            if rr.status_code in (200, 201, 204):
                sent += len(rows[i:i + 500])
            else:
                print(f"  업로드 실패 {rr.status_code}: {rr.text[:160]}")
        stats["pushed"] = sent
        print(f"Supabase 업로드 {sent:,}건(이전 브랜드 광고분)")

    OUT.write_text(json.dumps(stats, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n저장: {OUT}")


if __name__ == "__main__":
    main()
