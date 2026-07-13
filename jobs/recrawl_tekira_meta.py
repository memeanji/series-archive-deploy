"""테키라 메타 광고만 재크롤 → fbcdn 서명 URL 갱신(만료된 영상 재생 복구)."""
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import database  # noqa: E402
from collectors import meta_library_crawler  # noqa: E402

DISPLAY = "테키라"


def main() -> None:
    database.init_db()
    keywords = database.get_brand_keywords(DISPLAY) if database.brand_exists(DISPLAY) else [DISPLAY]
    bid_row = database.get_brand(DISPLAY) or {}
    print(f"=== '{DISPLAY}' 메타 재크롤 시작 · keywords={keywords} ===", flush=True)
    total_found = total_saved = 0
    for kw in keywords:
        t0 = datetime.now(timezone.utc).isoformat()
        try:
            ads = [{**a, "brand_name": DISPLAY}
                   for a in meta_library_crawler.search_brand(kw, scrolls=12, retries=2)]
            saved = database.ingest_ad_library(ads)
            total_found += len(ads)
            total_saved += saved
            print(f"  [meta] '{kw}' 발견 {len(ads)} 갱신/저장 {saved}", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"  [meta] '{kw}' 실패: {e}", flush=True)
    database.compute_matches()
    database.regrade()
    print(f"=== 완료: 발견 {total_found} · 갱신/저장 {total_saved} ===", flush=True)


if __name__ == "__main__":
    main()
