"""
Apify 소량 테스트 (비용 방지).  사용:
  python jobs/test_apify.py tiktok 2 3        # tiktok, 브랜드2개, 각 3건
  python jobs/test_apify.py tiktok 2 3 --dry  # 저장 안 함(드라이런)
  python jobs/test_apify.py google 3 20       # google(actor 설정 시)
저장 전 dry-run 로그를 출력하고, --dry 가 아니면 social_videos/ad_library 에 적재한다.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
import database  # noqa: E402
from services import apify_client  # noqa: E402


def _brands(n: int) -> list[str]:
    wl = json.loads((Path(__file__).resolve().parent.parent / "data" / "watchlist.json")
                    .read_text(encoding="utf-8"))
    return wl.get("brands", [])[:n]


def main() -> None:
    provider = sys.argv[1] if len(sys.argv) > 1 else "tiktok"
    n_brands = int(sys.argv[2]) if len(sys.argv) > 2 else 2
    max_items = int(sys.argv[3]) if len(sys.argv) > 3 else 3
    dry = "--dry" in sys.argv

    me = apify_client.validate_token()
    if not me:
        print("APIFY_TOKEN 무효 또는 USE_APIFY=false")
        return
    print(f"[Apify] user={me.get('username')} plan={(me.get('plan') or {}).get('id')} "
          f"| provider={provider} brands={n_brands} max_items={max_items} dry={dry}")
    database.init_db()

    totals = {"results": 0, "saved": 0, "excluded": 0, "dedup": 0, "errors": 0}

    if provider == "tiktok":
        from collectors import tiktok_apify
        all_saved = []
        for b in _brands(n_brands):
            vids, log = tiktok_apify.search_brand(b, max_items=max_items)
            print(f"  [DRY] tiktok '{b}' actor={log['actor']} 결과={log['results']} "
                  f"후보={log['candidates']} 저장후보={log['saved_candidates']} "
                  f"중복제거={log['dedup_removed']} 오류={log['error']}")
            totals["results"] += log["results"]; totals["dedup"] += log["dedup_removed"]
            if log["error"]:
                totals["errors"] += 1
            all_saved += vids
        if not dry and all_saved:
            n = database.ingest_social_videos(all_saved)
            for v in all_saved:
                database.add_snapshot(v["id"], v["views"], v["likes"], v["comments"], v["shares"])
            database.regrade()
            totals["saved"] = n
            print(f"  → social_videos 적재 {n}건 + 스냅샷 + 재등급")

    elif provider == "google":
        from collectors import google_transparency_apify
        all_saved = []
        for b in _brands(n_brands):
            ads, log = google_transparency_apify.search_brand(b, max_items=max_items)
            print(f"  [DRY] google '{b}' actor={log['actor']} 결과={log['results']} "
                  f"후보={log['candidates']} 저장후보={log['saved_candidates']} "
                  f"검색광고제외={log['excluded_search_text']} 미디어없음={log['excluded_no_media']} "
                  f"중복={log['dedup_removed']} 오류={log['error']}")
            totals["results"] += log["results"]
            totals["excluded"] += log["excluded_search_text"] + log["excluded_no_media"]
            if log["error"]:
                totals["errors"] += 1
            all_saved += ads
        if not dry and all_saved:
            n = database.ingest_ad_library(all_saved)
            totals["saved"] = n
            print(f"  → ad_library_ads 적재 {n}건")

    print(f"=== 합계: 결과 {totals['results']} · 저장 {totals['saved']} · "
          f"제외 {totals['excluded']} · 중복 {totals['dedup']} · 오류 {totals['errors']} ===")


if __name__ == "__main__":
    main()
