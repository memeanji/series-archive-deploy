"""
TikTok 레퍼런스 수집기.

기본값: mock 모드(data/mock_ads.json 의 platform=tiktok).
실제 모드(USE_REAL_TIKTOK=true): TikTok **Commercial Content API** 로 교체.

⚠️ 레퍼런스(남의 광고)용으로는 두 갈래가 있다:
  1) Commercial Content API (/v2/research/adlib/...) — 공개 광고 라이브러리.
     지역(주로 EU) 제한이 크고 별도 승인(Research API)이 필요할 수 있음.
  2) Marketing API (business-api, 우리 .env 의 TIKTOK_ACCESS_TOKEN) — '내 광고계정'의
     소재/지표만 조회 가능(남의 광고는 불가). 자사 레퍼런스 축적용으로는 쓸 수 있음.
  조회수/좋아요/댓글/공유 같은 공개 반응 지표는 1) 계열에서만 일부 제공됨(없으면 0).

_collect_commercial_content() 골격을 두었으니, 승인/지역 확인 후 엔드포인트·필드를 맞추세요.
"""
from __future__ import annotations

import config
from collectors.base import finalize, load_mock

PLATFORM = "tiktok"


def _collect_commercial_content() -> list[dict]:
    import requests

    token = config.TIKTOK_ACCESS_TOKEN
    if not token:
        print("  [tiktok] TIKTOK_ACCESS_TOKEN 없음 → 빈 결과")
        return []

    # Commercial Content API(예시 골격). 실제 path/params 는 승인된 제품에 맞게 조정.
    url = "https://open.tiktokapis.com/v2/research/adlib/ad/query/"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    body = {
        "filters": {"ad_published_date_range": {"min": "", "max": ""}, "country_code": "KR"},
        "max_count": 50,
    }
    try:
        r = requests.post(url, headers=headers, json=body, timeout=60)
        data = r.json()
    except Exception as e:  # noqa: BLE001
        print(f"  [tiktok] 요청 실패: {e}")
        return []
    if str(data.get("error", {}).get("code", "ok")) not in ("ok", "0"):
        print(f"  [tiktok] API 오류: {data.get('error')}")
        return []

    ads = []
    for item in (data.get("data", {}) or {}).get("ads", []):
        ads.append({
            "platform": PLATFORM,
            "platform_ad_id": item.get("id"),
            "advertiser_name": item.get("advertiser_name"),
            "ad_text": item.get("ad_text") or item.get("caption"),
            "original_ad_url": item.get("ad_url"),
            "video_url": item.get("video_url"),
            "thumbnail_url": item.get("cover_image_url"),
            "media_type": "video",
            "status": "live",
            "views": item.get("view_count"),
            "likes": item.get("like_count"),
            "comments": item.get("comment_count"),
            "shares": item.get("share_count"),
            "raw_data": item,
        })
    print(f"  [tiktok] Commercial Content 수집 {len(ads)}건")
    return ads


def collect() -> list[dict]:
    if config.USE_REAL_TIKTOK:
        return finalize(_collect_commercial_content())
    print("  [tiktok] mock 모드")
    return load_mock(PLATFORM)
