"""사용자 확인 법인명 3차 배치 — 구글 투명성센터 재크롤.
공유법인 다수(라움 3개 등) → 광고 ID 꼬리표로 분리 보관.
사용:  python jobs/crawl_google_legal3.py
"""
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import database  # noqa: E402
from collectors import google_library_crawler as G  # noqa: E402

GOOGLE_LIMIT = 80

# 기존 빈 브랜드(철자) → 사용자 표기
RENAMES = [("슈비컷", "쥬비컷"), ("멘티오스", "덴티오스"), ("겟버니스", "겟비너스")]

# (브랜드, 법인명, 공유법인 여부) — 니아르(1차완료)·바르너 제외
PAIRS = [
    ("닥터클린트", "신세계바이오 주식회사", True),   # 솔티스(1차)와 동일 법인
    ("유로키드", "라움", True),                      # 라움 3형제
    ("덴티오스", "라움", True),
    ("디라셀", "라움", True),
    ("쥬비컷", "주식회사 엘에스에스씨", True),        # 비렌느·치젠트리와 동일
    ("닥터블릿", "닥터블릿헬스케어㈜", False),
    ("이어셀", "Baseglow Inc.", False),              # 별칭 이어리피트
    ("히어젠", "Workbuilder", False),
    ("허밍테라피", "바이탈스코프 주식회사", False),
    ("겟비너스", "겟비너스", False),
    ("메디홉", "주식회사 온더웨이브", False),
    ("테키라", "주식회사 어센트원", False),
    ("하우스윗", "(주)이삼오구", True),              # 셀라딕스와 동일 법인
    ("다한방", "HKN Inc", True),                     # 무궁핏과 동일 법인
    ("리디잇", "주식회사 그로우팩토리", False),
]

# 검색 표기가 까다로운 법인명은 명시(자동완성이 표기에 민감)
ALIASES = {
    "라움": ["라움", "RAUM"],
    "HKN Inc": ["HKN Inc.", "HKN", "HKN Inc"],
    "Baseglow Inc.": ["Baseglow Inc.", "Baseglow"],
    "닥터블릿헬스케어㈜": ["닥터블릿헬스케어㈜", "닥터블릿헬스케어", "주식회사 닥터블릿헬스케어"],
    "겟비너스": ["겟비너스", "주식회사 겟비너스"],
    "Workbuilder": ["Workbuilder", "워크빌더"],
}


def _rename_brand(old: str, new: str) -> None:
    conn = database.get_conn()
    has_old = conn.execute("SELECT 1 FROM brands WHERE display_name=?", (old,)).fetchone()
    has_new = conn.execute("SELECT 1 FROM brands WHERE display_name=?", (new,)).fetchone()
    if has_old and not has_new:
        conn.execute("UPDATE brands SET display_name=? WHERE display_name=?", (new, old))
    elif has_old and has_new:
        conn.execute("DELETE FROM brands WHERE display_name=?", (old,))
    conn.execute("UPDATE ad_library_ads SET brand_name=? WHERE brand_name=?", (new, old))
    conn.execute("UPDATE social_videos SET brand_name=? WHERE brand_name=?", (new, old))
    conn.commit()
    conn.close()


def _variants(legal: str) -> list[str]:
    if legal in ALIASES:
        return ALIASES[legal]
    v = [legal]
    if legal.startswith("주식회사 "):
        v += [f"(주){legal[5:]}", legal[5:]]
    elif legal.endswith(" 주식회사"):
        core = legal[:-5]; v += [f"(주){core}", core]
    elif legal.startswith("(주)"):
        v += [f"주식회사 {legal[3:]}", legal[3:]]
    elif legal.endswith("㈜"):
        core = legal[:-1]; v += [core, f"주식회사 {core}"]
    return list(dict.fromkeys(v))


def main() -> None:
    database.init_db()
    print(f"[{datetime.now():%H:%M}] 3차 법인명 재크롤 — {len(PAIRS)}개 브랜드")
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
        fixed = []
        for a in ads:
            a = {**a, "brand_name": brand}
            if shared:
                a["platform_ad_id"] = f"{a.get('platform_ad_id') or ''}__{brand}"
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
    print(f"=== 3차 완료: 총 {grand}건 신규 적재 ===")


if __name__ == "__main__":
    main()
