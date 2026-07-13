"""
대량 브랜드 등록 + 메타 우선 수집(A안). 구글은 이후 별도 실행.
- 표 브랜드: 키워드/법인명/도메인 메타데이터 포함
- 플랫 리스트: 이름 기반(키워드=이름)
메타 검색은 브랜드명으로 1회(속도). 광고 0건 브랜드만 수집(기존 보유 브랜드는 건너뜀).
사용:  python jobs/bulk_register_meta.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import database  # noqa: E402
from collectors import meta_library_crawler as M  # noqa: E402

# (display, [keywords], legal_name, domain)
TABLE = [
    ("안티칼알파", ["안티칼알파", "안티칼 알파", "Antical", "Antical Alpha", "알파올라민", "머스트케이"], "주식회사 머스트케이", "antical.co.kr"),
    ("셀라딕스", ["셀라딕스", "Celladix", "피부백과"], "주식회사 이삼오구", "celladix.co.kr"),
    ("더스크랙", ["더스크랙", "DUSKRACK", "근성장연구소"], "주식회사 더스크랙", "duskrack.kr"),
    ("메디테라피", ["메디테라피", "Meditherapy"], "주식회사 메디테라피", "meditherapy.co.kr"),
    ("솔티스", ["솔티스", "Soltice", "신세계바이오"], "신세계바이오 주식회사", "soltice.kr"),
    ("푸드올로지", ["푸드올로지", "Foodology", "어댑트", "ADAPT"], "주식회사 어댑트", "food-ology.co.kr"),
    ("리셋80", ["리셋80", "리셋 80", "reset80", "HKN"], "HKN", "reset80.co.kr"),
    ("웰릿", ["웰릿", "Wellit"], "주식회사 이삼오구", "wellit.co.kr"),
    ("데일리플랜", ["데일리플랜", "데이피크", "데이하이", "데이퓨어"], "", "dailyplan.kr"),
    ("루노비전", ["루노비전", "LunorVision", "Klarherz", "크롬스"], "주식회사 크롬스", ""),
    ("에르고바디", ["에르고바디", "ergobody"], "", "ergobody.co.kr"),
]

FLAT = """다한방 치젠트리 디디 닥터메카닉 아르퓨레 니프젠 아타카 테카라 셀큐어 소우코우
닥터멜락신 만슬리릭 무궁핏 하우스윗 유로키드 히어젠 이어셀 키즈플랜 키즈랩스 데이베리어
비렌느 닥터클린트 허밍테라피 닥터랩젯 겟버니스 메디솜 울록담 리더뮨 아이하이 니아르
메라블 멘티오스 네리티아 미라클시드니 EOA 돔 바르너 리디에뜨 티제로 비아벨르
아지핀 슈비컷 퓨드리 라움 퍼센트솔트 날라브 디라셀 힐스뱅크 휴그랩 유로바디
유로디에트 리베니프 샤르드 인버브 리얼치메디""".split()


def main() -> None:
    database.init_db()
    # 1) 등록(upsert)
    seen = set()
    for name, kws, legal, dom in TABLE:
        database.add_brand(name, kws, dom, extra={"google_advertiser_name": legal})
        seen.add(name)
    for name in FLAT:
        if name in seen:
            continue
        seen.add(name)
        database.add_brand(name, [name], extra={"google_advertiser_name": f"주식회사 {name}"})
    print(f"=== 등록 완료: {len(seen)}개 브랜드 ===")

    # 2) 메타 우선 수집 — 광고 0건 브랜드만(기존 보유 브랜드 건너뜀)
    conn = database.get_conn()
    targets = [r[0] for r in conn.execute(
        "SELECT display_name FROM brands WHERE display_name IN (%s)" %
        ",".join("?" * len(seen)), tuple(seen)).fetchall()]
    zero = [b for b in targets if conn.execute(
        "SELECT COUNT(*) FROM ad_library_ads WHERE brand_name=?", (b,)).fetchone()[0] == 0]
    conn.close()
    print(f"=== 메타 수집 대상(광고 0건): {len(zero)}개 ===")
    total = 0
    for i, b in enumerate(zero, 1):
        try:
            ads = [{**a, "brand_name": b} for a in M.search_brand(b)]
            saved = database.ingest_ad_library(ads)
            total += saved
            print(f"  [{i}/{len(zero)}] {b}: {saved}건")
        except Exception as e:  # noqa: BLE001
            print(f"  [{i}/{len(zero)}] {b}: 실패 {str(e)[:60]}")
    database.compute_matches()
    database.regrade()
    database.migrate_brands()
    print(f"=== 메타 수집 완료: 총 {total}건 적재 ===")


if __name__ == "__main__":
    main()
