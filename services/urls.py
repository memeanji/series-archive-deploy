"""URL 정규화/검증 — Google 투명성센터 상대경로·localhost 오류 방지.
   + Meta fbcdn video_url 만료 판정(서명 URL 의 oe= 파라미터 = 만료 epoch, hex)."""
from __future__ import annotations

import re
import time
from typing import Optional

GOOGLE_TC = "https://adstransparency.google.com"
_BAD = ("localhost", "127.0.0.1", "streamlit")
_OE_RE = re.compile(r"[?&]oe=([0-9A-Fa-f]+)")


def fbcdn_url_expired(url: Optional[str]) -> Optional[bool]:
    """fbcdn 서명 URL 의 oe(만료 epoch, hex)로 만료 여부 판정.
       True=만료, False=유효, None=oe 없음(판정 불가)."""
    if not url:
        return None
    m = _OE_RE.search(url)
    if not m:
        return None
    try:
        return int(m.group(1), 16) <= int(time.time())
    except ValueError:
        return None


def meta_video_state(ad: dict) -> str:
    """렌더 시점 동적 판정 — video_url 은 만료되는 임시값으로 취급.
       반환: '' (영상 아님) | 'ok' | 'expired_url' | 'private_or_deleted' | 'unavailable'."""
    mt = (ad.get("media_type") or ad.get("ad_format") or "").lower()
    if mt != "video":
        return ""
    vu = (ad.get("video_url") or "").strip()
    if vu.startswith("http"):
        return "expired_url" if fbcdn_url_expired(vu) else "ok"
    stored = (ad.get("video_status") or "").strip()
    return stored if stored in ("private_or_deleted", "unavailable") else "unavailable"


def is_valid_external_url(url: Optional[str]) -> bool:
    if not url:
        return False
    u = url.strip().lower()
    if not (u.startswith("http://") or u.startswith("https://")):
        return False
    return not any(b in u for b in _BAD)


def normalize_google_transparency_url(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    u = url.strip()
    low = u.lower()
    # 잘못 저장된 localhost/streamlit 경로 → 뒤의 /advertiser... 만 살려 도메인 교체
    if any(b in low for b in _BAD):
        idx = u.find("/advertiser/")
        if idx != -1:
            return GOOGLE_TC + u[idx:]
        return None
    if low.startswith("http://") or low.startswith("https://"):
        return u
    if u.startswith("/advertiser/"):
        return GOOGLE_TC + u
    if u.startswith("advertiser/"):
        return GOOGLE_TC + "/" + u
    return None
