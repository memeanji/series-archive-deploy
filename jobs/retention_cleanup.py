# -*- coding: utf-8 -*-
"""보관정책 정리 — 주 1회 실행. 기본 DRY-RUN(집계만, 삭제 없음).

설계(확정, 2026-07-21 개정):
  · 광고 만료 기준 = last_seen_at < (오늘 - N일, 기본 40)  AND  아래 보존조건에 모두 해당 안 됨
      보존: is_bookmarked / memo / script_text / tags / is_preserved
  · 만료 광고는 아래를 함께 삭제:
      - DB 본문(ad_library_ads) + 자식(ad_view_snapshots, ad_social_matches)
      - 해당 광고의 로컬 썸네일 파일(static/thumbnails/..)  ※ 생존 광고가 쓰는 파일은 제외
      - git 추적 중인 그 썸네일(스테이징 삭제 → 다음 커밋에 반영)
  · 삭제 순서(참조 무결성): 자식(snapshots, matches) → 부모(ad_library_ads)
  · 스냅샷 압축: 60일 이내 일별 전량 / 61~180일 (ad,ISO주)당 1건 / 180일↑ (ad,연월)당 1건
  · 정리 후 VACUUM → regenerate_demo_db()로 정리분이 빠진 clean demo.db 재생성
  · first_seen_at 은 분석용 표시값이며 삭제 기준에 쓰지 않는다(삭제 기준은 last_seen_at).

실행:
  · (dry-run, 기본)  python jobs/retention_cleanup.py            → 삭제 건수/썸네일 수/예상 절감만 출력
  · (실제 적용)      python jobs/retention_cleanup.py --apply     → 삭제+VACUUM+demo.db 재생성
  · (적용+푸시)      python jobs/retention_cleanup.py --apply --push
  · (기간 변경)      --days 40   (기본 40)
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import database  # noqa: E402

DEFAULT_DAYS = 40

# 만료 대상 광고 WHERE (보존 예외 제외). :dcut 바인딩 필요.
EXPIRE_WHERE = """
      last_seen_at IS NOT NULL AND last_seen_at != '' AND last_seen_at < :dcut
  AND COALESCE(is_bookmarked, 0) = 0
  AND COALESCE(TRIM(memo), '') = ''
  AND COALESCE(TRIM(script_text), '') = ''
  AND COALESCE(TRIM(tags), '[]') IN ('', '[]')
  AND COALESCE(is_preserved, 0) = 0
