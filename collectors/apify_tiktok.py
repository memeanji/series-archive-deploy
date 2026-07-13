"""
Apify TikTok 스크래퍼 연동 (선택사항).
.env 에 APIFY_TOKEN 이 있으면 브랜드 키워드로 TikTok 영상(조회수/좋아요/댓글/공유)을 수집,
없으면 비활성(빈 결과 + 안내). 결과는 social_videos 스키마로 정규화해 반환한다.

Apify Actor: clockworks/tiktok-scraper (search 모드). 액터/필드는 환경에 맞게 조정 가능.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402

PLATFORM = "tiktok"
ACTOR = "clockworks~tiktok-scraper"   # Apify actor id (slug)


def is_enabled() -> bool:
    return bool(config.APIFY_TOKEN)


def search_brand(brand: str, limit: int = 30) -> list[dict]:
    """APIFY_TOKEN 이 있을 때만 동작. 없으면 [] 반환."""
    if not is_enabled():
        print("  [apify] APIFY_TOKEN 없음 → 비활성(소셜 영상 수집 건너뜀)")
        return []
    import requests

    run_input = {
        "searchQueries": [brand],
        "resultsPerPage": limit,
        "shouldDownloadVideos": False,
        "shouldDownloadCovers": True,
    }
    try:
        r = requests.post(
            f"https://api.apify.com/v2/acts/{ACTOR}/run-sync-get-dataset-items"
            f"?token={config.APIFY_TOKEN}",
            json=run_input, timeout=300)
        items = r.json() if r.headers.get("content-type", "").startswith("application/json") else []
    except Exception as e:  # noqa: BLE001
        print(f"  [apify] '{brand}' 요청 실패: {e}")
        return []

    out = []
    for it in items if isinstance(items, list) else []:
        stats = it.get("stats") or it
        out.append({
            "id": f"tt_{it.get('id') or it.get('videoId') or it.get('webVideoUrl','')[-18:]}",
            "brand_name": brand,
            "platform": PLATFORM,
            "video_url": it.get("videoUrl") or it.get("downloadAddr") or it.get("webVideoUrl") or "",
            "thumbnail_url": (it.get("videoMeta") or {}).get("coverUrl") or it.get("covers", [""])[0]
            if it.get("covers") else (it.get("videoMeta") or {}).get("coverUrl", ""),
            "caption": it.get("text") or it.get("desc") or "",
            "views": stats.get("playCount") or stats.get("views") or 0,
            "likes": stats.get("diggCount") or stats.get("likes") or 0,
            "comments": stats.get("commentCount") or stats.get("comments") or 0,
            "shares": stats.get("shareCount") or stats.get("shares") or 0,
            "posted_at": str(it.get("createTimeISO") or it.get("createTime") or "")[:10],
            "source_url": it.get("webVideoUrl") or "",
        })
    print(f"  [apify] '{brand}' {len(out)}건")
    return out


if __name__ == "__main__":
    term = sys.argv[1] if len(sys.argv) > 1 else "더스크랙"
    print("enabled:", is_enabled())
    for v in search_brand(term)[:5]:
        print(f"  - {v['views']}뷰 {v['likes']}♥ | {v['caption'][:40]}")
