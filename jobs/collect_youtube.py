"""
관심 브랜드(watchlist.json) YouTube 원본 영상 수집:  python jobs/collect_youtube.py [브랜드당개수]
브랜드명으로 search.list → videos.list(통계) → social_videos 적재 + 스냅샷 + 재등급.
YOUTUBE_API_KEY 없으면 비활성.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import database  # noqa: E402
import services.youtube as YT  # noqa: E402

PER_BRAND = int(sys.argv[1]) if len(sys.argv) > 1 else 5


def collect_brand_youtube(brand: str, per: int = PER_BRAND) -> int:
    ids = YT.search_video_ids(brand, max_results=per)
    vids = YT.fetch_videos(ids)
    for v in vids:
        v["brand_name"] = brand
    n = database.ingest_social_videos(vids)
    for v in vids:
        database.add_snapshot(v["id"], v["views"], v["likes"], v["comments"], v["shares"])
    print(f"  [youtube] '{brand}' {n}건")
    return n


def main() -> None:
    if not YT.is_enabled():
        print("YOUTUBE_API_KEY 없음 → 비활성")
        return
    wl = json.loads((Path(__file__).resolve().parent.parent / "data" / "watchlist.json")
                    .read_text(encoding="utf-8"))
    brands = wl.get("brands", [])
    database.init_db()
    print(f"=== YouTube 수집: {len(brands)}개 브랜드 ===")
    total = sum(collect_brand_youtube(b) for b in brands)
    g = database.regrade()
    print(f"=== 완료: {total}건 적재 · 재등급 {g}건 ===")


if __name__ == "__main__":
    main()
