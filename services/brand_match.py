"""
소셜 영상 ↔ 브랜드 검증 점수.
브랜드명 키워드 검색 결과를 그대로 확정하지 않고, 공식 계정/도메인/키워드 기반으로
brand_match_score(0~100) + review_status(approved/needs_review/rejected) 를 매긴다.
"""
from __future__ import annotations


def _n(s) -> str:
    return (str(s or "")).lower().strip()


def _handles(brand: dict) -> list[str]:
    vals = [brand.get("tiktok_handle"), brand.get("instagram_handle"),
            brand.get("youtube_channel_name"), brand.get("meta_page_name"),
            brand.get("google_advertiser_name")]
    out = []
    for v in vals:
        v = _n(v).lstrip("@")
        if v:
            out.append(v)
    return out


def score(video: dict, brand: dict, from_keyword_search: bool = True) -> dict:
    """video: social dict, brand: brands row dict. 반환 {score, reason, status}."""
    title = _n(video.get("title"))
    caption = _n(video.get("caption"))
    text = f"{title} {caption}"
    author = _n(video.get("channel_title"))
    src = _n(video.get("source_url")) + " " + _n(video.get("video_url"))

    display = _n(brand.get("display_name"))
    try:
        import json
        kws = [_n(k) for k in json.loads(brand.get("search_keywords") or "[]") if _n(k)]
    except Exception:  # noqa: BLE001
        kws = [display] if display else []
    domain = _n(brand.get("official_domain")).replace("https://", "").replace("http://", "").split("/")[0]
    officials = _handles(brand)

    s = 0
    reasons = []
    if officials and author and any(h in author or author in h for h in officials):
        s += 60
        reasons.append("공식 계정명 일치")
    if officials and any(h and h in src for h in officials):
        s += 50
        reasons.append("공식 핸들 URL 일치")
    if domain and domain in src:
        s += 50
        reasons.append("공식 도메인 일치")
    if display and author and (display in author or display.replace(" ", "") in author.replace(" ", "")):
        s += 30
        reasons.append("채널명에 브랜드명")
    if display and display in text:
        s += 25
        reasons.append("제목/캡션에 브랜드명")
    if any(k in text for k in kws if k and k != display):
        s += 15
        reasons.append("검색 키워드 포함")
    nospace = display.replace(" ", "")
    if nospace and (f"#{nospace}" in text or f"#{display}" in text):
        s += 10
        reasons.append("해시태그 브랜드명")
    if s == 0 and from_keyword_search:
        s += 5
        reasons.append("키워드 검색으로만 발견")

    s = min(100, s)
    status = "approved" if s >= 50 else "needs_review" if s >= 20 else "rejected"
    # 브랜드명/키워드가 텍스트 어디에도 없고 공식 일치도 없으면 저장 자체 부적합
    has_any = bool(reasons) and not (len(reasons) == 1 and reasons[0] == "키워드 검색으로만 발견")
    return {"score": float(s), "reason": "; ".join(reasons) or "근거 없음",
            "status": status, "keep": has_any or s >= 20}
