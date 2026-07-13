"""구글 limit(80) 도달로 누락된 브랜드만 limit 300·스크롤 25로 재크롤(구글만).
crawl_brand.py의 구글 ingest 방식 재사용. 메타/YouTube는 건드리지 않음."""
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import database  # noqa: E402
from collectors import google_library_crawler as G  # noqa: E402

con = sqlite3.connect(str(Path(__file__).resolve().parent.parent / "data" / "series_archive.db"))
con.row_factory = sqlite3.Row
# 80개 이상(limit 도달) = 누락 의심 브랜드
missing = con.execute(
    "SELECT brand_name, count(*) c FROM ad_library_ads WHERE platform='google' "
    "GROUP BY brand_name HAVING c>=80 ORDER BY c DESC").fetchall()
print(f"재크롤 대상 {len(missing)}개 브랜드", flush=True)

for i, row in enumerate(missing):
    brand = row["brand_name"]
    before = row["c"]
    b = con.execute("SELECT google_advertiser_name FROM brands WHERE display_name=?", (brand,)).fetchone()
    term = (b["google_advertiser_name"] if b and b["google_advertiser_name"] else brand)
    try:
        gads, glog = G.search_brand(term, limit=500, scrolls=60, headless=True)
        gads = [{**a, "brand_name": brand} for a in gads]
        saved = database.ingest_ad_library(gads)
        after = con.execute(
            "SELECT count(*) FROM ad_library_ads WHERE platform='google' AND brand_name=?",
            (brand,)).fetchone()[0]
        print(f"[{i+1}/{len(missing)}] {brand} ({term}): 발견 {glog['found']} 저장 {saved} "
              f"→ {before}→{after}개", flush=True)
    except Exception as e:  # noqa: BLE001
        print(f"[{i+1}/{len(missing)}] {brand}: ERR {str(e)[:70]}", flush=True)

con.close()
print("=== 재크롤 완료 ===", flush=True)
