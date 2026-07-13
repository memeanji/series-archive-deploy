"""
광고 라이브러리 ↔ 소셜 영상 매칭 점수.
- 브랜드명 일치(+40)
- 카피/캡션 유사도(0~35)
- 랜딩 URL/상품명 ↔ 캡션 유사도(0~15)
- 썸네일/영상 유사도(0~10) — 현재는 placeholder(미디어 해시 미구현)
match_score = 위 합(0~100).
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher
from urllib.parse import urlparse


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").lower()).strip()


def _ratio(a: str, b: str) -> float:
    a, b = _norm(a), _norm(b)
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def _product_token(landing_url: str) -> str:
    """랜딩 URL에서 상품/브랜드 토큰 추출(도메인 2단계 + path 키워드)."""
    if not landing_url:
        return ""
    p = urlparse(landing_url)
    host = (p.netloc or "").split(":")[0]
    root = host.split(".")[-2] if "." in host else host
    return root


def match(ad: dict, sv: dict) -> dict:
    brand_match = 1 if _norm(ad.get("brand_name")) and \
        _norm(ad.get("brand_name")) == _norm(sv.get("brand_name")) else 0
    copy_sim = _ratio(ad.get("ad_copy"), sv.get("caption"))

    token = _product_token(ad.get("landing_url"))
    cap = _norm(sv.get("caption"))
    url_sim = 1.0 if token and token in cap else _ratio(token, cap[:40]) if token else 0.0

    media_sim = 0.0  # TODO: 썸네일/영상 perceptual hash 비교(추후)

    score = brand_match * 40 + copy_sim * 35 + url_sim * 15 + media_sim * 10
    return {
        "match_score": round(score, 1),
        "brand_match": brand_match,
        "copy_sim": round(copy_sim, 3),
        "url_sim": round(url_sim, 3),
        "media_sim": round(media_sim, 3),
    }
