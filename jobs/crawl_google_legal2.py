"""사용자 확인 법인명 2차 배치 — 구글 투명성센터 재크롤.
- 철자 통합(기존 브랜드명을 사용자 표기로 rename + 기존 광고/소셜 데이터 이동)
- 공유 법인(여러 브랜드가 같은 법인)은 광고 ID에 브랜드 꼬리표를 붙여 서로 안 덮어쓰게
사용:  python jobs/crawl_google_legal2.py
"""
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import database  # noqa: E402
from collectors import google_library_crawler as G  # noqa: E402

GOOGLE_LIMIT = 80

# 기존 DB 철자 → 사용자 표기 (데이터 합치기)
RENAMES = [("울록담", "올록담"), ("아타카", "아타가"),
           ("셀큐어", "셀라큐어"), ("만슬리릭", "먼슬리픽")]

# (브랜드 표시명, 정확한 법인명, 공유법인 여부)
# shared=True → 이미 다른 브랜드가 같은 법인으로 크롤됨 → ID 꼬리표로 분리 보관
PAIRS = [
    ("치젠트리", "주식회사 엘에스에스씨", True),    # 비렌느(1차)와 동일 법인
    ("닥터메카닉", "라이징프로덕트", False),
    ("올록담", "주식회사 삼신", False),
    ("아타가", "주식회사 네이머즈", False),
    ("아르퓨레", "주식회사 올곧은무역", False),
    ("닥터멜락신", "주식회사 브랜드501", False),
    ("니프젠", "주식회사 크롬스", False),
    ("셀라큐어", "주식회사 드래프터", False),
    ("소우코우", "(주)한국현삼생활건강", False),
    ("무궁핏", "HKN Inc.", False),
    ("먼슬리픽", "주식회사 바이트랩", True),         # 바르너(1차)와 동일 법인
]


def _rename_brand(old: str, new: str) -> None:
    """기존 브랜드명을 새 표기로 통합(브랜드/광고/소셜 모두 이동). 대상 비어있어도 안전."""
    conn = database.get_conn()
    has_old = conn.execute("SELECT 1 FROM brands WHERE display_name=?", (old,)).fetchone()
    has_new = conn.execute("SELECT 1 FROM brands WHERE display_name=?", (new,)).fetchone()
    if has_old and not has_new:
        conn.execute("UPDATE brands SET display_name=? WHERE display_name=?", (new, old))
    elif has_old and has_new:
        # 둘 다 있으면 old 의 키워드만 합치고 old 행 제거(중복 방지)
        conn.execute("DELETE FROM brands WHERE display_name=?", (old,))
    conn.execute("UPDATE ad_library_ads SET brand_name=? WHERE brand_name=?", (new, old))
    conn.execute("UPDATE social_videos SET brand_name=? WHERE brand_name=?", (new, old))
    conn.commit()
    conn.close()


def _variants(legal: str) -> list[str]:
    v = [legal]
    if legal.startswith("주식회사 "):
        core = legal[len("주식회사 "):]
        v += [f"(주){core}", core]
    elif legal.endswith(" 주식회사"):
        core = legal[:-len(" 주식회사")]
        v += [f"(주){core}", core]
    elif legal.startswith("(주)"):
        core = legal[len("(주)"):]
        v += [f"주식회사 {core}", core]
    elif legal.endswith(" Inc."):
        v += [legal[:-len(" Inc.")]]
    return list(dict.fromkeys(v))


def main() -> None:
    database.init_db()
    print(f"[{datetime.now():%H:%M}] 2차 법인명 재크롤 — {len(PAIRS)}개 브랜드")
    for old, new in RENAMES:
        _rename_brand(old, new)
        print(f"  [rename] {old} → {new}")
    grand = 0
    for i, (brand, legal, shared) in enumerate(PAIRS, 1):
        database.add_brand(brand, [], extra={"google_advertiser_name": legal})
        used, ads, log = "", [], {}
        for term in _variants(legal):
            try:
                ads, log = G.search_brand(term, limit=GOOGLE_LIMIT)
            except Exception as e:  # noqa: BLE001
                print(f"  [{i}/{len(PAIRS)}] {brand} «{term}»: 크롤오류 {str(e)[:60]}")
                ads, log = [], {}
            if ads:
                used = term
                break
        # 표시명 통일 + 공유법인이면 ID 꼬리표로 분리 보관(덮어쓰기 방지)
        fixed = []
        for a in ads:
            a = {**a, "brand_name": brand}
            if shared:
                cid = a.get("platform_ad_id") or ""
                a["platform_ad_id"] = f"{cid}__{brand}"
            fixed.append(a)
        saved = database.ingest_ad_library(fixed) if fixed else 0
        grand += saved
        vids = sum(1 for a in fixed if a.get("media_type") == "video")
        tag = " [공유법인·꼬리표분리]" if shared else ""
        print(f"  [{i}/{len(PAIRS)}] {brand} (법인:{legal}"
              f"{' → 검색:'+used if used and used != legal else ''}): "
              f"{saved}건 / 영상 {vids}{tag} "
              f"[발견 {log.get('found',0)}, 미디어없음 {log.get('excluded_no_media',0)}]")
    database.compute_matches()
    database.regrade()
    database.migrate_brands()
    print(f"=== 2차 완료: 총 {grand}건 신규 적재 ===")


if __name__ == "__main__":
    main()
