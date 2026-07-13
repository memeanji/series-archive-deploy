"""
Apify 기반 Google Transparency Center 광고 수집 (옵션, GOOGLE_TRANSPARENCY_PROVIDER=apify).
Actor: APIFY_GOOGLE_ACTOR (비어 있으면 비활성 — Apify Store에서 actor 슬러그 지정 필요).
소재형 광고(image/video/display)만, 검색/텍스트 광고는 제외. localhost 상대경로 방어.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
from services import apify_client  # noqa: E402
from services.urls import normalize_google_transparency_url  # noqa: E402

PLATFORM = "google"


def _format_of(it: dict) -> str:
    fmt = str(it.get("format") or it.get("creativeType") or it.get("adFormat") or "").lower()
    if "video" in fmt:
        return "video"
    if "image" in fmt:
        return "image"
    if "text" in fmt or "search" in fmt:
        return "search_text"
    if it.get("videoUrl") or it.get("video_url"):
        return "video"
    if it.get("imageUrl") or it.get("image_url") or it.get("previewUrl") or it.get("thumbnailUrl"):
        return "image"
    return "unknown"


def _normalize(it: dict, brand: str) -> dict | None:
    cid = it.get("creativeId") or it.get("id") or it.get("adId")
    if not cid:
        return None
    fmt = _format_of(it)
    media = (it.get("videoUrl") or it.get("video_url") or it.get("imageUrl")
             or it.get("image_url") or it.get("previewUrl") or it.get("thumbnailUrl") or "")
    turl = normalize_google_transparency_url(
        it.get("transparencyUrl") or it.get("url") or it.get("adUrl") or "")
    landing = it.get("landingUrl") or it.get("destinationUrl") or it.get("clickUrl") or None
    return {
        "platform": PLATFORM, "platform_ad_id": str(cid),
        "brand_name": brand, "advertiser_name": it.get("advertiserName") or brand,
        "ad_title": it.get("title") or "", "ad_copy": it.get("adText") or it.get("body") or "",
        "ad_format": fmt, "media_type": "video" if fmt == "video" else "image",
        "thumbnail_url": it.get("thumbnailUrl") or it.get("previewUrl") or media or "",
        "video_url": it.get("videoUrl") or it.get("video_url") or "",
        "preview_url": it.get("previewUrl") or it.get("imageUrl") or "", "media_url": media,
        "landing_url": landing, "transparency_url": turl or "",
        "original_ad_url": turl or "", "status": "live",
        "raw_data": {"creative_id": cid, "fmt": fmt},
    }


def search_brand(brand: str, max_items: int = 20) -> tuple[list[dict], dict]:
    if not config.APIFY_GOOGLE_ACTOR:
        return [], {"actor": "(미설정)", "run_id": "-", "results": 0, "candidates": 0,
                    "saved_candidates": 0, "excluded_search_text": 0, "excluded_no_media": 0,
                    "dedup_removed": 0, "error": "APIFY_GOOGLE_ACTOR 미설정"}
    run_input = {"brand": brand, "searchTerms": [brand], "query": brand,
                 "maxItems": max_items, "region": "KR"}
    items, meta = apify_client.run_actor(config.APIFY_GOOGLE_ACTOR, run_input, max_items=max_items)
    log = {"actor": meta["actor"], "run_id": meta["run_id"], "results": meta["item_count"],
           "candidates": len(items), "saved_candidates": 0, "excluded_search_text": 0,
           "excluded_no_media": 0, "dedup_removed": 0, "error": meta["error"]}
    out, seen = [], set()
    for it in items:
        v = _normalize(it, brand)
        if not v:
            continue
        if v["ad_format"] == "search_text":
            log["excluded_search_text"] += 1
            continue
        if v["ad_format"] in ("unknown", "no_media") or not (v["thumbnail_url"] or v["video_url"]
                                                             or v["media_url"]):
            log["excluded_no_media"] += 1
            continue
        if v["platform_ad_id"] in seen:
            log["dedup_removed"] += 1
            continue
        seen.add(v["platform_ad_id"])
        out.append(v)
    log["saved_candidates"] = len(out)
    return out, log


if __name__ == "__main__":
    term = sys.argv[1] if len(sys.argv) > 1 else "더스크랙"
    ads, log = search_brand(term, max_items=10)
    print("LOG:", log)
