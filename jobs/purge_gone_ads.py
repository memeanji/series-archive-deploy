"""메타에서 사라진(private_or_deleted/not_found) 영상 광고를 삭제.
   - 북마크(is_bookmarked=1)·메모 있는 광고는 보존(사용자 큐레이션).
   - 해당 광고의 영구 로컬 썸네일(m_*.jpg)도 함께 정리(레포 용량 절감).
   사용:  python jobs/purge_gone_ads.py            (dry-run, 개수만)
          python jobs/purge_gone_ads.py --apply    (실제 삭제 + demo.db + push)
"""
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import database  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
GONE = ("private_or_deleted", "not_found")


def _targets(conn):
    ph = ",".join("?" * len(GONE))
    return conn.execute(
        f"SELECT id, brand_name, local_thumbnail_path, thumbnail_url "
        f"FROM ad_library_ads "
        f"WHERE platform='meta' AND media_type='video' AND video_status IN ({ph}) "
        f"AND COALESCE(is_bookmarked,0)=0 AND COALESCE(memo,'')=''", GONE).fetchall()


def main(apply: bool) -> None:
    database.init_db()
    conn = database.get_conn()
    rows = _targets(conn)
    # 보존(북마크/메모) 개수
    ph = ",".join("?" * len(GONE))
    kept = conn.execute(
        f"SELECT COUNT(*) FROM ad_library_ads WHERE platform='meta' AND media_type='video' "
        f"AND video_status IN ({ph}) AND (COALESCE(is_bookmarked,0)=1 OR COALESCE(memo,'')<>'')",
        GONE).fetchone()[0]
    print(f"삭제 대상(사라진 영상): {len(rows)}개 · 보존(북마크/메모): {kept}개", flush=True)
    by_brand: dict = {}
    for r in rows:
        by_brand[r["brand_name"]] = by_brand.get(r["brand_name"], 0) + 1
    for b, c in sorted(by_brand.items(), key=lambda x: -x[1])[:15]:
        print(f"  {b}: {c}", flush=True)

    if not apply:
        print("\n[dry-run] 실제 삭제하려면 --apply", flush=True)
        conn.close()
        return

    # 1) 고아 썸네일 파일 수집(삭제 전)
    thumbs = []
    for r in rows:
        for v in (r["local_thumbnail_path"], r["thumbnail_url"]):
            v = (v or "").strip()
            if v.startswith("app/static/") and v.endswith(".jpg"):
                thumbs.append(v[4:])  # static/...
    ids = [r["id"] for r in rows]
    # 2) 행 삭제(매칭/스냅샷 등 연관도 정리)
    CH = 500
    for i in range(0, len(ids), CH):
        chunk = ids[i:i + CH]
        q = ",".join("?" * len(chunk))
        conn.execute(f"DELETE FROM ad_library_ads WHERE id IN ({q})", chunk)
    conn.commit()
    conn.close()
    print(f"행 {len(ids)}개 삭제 완료", flush=True)

    # 3) 고아 썸네일 git rm(working tree+index) / 미추적은 unlink
    removed = 0
    for rel in set(thumbs):
        fp = ROOT / rel
        if not fp.exists():
            continue
        r = subprocess.run(["git", "rm", "-q", "--", rel], cwd=str(ROOT),
                           capture_output=True, text=True)
        if r.returncode != 0:
            try:
                fp.unlink()
            except Exception:  # noqa: BLE001
                pass
        removed += 1
    print(f"썸네일 {removed}개 정리", flush=True)

    # 4) 재집계 + demo.db + push
    database.compute_matches()
    database.regrade()
    database.regenerate_demo_db()
    print("demo.db 갱신 완료", flush=True)
    msg = f"사라진 메타 영상 광고 {len(ids)}개 삭제 + 고아 썸네일 정리 {datetime.now():%Y-%m-%d %H:%M}"
    for cmd in (["git", "add", "-A", "sample_data/demo.db", "static/thumbnails"],
                ["git", "commit", "-m", msg],
                ["git", "push", "origin", "main"]):
        rr = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, timeout=300)
        if rr.returncode != 0 and cmd[1] not in ("commit",):
            print(f"git {cmd[1]}: {rr.stderr[:150]}", flush=True)
    print("=== 완료(클라우드 푸시) ===", flush=True)


if __name__ == "__main__":
    main(apply="--apply" in sys.argv)
