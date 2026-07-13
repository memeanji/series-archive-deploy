"""전 브랜드 메타 광고만 재크롤 → 만료된 fbcdn 영상/썸네일 서명 URL 일괄 갱신.
   (구글/유튜브는 건드리지 않음 — '메타만' 복구용)
   완료 후 demo.db 갱신 + git push 로 클라우드 반영.
"""
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import database  # noqa: E402
from collectors import meta_library_crawler  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def _log(m: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {m}", flush=True)


def main(exclude: list = None) -> None:
    exclude = set(exclude or [])
    database.init_db()
    run_start = datetime.now(timezone.utc).isoformat()
    conn = database.get_conn()
    brands = [r[0] for r in conn.execute(
        "SELECT display_name FROM brands WHERE COALESCE(is_active,1)=1 ORDER BY display_name").fetchall()]
    conn.close()
    if exclude:
        brands = [b for b in brands if b not in exclude]
        _log(f"제외 {len(exclude)}개: {sorted(exclude)}")
    try:
        priority = database.expired_video_brands()
        if priority:
            pset = set(priority)
            brands = [b for b in priority if b not in exclude] + \
                     [b for b in brands if b not in pset]
            _log(f"우선 갱신(만료 영상 보유) 먼저")
    except Exception as e:  # noqa: BLE001
        _log(f"우선순위 계산 건너뜀: {e}")
    _log(f"=== 메타 전체 재크롤 시작: {len(brands)}개 브랜드 ===")
    grand = 0
    for i, b in enumerate(brands, 1):
        kws = database.get_brand_keywords(b) if database.brand_exists(b) else [b]
        saved_b = 0
        for kw in kws:
            try:
                ads = [{**a, "brand_name": b}
                       for a in meta_library_crawler.search_brand(kw, scrolls=10, retries=2)]
                saved = database.ingest_ad_library(ads)
                saved_b += saved
            except Exception as e:  # noqa: BLE001
                _log(f"  [{b}] '{kw}' 실패: {str(e)[:80]}")
        grand += saved_b
        _log(f"[{i}/{len(brands)}] {b}: 갱신 {saved_b}")
    database.compute_matches()
    database.regrade()
    database.migrate_brands()
    try:
        # 이번에 크롤한 브랜드만 상태확정(제외 브랜드의 정상 영상을 오판하지 않도록)
        vs = database.finalize_meta_video_status(run_start, brands=brands)
        _log(f"영상 상태(크롤 브랜드): {vs}")
    except Exception as e:  # noqa: BLE001
        _log(f"영상 상태 확정 실패: {e}")
    _log(f"크롤 완료 · 누적 갱신 {grand}")

    # demo.db 갱신(WAL 체크포인트 포함)
    try:
        database.regenerate_demo_db()
        _log("demo.db 갱신 완료")
    except Exception as e:  # noqa: BLE001
        _log(f"demo.db 갱신 실패: {e}")

    # git push
    msg = f"메타 전체 영상 URL 재크롤 갱신 {datetime.now():%Y-%m-%d %H:%M}"
    for cmd in (["git", "add", "sample_data/demo.db"],
                ["git", "add", "static/thumbnails/m_*.jpg"],   # 새 메타 썸네일도 클라우드 반영

                ["git", "commit", "-m", msg],
                ["git", "push", "origin", "main"]):
        try:
            r = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, timeout=180)
            if r.returncode != 0 and cmd[1] != "commit":
                _log(f"git {cmd[1]}: {r.stderr[:120]}")
        except Exception as e:  # noqa: BLE001
            _log(f"git 실패({cmd[1]}): {e}")
    _log("=== 완료(클라우드 푸시) ===")


if __name__ == "__main__":
    # 인자로 받은 브랜드들은 제외(이미 따로 갱신한 브랜드 재처리 방지)
    main(exclude=sys.argv[1:])
