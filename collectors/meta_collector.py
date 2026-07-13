"""
Meta 레퍼런스 수집기.

기본값: mock 모드(data/mock_ads.json 의 platform=meta).
실제 모드(USE_REAL_META=true): Meta **Ad Library API**(ads_archive)로 교체.

⚠️ 중요: 레퍼런스(남의 광고) 수집은 Marketing API 가 아니라 Ad Library API 다.
  - 엔드포인트: https://graph.facebook.com/<ver>/ads_archive
  - 정치/이슈 광고는 전 세계 공개이지만, 일반 커머스 광고는 EU 등 일부 지역만/필드 제한.
  - 조회수·좋아요 같은 공개 반응 지표는 Ad Library 에서 제공되지 않을 수 있음(없으면 0).
  실제 연결 시 _collect_real() 의 fields/검색 파라미터를 환경에 맞게 조정하세요.
"""
from __future__ import annotations

import config
from collectors.base import finalize, load_mock

PLATFORM = "meta"
GRAPH = "https://graph.facebook.com"


def _collect_real() -> list[dict]:
    import requests

    if not config.META_ACCESS_TOKEN:
        print("  [meta] META_ACCESS_TOKEN 없음 → 빈 결과")
        return []
    if not config.META_SEARCH_TERMS:
        print("  [meta] META_SEARCH_TERMS(.env) 가 비어 있음 → 검색어를 넣어야 Ad Library 조회 가능")
        return []

    url = f"{GRAPH}/{config.META_API_VERSION}/ads_archive"
    fields = ",".join([
        "id", "page_name", "ad_creative_bodies", "ad_creative_link_titles",
        "ad_creative_link_descriptions", "ad_creative_link_captions",
        "ad_snapshot_url", "ad_delivery_start_time", "ad_delivery_stop_time",
        "publisher_platforms", "languages",
    ])
    ads: list[dict] = []
    for term in config.META_SEARCH_TERMS:
        params = {
            "access_token": config.META_ACCESS_TOKEN,
            "search_terms": term,
            "ad_reached_countries": str(config.META_COUNTRIES).replace("'", '"'),
            "ad_active_status": "ALL",
            "fields": fields,
            "limit": 100,
        }
        try:
            r = requests.get(url, params=params, timeout=60)
            data = r.json()
        except Exception as e:  # noqa: BLE001
            print(f"  [meta] 요청 실패({term}): {e}")
            continue
        if "error" in data:
            print(f"  [meta] API 오류: {data['error'].get('message')}")
            continue
        for item in data.get("data", []):
            bodies = item.get("ad_creative_bodies") or [""]
            titles = item.get("ad_creative_link_titles") or [""]
            ads.append({
                "platform": PLATFORM,
                "platform_ad_id": item.get("id"),
                "advertiser_name": item.get("page_name"),
                "ad_text": bodies[0],
                "headline": titles[0],
                "original_ad_url": item.get("ad_snapshot_url"),
                "media_type": "unknown",  # Ad Library 는 영상/이미지 구분이 항상 오지 않음
                "status": "live" if not item.get("ad_delivery_stop_time") else "ended",
                "started_at": (item.get("ad_delivery_start_time") or "")[:10] or None,
                "ended_at": (item.get("ad_delivery_stop_time") or "")[:10] or None,
                "raw_data": item,
            })
    print(f"  [meta] Ad Library 수집 {len(ads)}건")
    return ads


def collect() -> list[dict]:
    if config.USE_REAL_META:
        return finalize(_collect_real())
    print("  [meta] mock 모드")
    return load_mock(PLATFORM)
