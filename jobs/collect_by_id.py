"""광고 ID 단건 즉시 수집 — Meta Ad Library ?id=<ID> 페이지를 크롤해 DB에 적재.
   ID별 성공/실패·사유·page_id 를 구조화해 'RESULT_JSON:' 로 출력(앱이 파싱).
   사용:  python jobs/collect_by_id.py <id1> <id2> ...
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import database  # noqa: E402
from collectors import meta_library_crawler  # noqa: E402


def main(ids: list) -> None:
    database.init_db()
    ids = [str(i).strip() for i in ids if str(i).strip()]
    before = database.existing_ad_ids(ids)
    results = []
    for aid in ids:
        if aid in before:
            results.append({"id": aid, "ok": True, "reason": "기존 보유", "is_new": False,
                            "advertiser": "", "page_id": ""})
            continue
        try:
            ads = meta_library_crawler.search_brand("", ad_id=aid, retries=2)
        except Exception as e:  # noqa: BLE001
            results.append({"id": aid, "ok": False, "reason": f"네트워크/크롤 오류: {str(e)[:60]}",
                            "is_new": False, "advertiser": "", "page_id": ""})
            continue
        if not ads:
            results.append({"id": aid, "ok": False,
                            "reason": "원본 접근 불가(만료·비공개·삭제 또는 권한 필요)",
                            "is_new": False, "advertiser": "", "page_id": ""})
            continue
        # ?id= 페이지는 추천/연관 광고도 같이 반환 → 입력 ID와 정확히 일치하는 광고만 저장(오염 방지)
        target = [a for a in ads if str(a.get("platform_ad_id")) == aid]
        if not target:
            results.append({"id": aid, "ok": False,
                            "reason": "해당 ID 광고 미확인(만료/비공개 가능) — 연관광고는 저장 안 함",
                            "is_new": False, "advertiser": "",
                            "page_id": ads[0].get("page_id") or ""})
            continue
        a = target[0]
        adv = (a.get("headline") or a.get("advertiser_name") or "(ID수집)").strip() or "(ID수집)"
        a["brand_name"] = a.get("brand_name") or adv
        database.ingest_ad_library([a])   # 입력 ID 1건만 적재
        results.append({"id": aid, "ok": True, "is_new": True, "reason": "신규 수집",
                        "advertiser": adv, "page_id": a.get("page_id") or "",
                        "media": a.get("media_type") or ""})
    if any(r["is_new"] for r in results):
        database.compute_matches()
        database.regrade()
        database.migrate_brands()
    print("RESULT_JSON:" + json.dumps(results, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: python jobs/collect_by_id.py <id> [<id> ...]")
        sys.exit(1)
    main(sys.argv[1:])
