"""
관심 브랜드(watchlist.json) 전체 실수집:  python jobs/collect_brands.py
메타+구글 → ad_library_ads, (APIFY 있으면) TikTok → social_videos, 매칭 재계산.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import database  # noqa: E402
from jobs.crawl_brand import crawl_one  # noqa: E402


def main() -> None:
    wl = json.loads((Path(__file__).resolve().parent.parent / "data" / "watchlist.json")
                    .read_text(encoding="utf-8"))
    brands = wl.get("brands", [])
    database.init_db()
    print(f"=== 관심 브랜드 {len(brands)}개 수집 ===")
    ad = social = 0
    for b in brands:
        r = crawl_one(b)
        ad += r["ad"]
        social += r["social"]
    m = database.compute_matches()
    print(f"=== 완료: 광고 {ad} · 소셜 {social} · 매칭 {m} ===")


if __name__ == "__main__":
    main()
