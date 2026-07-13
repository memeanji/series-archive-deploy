"""
Apify 기반 TikTok 소셜 영상 수집 (옵션, SOCIAL_VIDEO_PROVIDER=apify).
Actor: APIFY_TIKTOK_ACTOR (기본 clockworks~tiktok-scraper).
조회수/좋아요/댓글/공유는 '원본 소셜 영상' 기준(광고 성과 아님).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
from services import apify_client  # noqa: E402

PLATFORM = "tiktok"


def _g(d: dict, *keys, default=0):
    for k in keys:
        if isinstance(d, dict) and d.get(k) not in (None, ""):
            return d[k]
    return default


def _normalize(it: dict, brand: str) -> dict | None:
    stats = it.get("stats") or it.get("statsV2") or it
    vid = it.get("id") or it.get("videoId") or (it.get("webVideoUrl") or "")[-19:]
    if not vid:
        return None
    author = it.get("authorMeta") or it.get("author") or {}
    cover = (it.get("videoMeta") or {}).get("coverUrl") or it.get("covers") or ""
    if isinstance(cover, list):
        cover = cover[0] if cover else ""
    return {
        "id": f"tt_{vid}", "video_id": str(vid), "platform": PLATFORM,
        "brand_name": brand,
        "video_url": it.get("videoUrl") or it.get("webVideoUrl") or "",
        "thumbnail_url": cover,
        "caption": it.get("text") or it.get("desc") or "",
        "channel_title": (author.get("name") or author.get("nickName") or "")
        if isinstance(author, dict) else str(author),
        "views": int(_g(stats, "playCount", "views", "playCountStr", default=0) or 0),
        "likes": int(_g(stats, "diggCount", "likes", default=0) or 0),
        "comments": int(_g(stats, "commentCount", "comments", default=0) or 0),
        "shares": int(_g(stats, "shareCount", "shares", default=0) or 0),
        "posted_at": str(it.get("createTimeISO") or it.get("createTime") or "")[:10],
        "source_url": it.get("webVideoUrl") or "",
    }


def search_brand(brand: str, max_items: int = 5) -> tuple[list[dict], dict]:
    run_input = {"searchQueries": [brand], "resultsPerPage": max_items,
                 "shouldDownloadVideos": False, "shouldDownloadCovers": True,
                 "maxItems": max_items}
    items, meta = apify_client.run_actor(config.APIFY_TIKTOK_ACTOR, run_input, max_items=max_items)
    out, seen = [], set()
    for it in items:
        v = _normalize(it, brand)
        if v and v["id"] not in seen:
            seen.add(v["id"])
            out.append(v)
    log = {"actor": meta["actor"], "run_id": meta["run_id"], "results": meta["item_count"],
           "candidates": len(items), "saved_candidates": len(out),
           "dedup_removed": len(items) - len(out), "error": meta["error"]}
    return out, log


if __name__ == "__main__":
    term = sys.argv[1] if len(sys.argv) > 1 else "더스크랙"
    ads, log = search_brand(term, max_items=3)
    print("LOG:", log)
    for v in ads[:5]:
        print(f"  {v['views']}뷰 {v['likes']}♥ | {v['caption'][:40]}")
