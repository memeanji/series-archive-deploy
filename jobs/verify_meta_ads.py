"""
메타 광고 상세(permalink) 검증 — 카드엔 보여도 상세에서 '광고 라이브러리에 없습니다'가
뜨는 광고를 detail_status='unavailable'로 표시(기본 목록에서 제외).
사용:  python jobs/verify_meta_ads.py [브랜드명]   (인자 없으면 메타 전체)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import database  # noqa: E402
from collectors import meta_library_crawler as M  # noqa: E402

BATCH = 60


def main() -> None:
    database.init_db()
    conn = database.get_conn()
    where = "platform='meta' AND COALESCE(detail_status,'')<>'unavailable'"
    params: tuple = ()
    if len(sys.argv) > 1:
        where += " AND brand_name=?"
        params = (sys.argv[1],)
    # 영상/이미지가 이미 정상 확보된 것 위주로 확인(전수). 미디어 없는 건 어차피 숨김.
    ids = [r[0] for r in conn.execute(
        f"SELECT id FROM ad_library_ads WHERE {where}", params).fetchall()]
    conn.close()
    print(f"=== 메타 상세 검증: {len(ids)}건 ===")
    unavail = 0
    for i in range(0, len(ids), BATCH):
        chunk = ids[i:i + BATCH]
        res = M.verify_available(chunk)
        conn = database.get_conn()
        conn.isolation_level = None
        for aid, status in res.items():
            if status == "unavailable":
                conn.execute("UPDATE ad_library_ads SET detail_status='unavailable' WHERE id=?", (aid,))
                unavail += 1
        conn.close()
        print(f"  {min(i + BATCH, len(ids))}/{len(ids)} 검증 · 누적 unavailable {unavail}")
    print(f"=== 완료: unavailable {unavail}건 제외 처리 ===")


if __name__ == "__main__":
    main()
