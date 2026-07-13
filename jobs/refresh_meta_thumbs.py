"""
메타 썸네일 영구화: 메타 광고가 있는 브랜드를 재크롤해 fbcdn 만료 URL → static 파일로 교체.
(구글은 이미 static 스크린샷이라 제외, YouTube는 http 안정 → 제외)
사용:  python jobs/refresh_meta_thumbs.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import database  # noqa: E402
from collectors import meta_library_crawler  # noqa: E402


def main() -> None:
    database.init_db()
    conn = database.get_conn()
    brands = [r[0] for r in conn.execute(
        "SELECT DISTINCT brand_name FROM ad_library_ads WHERE platform='meta' AND brand_name<>''"
    ).fetchall()]
    conn.close()
    print(f"=== 메타 썸네일 리프레시: {len(brands)}개 브랜드 ===")
    for b in brands:
        try:
            ads = [{**a, "brand_name": b} for a in meta_library_crawler.search_brand(b)]
            n = database.ingest_ad_library(ads)
            local = sum(1 for a in ads if (a.get("thumbnail_url") or "").startswith("app/static"))
            print(f"  {b}: {len(ads)}건 (로컬썸네일 {local}) 저장 {n}")
        except Exception as e:  # noqa: BLE001
            print(f"  {b}: 실패 {e}")
    print("=== DONE ===")


if __name__ == "__main__":
    main()
