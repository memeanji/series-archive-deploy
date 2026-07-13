"""Google Transparency Center 광고 → 실제 브랜드 매칭.
법인(advertiser/company)과 브랜드를 분리하고, 랜딩URL/문구/제품키워드/브랜드alias 우선순위로 매칭.
법인명만 일치하면 '브랜드 미확정(company_only)'으로 두고 리뷰 영역에서 수동 지정.
"""
from __future__ import annotations

import json
import re

_AR_RE = re.compile(r"/advertiser/(AR[0-9A-Za-z]+)")


def advertiser_id(ad: dict) -> str:
    """transparency_url/original_ad_url 에서 광고주 계정 ID(AR...) 추출 — 브랜드 구분 핵심키."""
    for k in ("transparency_url", "original_ad_url"):
        m = _AR_RE.search(str(ad.get(k) or ""))
        if m:
            return m.group(1)
    return ""


def _loads(v):
    try:
        x = json.loads(v) if v else []
        return x if isinstance(x, list) else []
    except Exception:  # noqa: BLE001
        return []


def build_registry(conn) -> dict:
    """brands → {brand: {aliases:[..lower], keywords:[..], domains:[..], company:str}}.
    company(법인) → [brands] 매핑도 포함(여러 브랜드가 한 법인 공유)."""
    rows = conn.execute(
        "SELECT display_name, search_keywords, official_domain, google_advertiser_name, "
        "brand_aliases, product_keywords, brand_domains FROM brands "
        "WHERE COALESCE(is_active,1)=1").fetchall()
    reg, by_company = {}, {}
    for r in rows:
        b = r["display_name"]
        aliases = {b.lower()} | {a.lower() for a in _loads(r["brand_aliases"]) if a}
        kws = {k.lower() for k in (_loads(r["product_keywords"]) + _loads(r["search_keywords"])) if k}
        domains = set()
        for d in ([r["official_domain"]] + _loads(r["brand_domains"])):
            d = (d or "").strip().lower().replace("https://", "").replace("http://", "").split("/")[0]
            if d:
                domains.add(d)
        company = (r["google_advertiser_name"] or "").strip().lower()
        reg[b] = {"aliases": aliases, "keywords": kws, "domains": domains, "company": company}
        if company:
            by_company.setdefault(company, []).append(b)
    return {"brands": reg, "by_company": by_company}


def load_rules(conn) -> list:
    return [dict(r) for r in conn.execute(
        "SELECT pattern_type, pattern, brand_name FROM brand_match_rules").fetchall()]


def match_ad(ad: dict, registry: dict, rules: list) -> dict:
    """반환 {brand, method, confidence, status}.
    status: confirmed / estimated / company_only / unmatched."""
    landing = " ".join(str(ad.get(k) or "") for k in ("landing_url", "final_url", "media_url")).lower()
    # 사용 가능한 모든 텍스트 필드(파일명/경로 포함)에서 브랜드 힌트 탐색
    # brand_name(크롤 추정값)은 순환 확정 방지 위해 제외 — 실제 콘텐츠 필드만
    text = " ".join(str(ad.get(k) or "") for k in
                    ("ad_title", "ad_copy", "advertiser_name", "transparency_url",
                     "original_ad_url", "thumbnail_url", "local_thumbnail_path")).lower()
    blob = landing + " " + text
    adv = (ad.get("advertiser_name") or "").strip().lower()
    arid = advertiser_id(ad)
    brands = registry["brands"]

    # 0-A) 제외 학습 규칙 — 이 광고주(AR ID)는 레퍼런스 제외(보험·대출 등 안 들고옴)
    if arid:
        for rule in rules:
            if rule["pattern_type"] == "exclude_advertiser_id" and (rule["pattern"] or "").lower() == arid.lower():
                return {"brand": None, "method": "manual", "confidence": "none",
                        "status": "reference_excluded", "reason": "레퍼런스 제외(학습: 같은 광고주)"}
    # 0) 학습 규칙(수동 지정으로 등록된 advertiser_id/도메인/키워드) — 최우선 자동매칭
    for rule in rules:
        pat = (rule["pattern"] or "").lower()
        if not pat or rule["brand_name"] not in brands:
            continue
        pt = rule["pattern_type"]
        hit = (pt == "advertiser_id" and arid and pat == arid.lower()) \
            or (pt == "domain" and pat in landing) \
            or (pt == "keyword" and pat in blob)
        if hit:
            return {"brand": rule["brand_name"], "method": f"learned_{pt}", "confidence": "high",
                    "status": "confirmed", "reason": f"{pt} 학습규칙: {rule['pattern']}"}
    # 1) 브랜드 공식 도메인 일치(랜딩/모든 URL·경로)
    for b, info in brands.items():
        hit = next((d for d in info["domains"] if d and (d in landing or d in blob)), None)
        if hit:
            return {"brand": b, "method": "domain_match", "confidence": "high",
                    "status": "confirmed", "reason": f"도메인 '{hit}' 발견"}
    # 2) 브랜드명·alias 직접 포함(콘텐츠 텍스트)
    for b, info in brands.items():
        hit = next((a for a in info["aliases"] if a and a in text), None)
        if hit:
            return {"brand": b, "method": "brand_name_or_alias_match", "confidence": "high",
                    "status": "confirmed", "reason": f"브랜드명/alias '{hit}' 발견"}
    # 3) 제품/라인/키워드
    for b, info in brands.items():
        hit = next((k for k in info["keywords"] if k and k in blob), None)
        if hit:
            return {"brand": b, "method": "product_keyword_match", "confidence": "medium",
                    "status": "estimated", "reason": f"제품 키워드 '{hit}' 발견"}
    # 4) 법인(광고주)만 일치
    if adv:
        cands = registry["by_company"].get(adv, [])
        if len(cands) == 1:
            return {"brand": cands[0], "method": "company_single", "confidence": "low",
                    "status": "confirmed", "reason": f"법인 '{adv}' = 단일 브랜드"}
        if len(cands) >= 2:
            return {"brand": None, "method": "company_only", "confidence": "low",
                    "status": "company_only",
                    "reason": f"법인 '{adv}'만 발견(브랜드 {len(cands)}개 공유) · AR:{arid or '-'}"}
    return {"brand": None, "method": "unmatched", "confidence": "none", "status": "unmatched",
            "reason": "브랜드명/도메인/법인 힌트 없음"}
