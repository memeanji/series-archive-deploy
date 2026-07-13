"""
구글 광고 0건 브랜드의 '법인명(광고주명)'을 투명성센터 자동완성으로 자동 탐색해 채우고,
구글을 재크롤한다(영상 추출 포함). 자동완성/크롤 모두 무료(Playwright, 토큰 X).
사용:  python jobs/discover_and_crawl_google.py
"""
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import database  # noqa: E402
from collectors import google_library_crawler as G  # noqa: E402

GOOGLE_LIMIT = 80


def _has_google(brand: str) -> int:
    conn = database.get_conn()
    n = conn.execute("SELECT COUNT(*) FROM ad_library_ads WHERE platform='google' AND brand_name=?",
                     (brand,)).fetchone()[0]
    conn.close()
    return n


def main() -> None:
    database.init_db()
    conn = database.get_conn()
    brands = [r[0] for r in conn.execute(
        "SELECT display_name FROM brands WHERE COALESCE(is_active,1)=1 ORDER BY display_name").fetchall()]
    conn.close()
    targets = [b for b in brands if _has_google(b) == 0]
    print(f"[{datetime.now():%H:%M}] 구글 0건 브랜드 {len(targets)}개 — 법인명 자동탐색+재크롤")
    filled = 0
    for i, b in enumerate(targets, 1):
        try:
            names = G.fetch_advertiser_names(b)
            term = names[0] if names else (database.get_brand(b) or {}).get("google_advertiser_name") or b
            if names:
                database.add_brand(b, [], extra={"google_advertiser_name": names[0]})
            ads, log = G.search_brand(term, limit=GOOGLE_LIMIT)
            ads = [{**a, "brand_name": b} for a in ads]
            saved = database.ingest_ad_library(ads)
            if saved:
                filled += 1
            print(f"  [{i}/{len(targets)}] {b} (법인명:{term}): {saved}건 / 영상 {log.get('youtube_linked',0)}")
        except Exception as e:  # noqa: BLE001
            print(f"  [{i}/{len(targets)}] {b}: 실패 {str(e)[:70]}")
    database.compute_matches()
    database.regrade()
    database.migrate_brands()
    print(f"=== 완료: {filled}개 브랜드 신규 구글 광고 확보 ===")


if __name__ == "__main__":
    main()