""".strip()


def _cutoffs(today: dt.date, days: int) -> dict:
    return {
        "dcut": (today - dt.timedelta(days=days)).isoformat(),   # 광고 만료 기준(기본 40일)
        "d60": (today - dt.timedelta(days=60)).isoformat(),      # 스냅샷 압축 경계
        "d180": (today - dt.timedelta(days=180)).isoformat(),
    }


def _rel_local(v: str) -> str:
    """썸네일 컬럼값이 '로컬 static 파일'이면 프로젝트루트 기준 상대경로 반환, 아니면 ''.
    http/data URI/빈값은 파일 아님 → ''. 'app/static/..' 접두사는 'static/..' 로 정규화."""
    v = (v or "").strip()
    if not v or v.startswith(("http://", "https://", "data:")):
        return ""
    rel = v[4:] if v.startswith("app/") else v   # 'app/static/..' → 'static/..'
    return rel.replace("\\", "/")


def _local_files(row) -> set[str]:
    """한 광고 행에서 참조하는 로컬 썸네일 파일 상대경로 집합."""
    out = set()
    for key in ("local_thumbnail_path", "thumbnail_url", "preview_url"):
        rel = _rel_local(row[key] if isinstance(row, sqlite3.Row) else row.get(key))
        if rel:
            out.add(rel)
    return out


def _collect_thumb_files(conn, P: dict) -> set[str]:
    """만료 광고가 쓰는 로컬 썸네일 중, 생존 광고가 안 쓰는 파일만(=안전 삭제 대상)."""
    exp = conn.execute(
        f"SELECT local_thumbnail_path, thumbnail_url, preview_url FROM ad_library_ads WHERE {EXPIRE_WHERE}", P
    ).fetchall()
    surv = conn.execute(
        f"SELECT local_thumbnail_path, thumbnail_url, preview_url FROM ad_library_ads WHERE NOT ({EXPIRE_WHERE})", P
    ).fetchall()
    del_files: set[str] = set()
    for r in exp:
        del_files |= _local_files(r)
    keep_files: set[str] = set()
    for r in surv:
        keep_files |= _local_files(r)
    return del_files - keep_files   # 생존 광고가 참조하는 파일은 보존


def _files_size_mb(rel_paths) -> tuple[int, float]:
    """상대경로 집합 중 실제 존재 파일 수 / 총 MB."""
    n = 0
    total = 0
    for rel in rel_paths:
        p = database.ROOT / rel
        if p.exists():
            n += 1
            total += p.stat().st_size
    return n, total / 1024 / 1024


def _compaction_counts(conn, where: str, fmt: str) -> tuple[int, int]:
    """구간 내 스냅샷 총수 / 압축 후 유지수(=(ad,버킷) 조합 수)."""
    total = conn.execute(f"SELECT COUNT(*) FROM ad_view_snapshots WHERE {where}").fetchone()[0]
    keep = conn.execute(
        f"SELECT COUNT(*) FROM (SELECT ad_id, strftime('{fmt}', snapshot_date) b "
        f"FROM ad_view_snapshots WHERE {where} GROUP BY ad_id, b)"
    ).fetchone()[0]
    return total, keep


def dry_run(days: int, measure: bool = False) -> dict:
    conn = database.get_conn()
    today = dt.date.today()
    P = _cutoffs(today, days)
    dcut, d60, d180 = P["dcut"], P["d60"], P["d180"]

    # ── 만료 광고 + 보존 예외 ──
    n_expire = conn.execute(f"SELECT COUNT(*) FROM ad_library_ads WHERE {EXPIRE_WHERE}", P).fetchone()[0]
    n_keep = conn.execute(
        f"SELECT COUNT(*) FROM ad_library_ads WHERE last_seen_at < :dcut AND NOT ({EXPIRE_WHERE})", P
    ).fetchone()[0]
    snap_child = conn.execute(
        f"SELECT COUNT(*) FROM ad_view_snapshots WHERE ad_id IN (SELECT id FROM ad_library_ads WHERE {EXPIRE_WHERE})", P
    ).fetchone()[0]
    match_child = conn.execute(
        f"SELECT COUNT(*) FROM ad_social_matches WHERE ad_id IN (SELECT id FROM ad_library_ads WHERE {EXPIRE_WHERE})", P
    ).fetchone()[0]
    total_ads = conn.execute("SELECT COUNT(*) FROM ad_library_ads").fetchone()[0]

    # ── 삭제될 썸네일 파일 ──
    del_files = _collect_thumb_files(conn, P)
    thumb_n, thumb_mb = _files_size_mb(del_files)

    # ── 스냅샷 압축(만료광고 삭제분과 별개, 남는 스냅샷 대상) ──
    mid_where = f"snapshot_date >= '{d180}' AND snapshot_date < '{d60}'"
    old_where = f"snapshot_date < '{d180}'"
    mid_tot, mid_keep = _compaction_counts(conn, mid_where, "%Y-%W")
    old_tot, old_keep = _compaction_counts(conn, old_where, "%Y-%m")
    conn.close()

    print("=" * 64)
    print(f"[RETENTION DRY-RUN] 기준일 {today}  (만료 {days}일<{dcut})")
    print("-" * 64)
    print(f"  전체 광고            : {total_ads:>8,}")
    print(f"  만료 광고(삭제)      : {n_expire:>8,}  (동반 스냅샷 {snap_child:,} · 매칭 {match_child:,})")
    print(f"  보존 예외(제외)      : {n_keep:>8,}  (북마크/메모/스크립트/태그/보존토글)")
    print(f"  삭제 썸네일 파일     : {thumb_n:>8,}  ({thumb_mb:.1f}MB, 생존광고 공유분 제외)")
    print(f"  스냅샷 압축 61~180일 : {mid_tot:>8,} → 주1 유지 {mid_keep:,} (삭제 {mid_tot - mid_keep:,})")
    print(f"  스냅샷 압축 180일↑   : {old_tot:>8,} → 월1 유지 {old_keep:,} (삭제 {old_tot - old_keep:,})")

    if measure:
        _measure_savings(P)
    print("-" * 64)
    print("  DRY-RUN: 아무것도 삭제하지 않았습니다. 실제 삭제는 --apply.")
    print("=" * 64)
    return {"expire": n_expire, "keep": n_keep, "thumb_files": thumb_n,
            "thumb_mb": thumb_mb, "snap_child": snap_child, "total": total_ads}


def _measure_savings(P: dict) -> None:
    """임시 사본에 실제 삭제+VACUUM → 파일크기 절감만 측정(원본 불변)."""
    src = str(database.DB_PATH)
    d60, d180 = P["d60"], P["d180"]
    tmp = os.path.join(tempfile.gettempdir(), "retention_measure.db")
    shutil.copy(src, tmp)
    before = os.path.getsize(tmp)
    m = sqlite3.connect(tmp)
    m.execute(f"DELETE FROM ad_view_snapshots WHERE ad_id IN (SELECT id FROM ad_library_ads WHERE {EXPIRE_WHERE})", P)
    m.execute(f"DELETE FROM ad_social_matches WHERE ad_id IN (SELECT id FROM ad_library_ads WHERE {EXPIRE_WHERE})", P)
    m.execute(f"DELETE FROM ad_library_ads WHERE {EXPIRE_WHERE}", P)
    for where, fmt in ((f"snapshot_date >= '{d180}' AND snapshot_date < '{d60}'", "%Y-%W"),
                       (f"snapshot_date < '{d180}'", "%Y-%m")):
        m.execute(
            f"DELETE FROM ad_view_snapshots WHERE {where} AND rowid NOT IN ("
            f"  SELECT rowid FROM (SELECT rowid, ROW_NUMBER() OVER ("
            f"    PARTITION BY ad_id, strftime('{fmt}', snapshot_date) ORDER BY snapshot_date DESC) rn "
            f"  FROM ad_view_snapshots WHERE {where}) WHERE rn = 1)"
        )
    m.commit()
    m.execute("VACUUM")
    m.close()
    after = os.path.getsize(tmp)
    os.remove(tmp)
    print(f"  예상 DB 절감(사본 VACUUM 측정): {before/1024/1024:.1f}MB → {after/1024/1024:.1f}MB "
          f"(절감 {(before-after)/1024/1024:.1f}MB)")


def _git(args: list[str]) -> tuple[int, str]:
    r = subprocess.run(["git", *args], cwd=str(database.ROOT),
                       capture_output=True, text=True, timeout=600)
    return r.returncode, (r.stderr or r.stdout or "").strip()


def apply(days: int, push: bool = False) -> dict:
    """실제 정리: 썸네일 파일 수집 → 자식/부모 행 삭제 → 스냅샷 압축 → VACUUM
       → 썸네일 파일 삭제 → demo.db 재생성 → (push 시) git add -A/commit/push."""
    today = dt.date.today()
    P = _cutoffs(today, days)
    d60, d180 = P["d60"], P["d180"]

    conn = database.get_conn()
    # 1) 삭제 대상 썸네일 파일 목록(행 삭제 전에 수집 — 삭제 후엔 조회 불가)
    del_files = _collect_thumb_files(conn, P)
    n_expire = conn.execute(f"SELECT COUNT(*) FROM ad_library_ads WHERE {EXPIRE_WHERE}", P).fetchone()[0]

    # 2) 자식 → 부모 순 삭제(참조 무결성)
    conn.execute(
        f"DELETE FROM ad_view_snapshots WHERE ad_id IN (SELECT id FROM ad_library_ads WHERE {EXPIRE_WHERE})", P)
    conn.execute(
        f"DELETE FROM ad_social_matches WHERE ad_id IN (SELECT id FROM ad_library_ads WHERE {EXPIRE_WHERE})", P)
    deleted = conn.execute(f"DELETE FROM ad_library_ads WHERE {EXPIRE_WHERE}", P).rowcount

    # 3) 스냅샷 압축(생존 광고의 오래된 스냅샷)
    for where, fmt in ((f"snapshot_date >= '{d180}' AND snapshot_date < '{d60}'", "%Y-%W"),
                       (f"snapshot_date < '{d180}'", "%Y-%m")):
        conn.execute(
            f"DELETE FROM ad_view_snapshots WHERE {where} AND rowid NOT IN ("
            f"  SELECT rowid FROM (SELECT rowid, ROW_NUMBER() OVER ("
            f"    PARTITION BY ad_id, strftime('{fmt}', snapshot_date) ORDER BY snapshot_date DESC) rn "
            f"  FROM ad_view_snapshots WHERE {where}) WHERE rn = 1)"
        )
    conn.commit()
    conn.close()

    # 4) VACUUM(트랜잭션 밖, 독립 커넥션)
    raw = sqlite3.connect(str(database.DB_PATH))
    raw.isolation_level = None
    raw.execute("VACUUM")
    raw.close()

    # 5) 썸네일 파일 삭제(디스크)
    removed = 0
    for rel in del_files:
        p = database.ROOT / rel
        try:
            if p.exists():
                p.unlink()
                removed += 1
        except OSError:
            pass

    # 6) clean demo.db 재생성(정리분 자동 제외 + users 제거 + VACUUM)
    database.regenerate_demo_db()

    print(f"[RETENTION APPLY] 광고 {deleted:,}건 삭제 · 썸네일 {removed:,}개 삭제 · VACUUM · demo.db 재생성 완료")

    # 6-1) Supabase 쪽도 같은 정책으로 정리(60일). 장기보존(is_preserved=1) 브랜드는 대상에서 빠진다.
    #      로컬만 지우면 Supabase에 옛 데이터가 남아 앱(=Supabase 읽기)에서 계속 보인다.
    try:
        import jobs.supabase_retention as SR
        SR.run(apply=True)
    except Exception as e:  # noqa: BLE001  (Supabase 실패가 로컬 정리를 되돌리지 않게)
        print(f"  [Supabase retention] 건너뜀: {type(e).__name__}: {e}")

    # 7) git 반영(삭제 스테이징 포함)
    if push:
        msg = f"retention: {days}일+ 미수집 광고 {deleted}건 정리(썸네일 {removed} 삭제) {today}"
        for args in (["add", "-A", "static/thumbnails"],
                     ["add", "sample_data/demo.db"],
                     ["commit", "-m", msg],
                     ["push", "origin", "main"]):
            rc, out = _git(args)
            if rc != 0 and args[0] != "commit":
                print(f"  git {args[0]} 경고: {out[:160]}")
        print("  git push 완료(또는 시도).")

    return {"deleted": deleted, "thumbs_removed": removed}


def main() -> None:
    ap = argparse.ArgumentParser(description="보관정책 정리(기본 dry-run)")
    ap.add_argument("--days", type=int, default=DEFAULT_DAYS, help=f"만료 기준 일수(기본 {DEFAULT_DAYS})")
    ap.add_argument("--apply", action="store_true", help="실제 삭제/압축 실행")
    ap.add_argument("--push", action="store_true", help="적용 후 demo.db·썸네일 삭제를 git commit/push")
    ap.add_argument("--measure", action="store_true", help="dry-run 시 임시 사본으로 예상 DB 절감량 측정")
    args = ap.parse_args()
    if args.apply:
        apply(days=args.days, push=args.push)
    else:
        dry_run(days=args.days, measure=args.measure)


if __name__ == "__main__":
    main()
