"""사용자 확인 법인명으로 구글 투명성센터 재크롤(정확한 광고주명 사용).
이전 자동탐색이 '주식회사 {브랜드명}' 엉터리 이름을 써서 0건이던 걸 교정.
사용:  python jobs/crawl_google_legal.py
"""
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import database  # noqa: E402
from collectors import google_library_crawler as G  # noqa: E402

GOOGLE_LIMIT = 80

# (브랜드 표시명, 정확한 법인명/광고주명) — 사용자 제공
PAIRS = [
    ("네리티아", "더젊음마켓"),
    ("바르너", "주식회사 바이트랩"),
    ("셀라딕스", "주식회사 이삼오구"),
    ("EOA", "주식회사 넥스트립"),
    ("솔티스", "신세계바이오 주식회사"),
    ("비렌느", "주식회사 엘에스에스씨"),
    ("메디테라피", "주식회사 메디테라피"),
    ("인버브", "주식회사 어파인"),
    ("니아르", "주식회사 페슬"),
    ("에르고바디", "주식회사 부스트랩"),
]


def _variants(legal: str) -> list[str]:
    """검색 표기 변형: 주식회사 ↔ (주) ↔ 핵심명. 자동완성이 표기에 민감할 수 있어 폴백 제공."""
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
    # 중복 제거(순서 유지)
    return list(dict.fromkeys(v))


def main() -> None:
    database.init_db()
    print(f"[{datetime.now():%H:%M}] 법인명 교정 재크롤 — {len(PAIRS)}개 브랜드")
    grand = 0
    for i, (brand, legal) in enumerate(PAIRS, 1):
        # 1) 정확한 법인명을 brands 에 저장(교정)
        database.add_brand(brand, [], extra={"google_advertiser_name": legal})
        # 2) 법인명(변형 폴백)으로 크롤 → 광고 나오면 중단
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
        # 3) brand_name 을 표시명으로 통일 후 적재
        ads = [{**a, "brand_name": brand} for a in ads]
        saved = database.ingest_ad_library(ads) if ads else 0
        grand += saved
        vids = sum(1 for a in ads if a.get("media_type") == "video")
        print(f"  [{i}/{len(PAIRS)}] {brand} (법인명:{legal}"
              f"{' → 검색:'+used if used and used != legal else ''}): "
              f"{saved}건 적재 / 영상 {vids} "
              f"[발견 {log.get('found',0)}, 텍스트제외 {log.get('excluded_search_text',0)}, "
              f"미디어없음 {log.get('excluded_no_media',0)}]")
    database.compute_matches()
    database.regrade()
    database.migrate_brands()
    print(f"=== 완료: 총 {grand}건 신규 적재 ===")


if __name__ == "__main__":
    main()
