"""지정 브랜드들의 Meta 광고만 우선 재크롤 → video_url 갱신 + 상태확정 + 클라우드 반영.
   사용:  python jobs/recrawl_brands_meta.py 네리티아 세라블랑 테키라
"""
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


def main(brands: list) -> None:
    database.init_db()
    run_start = datetime.now(timezone.utc).isoformat()
    _log(f"=== 우선 재크롤(Meta): {brands} ===")
    for b in brands:
        kws = database.get_brand_keywords(b) if database.brand_exists(b) else [b]
        saved = 0
        for kw in kws:
            try:
                ads = [{**a, "brand_name": b}
                       for a in meta_library_crawler.search_brand(kw, scrolls=12, retries=2)]
                saved += database.ingest_ad_library(ads)
            except Exception as e:  # noqa: BLE001
                _log(f"  [{b}] '{kw}' 실패: {str(e)[:90]}")
        _log(f"  {b}: 갱신 {saved}")
    database.compute_matches()
    database.regrade()
    # 이 브랜드들만 상태확정(다른 브랜드 오판 방지)
    vs = database.finalize_meta_video_status(run_start, brands=brands)
    _log(f"영상 상태(해당 브랜드): {vs}")
    # 클라우드 반영
    try:
        database.regenerate_demo_db()
        _log("demo.db 갱신 완료")
    except Exception as e:  # noqa: BLE001
        _log(f"demo.db 갱신 실패: {e}")
    msg = f"우선 재크롤(Meta) {','.join(brands)} {datetime.now():%Y-%m-%d %H:%M}"
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
    args = sys.argv[1:] or ["네리티아", "세라블랑", "테키라"]
    main(args)
