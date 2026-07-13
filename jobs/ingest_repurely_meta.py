"""repurely Meta 소재를 '다른 브랜드처럼' Ad Library 크롤로 적재 — 재생 가능한 fbcdn mp4 포함.
페이지ID 기준 크롤(키워드 검색은 영상 미수집). 썸네일은 search_brand가 로컬 저장.
사용:  python jobs/ingest_repurely_meta.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import database  # noqa: E402
from collectors import meta_library_crawler as M  # noqa: E402

# repurely 광고 페이지(Marketing API로 확인): Video st 등
PAGES = ["709427242258335", "548570688334618"]


def main() -> None:
    database.init_db()
    database.add_brand("repurely", ["올레놀샷", "repurely"], extra={"meta_page_name": "Video st"})
    all_ads = []
    seen = set()
    for pid in PAGES:
        try:
            crawled = M.search_brand("repurely", page_id=pid, scrolls=7)
        except Exception as e:  # noqa: BLE001
            print(f"  page {pid} 크롤 오류: {str(e)[:60]}")
            crawled = []
        n_v = sum(1 for a in crawled if a.get("media_type") == "video")
        print(f"  page {pid}: {len(crawled)}건 (영상 {n_v})")
        for a in crawled:
            cid = a.get("platform_ad_id") or a.get("id")
            if not cid or cid in seen:
                continue
            seen.add(cid)
            all_ads.append({**a, "brand_name": "repurely"})

    # 기존 repurely meta 교체
    conn = database.get_conn()
    conn.execute("DELETE FROM ad_library_ads WHERE brand_name='repurely' AND platform='meta'")
    conn.commit()
    conn.close()
    saved = database.ingest_ad_library(all_ads)
    v = sum(1 for a in all_ads if a.get("media_type") == "video" and a.get("video_url"))
    print(f"repurely Meta 적재: {saved}건 (재생가능 영상 {v} · 이미지 {len(all_ads)-v})")
    database.migrate_brands()


if __name__ == "__main__":
    main()
