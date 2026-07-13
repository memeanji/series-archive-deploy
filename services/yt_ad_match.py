"""
YouTube '광고' 매칭 로직 (전체 수집과 분리).
광고 데이터(광고주/법인명·문구·썸네일·랜딩·게재일)로 검색어를 만들고,
YouTube 후보 영상과의 유사도(matching_score)를 계산해 3분류한다.

분류:
  - youtube_ad_matched     : 광고 썸네일/문구/랜딩/브랜드와 '창작물 수준' 연결됨 → 광고로 확정
  - youtube_ad_candidate   : 브랜드는 맞지만 확신 부족(채널·부분유사) → 후보
  - youtube_social_or_ppl  : 제품명/해시태그만 일치(후기·PPL·일반) → 광고 아님

⚠️ 제품명/해시태그만 같다고 광고로 확정하지 않는다.
   광고 썸네일/문구/랜딩 URL/브랜드와 연결될 때만 youtube_ad_matched.
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher
from urllib.parse import urlparse

# 법인 표기 — 브랜드명 후보를 만들 때 제거
_LEGAL = [
    "주식회사", "유한회사", "유한책임회사", "합자회사", "합명회사", "재단법인", "사단법인",
    "㈜", "(주)", "(유)", "주식 회사",
    "inc", "inc.", "co.", "co", "ltd", "ltd.", "corp", "corp.", "corporation",
    "llc", "company", "limited", "co.,ltd", "co., ltd", "co.,ltd.",
]


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").lower()).strip()


def _ratio(a: str, b: str) -> float:
    a, b = _norm(a), _norm(b)
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def strip_legal(name: str) -> str:
    """'주식회사 천경' → '천경', 'ABC Co., Ltd.' → 'ABC' 처럼 법인 표기 제거."""
    s = (name or "").strip()
    if not s:
        return ""
    # 괄호형 (주)/(유)/㈜ 제거
    s = s.replace("㈜", " ").replace("(주)", " ").replace("(유)", " ")
    low = s.lower()
    # 단어 단위로 법인 토큰 제거(앞/뒤 모두)
    tokens = re.split(r"[\s,]+", s)
    kept = []
    for t in tokens:
        tl = t.lower().strip(".,")
        if tl in _LEGAL or t.lower() in _LEGAL:
            continue
        kept.append(t)
    out = " ".join(kept).strip(" ,.")
    return out or s


def brand_candidates(advertiser: str, display_brand: str = "") -> list[str]:
    """광고주명/디스플레이명에서 브랜드명 후보 생성(법인 표기 제거 버전 우선)."""
    cands = []
    for raw in (display_brand, strip_legal(advertiser), advertiser):
        c = (raw or "").strip()
        if c and c not in cands:
            cands.append(c)
    return cands


# TLD/SLD·서브도메인 토큰(도메인 루트 추출 시 무시) — .co.kr, .com 등 복합 TLD 대응
_TLD_SLD = {"com", "net", "org", "co", "kr", "jp", "cn", "io", "shop", "store",
            "me", "biz", "info", "go", "or", "ne", "ac", "gov", "app", "kkr",
            "www", "m", "shopping", "smartstore"}


def domain_root(url: str) -> str:
    """도메인에서 브랜드 토큰 추출. 'raycelturn.co.kr' → 'raycelturn', 'brand.com' → 'brand'."""
    if not url:
        return ""
    host = (urlparse(url if "//" in url else "//" + url).netloc or "").split(":")[0]
    parts = [p for p in host.split(".") if p]
    meaningful = [p for p in parts if p.lower() not in _TLD_SLD]
    return meaningful[-1] if meaningful else (parts[0] if parts else "")


def build_queries(advertiser: str, display_brand: str = "",
                  ad_copies: list[str] | None = None,
                  landing_urls: list[str] | None = None,
                  max_q: int = 4) -> list[str]:
    """브랜드명 후보 + 상품/랜딩 도메인 + 문구 키워드로 YouTube 검색어 자동 생성."""
    brand = brand_candidates(advertiser, display_brand)[0]
    roots = []
    for u in (landing_urls or []):
        r = domain_root(u)
        if r and r.lower() != brand.lower() and r not in roots:
            roots.append(r)
    queries = [brand, f"{brand} 광고"]
    for r in roots[:1]:
        queries.append(f"{brand} {r}")
    # 광고 문구에서 의미있는 토큰 1개(브랜드와 다른 한글/영문 단어)
    for cp in (ad_copies or []):
        for w in re.findall(r"[가-힣A-Za-z][가-힣A-Za-z0-9]{1,}", cp or ""):
            if len(w) >= 2 and _norm(w) != _norm(brand) and w not in brand:
                queries.append(f"{brand} {w}")
                break
        if len(queries) >= max_q:
            break
    # 중복 제거 + 상한
    seen, out = set(), []
    for q in queries:
        k = _norm(q)
        if k and k not in seen:
            seen.add(k)
            out.append(q)
    return out[:max_q]


def _ahash_image(img) -> int:
    img = img.convert("L").resize((8, 8))
    px = list(img.getdata())
    avg = sum(px) / len(px)
    bits = 0
    for i, p in enumerate(px):
        if p >= avg:
            bits |= (1 << i)
    return bits


def ahash(src: str, root=None) -> int | None:
    """썸네일 average-hash(64bit). http URL·로컬 경로('app/static/...') 모두 지원.
    PIL 없거나 실패하면 None(썸네일 신호 생략)."""
    if not src:
        return None
    try:
        import io
        from pathlib import Path

        from PIL import Image
        if src.startswith("http"):
            import requests
            r = requests.get(src, timeout=15)
            if r.status_code != 200 or not r.content:
                return None
            img = Image.open(io.BytesIO(r.content))
        else:
            p = src[4:] if src.startswith("app/") else src   # 'app/static/..' → 'static/..'
            base = Path(root) if root else Path(".")
            fp = base / p
            if not fp.exists():
                return None
            img = Image.open(fp)
        return _ahash_image(img)
    except Exception:  # noqa: BLE001
        return None


def ahash_url(url: str) -> int | None:
    return ahash(url)


def hash_sim(h1: int | None, h2: int | None) -> float | None:
    """두 aHash의 유사도(0~1). 하나라도 없으면 None."""
    if h1 is None or h2 is None:
        return None
    dist = bin(h1 ^ h2).count("1")
    return 1.0 - dist / 64.0


def _strip_hashtags(text: str) -> str:
    """해시태그 토큰(#xxx) 제거 — '해시태그만 일치'를 본문 일치와 구분하기 위함."""
    return re.sub(r"#\S+", " ", text or "")


def score(ctx: dict, video: dict) -> dict:
    """광고 컨텍스트(ctx) ↔ YouTube 후보(video) 매칭.
    ctx: {advertiser(법인명), display_brand(브랜드명), copies[], landing_urls[],
          thumb_hashes[], last_shown}
    video: {title, description, channel_title, thumb_hash, published_at, ...}

    핵심: 브랜드명=계정명=법인명 가정 안 함. 채널명이 달라도 랜딩/상품명/문구/썸네일이
    일치하면 광고 후보로 본다. 법인명/계정명 일치는 약한 보조 신호일 뿐.
    """
    # 상품/브랜드 토큰(실제 브랜드 중심) — 법인명은 별도 약한 신호로만
    brand_terms = [ctx.get("display_brand", "")] + [domain_root(u) for u in (ctx.get("landing_urls") or [])]
    brand_terms = [t for t in brand_terms if t]
    legal_terms = [strip_legal(ctx.get("advertiser", "")), ctx.get("advertiser", "")]
    legal_terms = [t for t in legal_terms if t]

    title_desc = f"{video.get('title','')} {video.get('description','')}"
    clean = _norm(_strip_hashtags(title_desc))     # 해시태그 제거 본문
    full = _norm(title_desc)
    chan = _norm(video.get("channel_title", ""))
    desc_full = _norm(video.get("description", ""))

    # 상품/브랜드명: 본문(해시태그 외) 일치 vs 해시태그만 일치 구분
    product_in_body = any(_norm(t) in clean for t in brand_terms)
    product_any = any(_norm(t) in full for t in brand_terms)
    hashtag_only = product_any and not product_in_body
    channel_hit = any(_norm(t) in chan for t in brand_terms + legal_terms)
    legal_hit = any(_norm(t) in clean for t in legal_terms)
    # seed로 확인된 '광고 채널'과 같은 채널이면 강신호(브랜드명과 달라도 광고 채널일 수 있음)
    seed_channels = [_norm(c) for c in (ctx.get("seed_channels") or []) if c]
    seed_channel_hit = bool(chan) and chan in seed_channels

    # 문구 유사도는 해시태그 제외 본문 기준(해시태그 속 브랜드명이 유사도를 부풀리지 않게)
    clean_td = _strip_hashtags(title_desc)
    copy_sim = max([_ratio(cp, clean_td) for cp in (ctx.get("copies") or [])] or [0.0])

    roots = [domain_root(u) for u in (ctx.get("landing_urls") or [])]
    landing_hit = any(r and _norm(r) in desc_full for r in roots)

    sims = [hash_sim(h, video.get("thumb_hash")) for h in (ctx.get("thumb_hashes") or [])]
    sims = [s for s in sims if s is not None]
    thumb_sim = max(sims) if sims else None

    date_prox = 0.0
    try:
        from datetime import date
        ls, pu = (ctx.get("last_shown") or "")[:10], (video.get("published_at") or "")[:10]
        if ls and pu and abs((date.fromisoformat(ls) - date.fromisoformat(pu)).days) <= 60:
            date_prox = 1.0
    except Exception:  # noqa: BLE001
        pass

    sg = {
        "product_in_body": product_in_body, "hashtag_only": hashtag_only,
        "channel_hit": channel_hit, "legal_hit": legal_hit,
        "copy_sim": round(copy_sim, 3), "landing_hit": bool(landing_hit),
        "thumb_sim": round(thumb_sim, 3) if thumb_sim is not None else None,
        "date_prox": bool(date_prox),
    }
    conf, status = classify(sg)
    matched_by = _matched_by(sg)
    ms = (25 * landing_hit + 25 * product_in_body + 30 * copy_sim
          + 20 * (thumb_sim or 0) + 10 * channel_hit + 5 * legal_hit + 5 * date_prox)
    return {"matching_score": round(min(ms, 100.0), 1),
            "matching_confidence": conf, "match_status": status,
            "matched_by": matched_by, "signals": sg,
            "classification": status}   # classification = match_status(하위호환)


def _matched_by(sg: dict) -> list:
    """매칭 근거 리스트(저장/표시용)."""
    by = []
    if sg.get("landing_hit"):
        by.append("랜딩 URL")
    if sg.get("product_in_body"):
        by.append("상품/브랜드명")
    if (sg.get("copy_sim") or 0) >= 0.3:
        by.append("광고 문구")
    if sg.get("thumb_sim") is not None and sg["thumb_sim"] >= 0.7:
        by.append("썸네일")
    if sg.get("channel_hit"):
        by.append("채널명")
    if sg.get("legal_hit"):
        by.append("법인명")
    if sg.get("seed_channel_hit"):
        by.append("시드 채널")
    if sg.get("hashtag_only"):
        by.append("해시태그")
    return by


def classify(sg: dict) -> tuple:
    """(matching_confidence, match_status) 반환.
    - high  : 랜딩 URL 일치 OR (상품명 본문일치 + 썸네일/문구 강일치)
              OR (seed 광고채널 + 창작물 신호)                              → youtube_ad_matched
    - medium: 상품/브랜드명·문구 일부 일치, 또는 seed 광고채널               → youtube_ad_candidate
    - low   : 계정명/해시태그/법인명만 일치                                  → not_matched
    - none  : 근거 부족                                                      → not_matched
    """
    copy_sim = sg.get("copy_sim") or 0
    thumb = sg.get("thumb_sim")
    strong_creative = (copy_sim >= 0.5) or (thumb is not None and thumb >= 0.85)
    seed = sg.get("seed_channel_hit")
    creative_any = (sg.get("landing_hit") or sg.get("product_in_body")
                    or copy_sim >= 0.3 or (thumb is not None and thumb >= 0.7))
    if sg.get("landing_hit") or (sg.get("product_in_body") and strong_creative) \
            or (thumb is not None and thumb >= 0.9) or (seed and creative_any):
        return "high", "youtube_ad_matched"
    if seed or sg.get("product_in_body") or copy_sim >= 0.3 or (thumb is not None and thumb >= 0.7):
        return "medium", "youtube_ad_candidate"
    if sg.get("hashtag_only") or sg.get("channel_hit") or sg.get("legal_hit"):
        return "low", "not_matched"
    return "none", "not_matched"
