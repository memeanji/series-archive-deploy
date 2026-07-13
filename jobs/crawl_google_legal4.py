"""사용자 확인 법인명 4차 배치 — 구글 투명성센터 재크롤.
사용:  python jobs/crawl_google_legal4.py
"""
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import database  # noqa: E402
from collectors import google_library_crawler as G  # noqa: E402

GOOGLE_LIMIT = 80

RENAMES = [("비아벨르", "비아벨로"), ("날라브", "닐라브"), ("리얼치메디", "리칼지메디")]

# (브랜드, 법인명, 공유법인 여부)
PAIRS = [
    ("아이하이", "주식회사 비비에스랩", False),
    ("데일리플랜", "그레이더", True),          # 키즈플랜과 공유
    ("티제로", "주식회사 플레노반트", False),
    ("비아벨로", "주식회사 케이빅스", False),
    ("뮤르디", "주식회사 유노바헬스케어", False),
    ("안티칼알파", "주식회사 머스트케이", False),
    ("키즈랩스", "주식회사 넥스트플레이스", False),
    ("데이베리어", "데이베리어", False),
    ("퍼센트솔트", "Ascentone", True),         # 테키라(어센트원)와 공유
    ("힐스뱅크", "RAUM", True),                # 라움 공유
    ("닐라브", "스마트라이프텍 주식회사", False),
    ("유로디에트", "RAUM", True),              # 라움 공유
    ("웰릿", "(주)이삼오구", True),            # 셀라딕스·하우스윗과 공유
    ("리베니프", "주식회사 아이리스브라이트", False),
    ("리더뮨", "주식회사 발전존중", False),
    ("푸드올로지", "(주)어댑트", False),
    ("키즈플랜", "그레이더", True),
    ("유로바디", "HKN Inc", True),             # HKN 공유
    ("루노비전", "주식회사 크롬스", True),      # 니프젠과 공유
    ("리칼지메디", "주식회사 드래프터", True),  # 셀라큐어와 공유
    ("미라클시드니", "주식회사 미라클시드니코리아", False),
    # 추가분(재전송 메시지의 신규 3개)
    ("메라블", "주식회사 고지식바이오", False),
    ("세라블랑", "주식회사 베그리프", False),
    ("리셋80", "HKN Inc", True),               # HKN 공유
]

ALIASES = {
    "RAUM": ["라움", "RAUM"],
    "HKN Inc": ["HKN Inc.", "HKN", "HKN Inc"],
    "그레이더": ["그레이더", "GRADER"],
    "Ascentone": ["Ascentone", "어센트원", "주식회사 어센트원"],
    "데이베리어": ["데이베리어", "주식회사 데이베리어"],
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
    print(f"[{datetime.now():%H:%M}] 4차 법인명 재크롤 — {len(PAIRS)}개 브랜드")
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
    print(f"=== 4차 완료: 총 {grand}건 신규 적재 ===")


if __name__ == "__main__":
    main()
