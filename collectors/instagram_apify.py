"""
Apify 기반 Instagram 소셜 영상 수집 (추후 확장용 스텁).
Actor: APIFY_INSTAGRAM_ACTOR (기본 apify~instagram-scraper).
현재는 인터페이스만 — 실제 사용 시 입력/필드 매핑을 actor 에 맞춰 구현.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
from services import apify_client  # noqa: E402

PLATFORM = "instagram"


def _normalize(it: dict, brand: str) -> dict | None:
    sid = it.get("id") or it.get("shortCode") or it.get("url", "")[-20:]
    if not sid:
        return None
    return {
        "id": f"ig_{sid}", "video_id": str(sid), "platform": PLATFORM, "brand_name": brand,
        "video_url": it.get("videoUrl") or it.get("url") or "",
        "thumbnail_url": it.get("displayUrl") or it.get("thumbnailUrl") or "",
        "caption": it.get("caption") or "",
        "channel_title": (it.get("ownerUsername") or it.get("ownerFullName") or ""),
        "views": int(it.get("videoViewCount") or it.get("videoPlayCount") or 0),
        "likes": int(it.get("likesCount") or 0), "comments": int(it.get("commentsCount") or 0),
        "shares": 0, "posted_at": str(it.get("timestamp") or "")[:10],
        "source_url": it.get("url") or "",
    }


def search_brand(brand: str, max_items: int = 5) -> tuple[list[dict], dict]:
    run_input = {"search": brand, "searchType": "hashtag", "resultsLimit": max_items,
                 "maxItems": max_items}
    items, meta = apify_client.run_actor(config.APIFY_INSTAGRAM_ACTOR, run_input, max_items=max_items)
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
