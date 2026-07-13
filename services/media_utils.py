"""
표준화/유틸 헬퍼.
- normalize_ad: 수집기 raw dict → ads 테이블 컬럼에 맞춘 표준 dict
- dedup_key: platform_ad_id 가 없을 때 쓰는 fallback 해시
- estimated_running_days: 집행 추정일수
"""
from __future__ import annotations

import hashlib
from datetime import date, datetime
from typing import Any, Optional

# ads 테이블에 실제로 저장하는 컬럼(화이트리스트). 그 외 키는 raw_data 로 들어간다.
AD_COLUMNS = (
    "platform", "platform_ad_id", "dedup_key", "advertiser_name", "advertiser_id",
    "ad_text", "headline", "description", "cta", "landing_url", "original_ad_url",
    "media_type", "video_url", "thumbnail_url", "transcript", "status", "first_seen",
    "last_seen", "started_at", "ended_at", "estimated_running_days", "views", "likes",
    "comments", "shares", "reference_score", "hook_tags", "format_tags", "raw_data",
)


def _to_iso(d: Any) -> Optional[str]:
    if d is None or d == "":
        return None
    if isinstance(d, (date, datetime)):
        return d.isoformat()[:10]
    return str(d)[:10]


def ad_text_hash(text: Optional[str]) -> str:
    return hashlib.sha1((text or "").strip().encode("utf-8")).hexdigest()[:16]


def make_dedup_key(ad: dict) -> str:
    """platform_ad_id 가 없을 때: advertiser + ad_text 해시 + landing_url 조합."""
    parts = [
        (ad.get("advertiser_name") or "").strip().lower(),
        ad_text_hash(ad.get("ad_text")),
        (ad.get("landing_url") or "").strip().lower(),
    ]
    return "|".join(parts)


def estimated_running_days(started: Any, ended: Any, first_seen: Any, last_seen: Any) -> Optional[int]:
    """started~ended 가 있으면 그걸로, 없으면 first_seen~last_seen 로 집행 추정일수 계산."""
    def _d(x):
        s = _to_iso(x)
        if not s:
            return None
        try:
            return datetime.strptime(s, "%Y-%m-%d").date()
        except ValueError:
            return None
    start = _d(started) or _d(first_seen)
    end = _d(ended) or _d(last_seen)
    if start and end:
        return max(0, (end - start).days)
    return None


def normalize_ad(raw: dict) -> dict:
    """수집기가 만든 dict 를 ads 표준 스키마로 정리한다."""
    ad: dict[str, Any] = {}
    for col in AD_COLUMNS:
        if col in raw:
            ad[col] = raw[col]

    ad["platform"] = (raw.get("platform") or "unknown").lower()
    ad["media_type"] = (raw.get("media_type") or "unknown").lower()
    ad["status"] = (raw.get("status") or "unknown").lower()

    for k in ("first_seen", "last_seen", "started_at", "ended_at"):
        if raw.get(k) is not None:
            ad[k] = _to_iso(raw[k])

    for k in ("views", "likes", "comments", "shares"):
        v = raw.get(k)
        ad[k] = int(v) if v not in (None, "") else 0

    ad["hook_tags"] = list(raw.get("hook_tags") or [])
    ad["format_tags"] = list(raw.get("format_tags") or [])

    ad["estimated_running_days"] = raw.get("estimated_running_days")
    if ad["estimated_running_days"] is None:
        ad["estimated_running_days"] = estimated_running_days(
            ad.get("started_at"), ad.get("ended_at"), ad.get("first_seen"), ad.get("last_seen")
        )

    # dedup 키
    if not ad.get("platform_ad_id"):
        ad["dedup_key"] = make_dedup_key(ad)

    # raw_data 보존(원본 전체)
    ad["raw_data"] = raw.get("raw_data") or raw
    return ad
