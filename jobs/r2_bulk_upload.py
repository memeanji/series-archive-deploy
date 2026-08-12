# -*- coding: utf-8 -*-
"""초기 5.7GB 썸네일 R2 일괄 업로드 — 멱등·재개 가능(설계/구현, 승인 후 실행).

⚠️ 기본은 DRY-RUN(집계만). 실제 업로드는 --apply.
  · DB가 참조하는 로컬 썸네일만 대상(고아 파일 제외).
  · upload_many가 exists() 체크로 이미 올라간 건 skip → 중단돼도 재실행 시 이어서 진행.
  · 배치 단위로 진행률/실패 로그. 검증: 로컬 대상수 vs R2 업로드수, 표본 HTTP 200 확인.

사용:
  python jobs/r2_bulk_upload.py                 # DRY-RUN(대상 집계만)
  python jobs/r2_bulk_upload.py --apply         # 실제 업로드(멱등, 재개 가능)
  python jobs/r2_bulk_upload.py --apply --batch 500 --workers 12
  python jobs/r2_bulk_upload.py --verify        # 표본 HTTP 검증만
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402
import database  # noqa: E402
import services.thumbnail_store as ts  # noqa: E402


def _all_referenced_thumbs() -> list[Path]:
    """DB 참조 로컬 썸네일 전체(중복 제거, 존재하는 파일만). 고아 파일 자동 제외."""
    con = sqlite3.connect(str(database.DB_PATH))
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT local_thumbnail_path, thumbnail_url, preview_url FROM ad_library_ads"
    ).fetchall()
    con.close()
    seen: set[str] = set()
    out: list[Path] = []
    for r in rows:
        for k in ("local_thumbnail_path", "thumbnail_url", "preview_url"):
            v = (r[k] or "").strip()
            if not v or v.startswith(("http", "data:")):
                continue
            rel = v[4:] if v.startswith("app/") else v
            if rel in seen:
                continue
            seen.add(rel)
            p = database.ROOT / rel
            if p.exists():
                out.append(p)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--verify", action="store_true")
    ap.add_argument("--batch", type=int, default=500)
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()

    if not ts.is_enabled():
        raise SystemExit("❌ R2 비활성(USE_R2!=true 또는 설정 누락)")

    files = _all_referenced_thumbs()
    total_mb = sum(p.stat().st_size for p in files) / 1024 / 1024
    print(f"대상 썸네일: {len(files):,}개 · {total_mb:,.0f}MB · bucket={config.secret('R2_BUCKET')}")

    if args.verify:
        import requests
        import random  # noqa: E402  (표본 검증용, 결과에 영향 없음)
        sample = files[:: max(1, len(files) // 20)][:20]
        ok = 0
        for p in sample:
            url = ts.public_url(p.name)
            try:
                s = requests.get(url, timeout=15).status_code
                good = s == 200
                ok += good
                print(f"  [{s}] {url} {'✅' if good else '❌'}")
            except Exception as e:  # noqa: BLE001
                print(f"  ❌ {url} {e}")
        print(f"표본 검증: {ok}/{len(sample)} OK")
        return

    if not args.apply:
        print("DRY-RUN: --apply 없으면 업로드 안 함. 위 대상 규모만 표시.")
        return

    agg = {"uploaded": 0, "skipped": 0, "failed": 0}
    for i in range(0, len(files), args.batch):
        batch = files[i:i + args.batch]
        r = ts.upload_many(batch, overwrite=False, workers=args.workers)
        for k in agg:
            agg[k] += r[k]
        done = min(i + args.batch, len(files))
        print(f"  {done:,}/{len(files):,}  누적 업로드 {agg['uploaded']:,}·skip {agg['skipped']:,}·실패 {agg['failed']:,}")
        if r["errors"]:
            print(f"    최근 오류: {r['errors'][:3]}")
    print(f"\n완료: 업로드 {agg['uploaded']:,}·skip {agg['skipped']:,}·실패 {agg['failed']:,} / 대상 {len(files):,}")
    print("검증 권장: python jobs/r2_bulk_upload.py --verify")


if __name__ == "__main__":
    main()
