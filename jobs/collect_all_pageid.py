"""전 브랜드 Meta 재수집(page_id 기반 신구조) — keyword→page_id 자동추출·연쇄.
   브랜드별 적재만 반복하고, 무거운 후처리(매칭/등급/상태확정/demo.db/push)는 끝에 1회.
   사용:  python jobs/collect_all_pageid.py [제외브랜드 ...]
"""
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import database  # noqa: E402
import config  # noqa: E402
from jobs.meta_collect import crawl_brand_meta  # noqa: E402

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
        _log(f"제외 {len(exclude)}개")
    _log(f"=== 전 브랜드 page_id 재수집 시작: {len(brands)}개 ===")
    grand_new = grand = 0
    for i, b in enumerate(brands, 1):
        try:
            cr = crawl_brand_meta(b)
            ads = cr["ads"]
            ids = list(dict.fromkeys(a.get("platform_ad_id") for a in ads if a.get("platform_ad_id")))
            before = database.existing_ad_ids(ids)
            database.ingest_ad_library(ads)
            new = sum(1 for x in ids if x not in before)
            grand_new += new
            grand += len(ids)
            bid = (database.get_brand(b) or {}).get("id") or 0
            database.log_brand_collection(bid, "meta", cr["method"], "success",
                                          len(ids), new, len(ids) - new, started=run_start)
            _log(f"[{i}/{len(brands)}] {b}: {cr['method']} · 발견 {len(ids)} · 신규 {new}")
        except Exception as e:  # noqa: BLE001
            _log(f"[{i}/{len(brands)}] {b}: 실패 {str(e)[:80]}")
    # 후처리 1회
    database.compute_matches()
    database.regrade()
    database.migrate_brands()
    try:
        vs = database.finalize_meta_video_status(run_start, brands=brands)
        _log(f"영상 상태: {vs}")
    except Exception as e:  # noqa: BLE001
        _log(f"상태확정 실패: {e}")
    _log(f"크롤 완료 · 발견 {grand} · 신규 {grand_new}")
    try:
        database.regenerate_demo_db()
        _log("demo.db 갱신 완료")
    except Exception as e:  # noqa: BLE001
        _log(f"demo.db 실패: {e}")
    if config.ENABLE_AUTO_GIT_PUSH:
        msg = f"전 브랜드 page_id 재수집 {datetime.now():%Y-%m-%d %H:%M}"
        for cmd in (["git", "add", "sample_data/demo.db"],
                    ["git", "add", "static/thumbnails/m_*.jpg"],
                    ["git", "commit", "-m", msg],
                    ["git", "push", "origin", "main"]):
            r = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, timeout=300)
            if r.returncode != 0 and cmd[1] != "commit":
                _log(f"git {cmd[1]}: {r.stderr[:120]}")
        _log("=== 완료(클라우드 푸시) ===")
    else:
        _log("=== 완료(로컬 갱신만 · 자동 git push 비활성) ===")


if __name__ == "__main__":
    main(exclude=sys.argv[1:])
