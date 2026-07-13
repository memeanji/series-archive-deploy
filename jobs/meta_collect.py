"""Meta 수집 오케스트레이션 — page_id 우선, 없으면 키워드+page_id 후보 추출.
   사용:  python jobs/meta_collect.py "세라블랑"          (해당 브랜드 수집)
          python jobs/meta_collect.py "세라블랑" --keyword (page_id 있어도 키워드로)
"""
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import database  # noqa: E402
from collectors import meta_library_crawler  # noqa: E402


def crawl_brand_meta(display: str, force_keyword: bool = False) -> dict:
    """브랜드 Meta 광고 다중경로 크롤(키워드 + page_id×[all/video/image/FB/IG]) → 합산·중복제거.
       page_id 자동추출 후 광고주 전체를 여러 필터로 긁어 수집률을 높임. 적재/후처리는 안 함.
       반환 {ads, method, page_id, reported}."""
    SB = meta_library_crawler.search_brand
    b = database.get_brand(display) or {}
    pid = (b.get("meta_page_id") or "").strip()
    raw: list = []
    reported = 0
    method = "page_id" if (pid and not force_keyword) else "keyword"

    def _take(r):
        nonlocal reported
        raw.extend(r)
        reported = max(reported, max((a.get("reported_count", 0) for a in r), default=0))

    # 1) 키워드 경로(+page_id 후보 확보) — page_id 미확정일 때
    if not pid or force_keyword:
        for kw in (database.get_brand_keywords(display) or [display]):
            try:
                _take(SB(kw, media_type="all"))
            except Exception as e:  # noqa: BLE001
                print(f"  [meta] kw '{kw}' 실패: {str(e)[:80]}", flush=True)
        cands = Counter(a.get("page_id") for a in raw if (a.get("page_id") or "").strip())
        if cands and not pid:
            pid = cands.most_common(1)[0][0]
            database.set_brand_page_id(display, pid, "candidate")
            print(f"  [meta] page_id 자동추출: {pid}", flush=True)

    # 2) page_id 다중경로(미디어/플랫폼 분할 → 각 뷰 상한 회피, 합산으로 커버리지↑)
    if pid:
        for mt, pf in (("all", ""), ("video", ""), ("image", ""),
                       ("all", "facebook"), ("all", "instagram")):
            try:
                _take(SB("", page_id=pid, media_type=mt, publisher_platform=pf))
            except Exception as e:  # noqa: BLE001
                print(f"  [meta] page_id {mt}/{pf or '-'} 실패: {str(e)[:70]}", flush=True)
        database.set_brand_page_id(display, pid, "confirmed")
        method = "page_id" if (b.get("meta_page_id") and not force_keyword) else "keyword→page_id"
    elif not raw:
        print("  [meta] page_id 자동추출 실패 — 관리화면에서 ID/링크로 찾기 필요", flush=True)

    # 3) ad_id 기준 중복 제거(여러 경로 합산)
    seen, ads = set(), []
    for a in raw:
        k = a.get("platform_ad_id")
        if k and k not in seen:
            seen.add(k)
            a["brand_name"] = display
            ads.append(a)
    if reported:
        database.set_brand_reported(display, reported)
    print(f"  [meta] {display}: 경로합산 {len(raw)} → 고유 {len(ads)} · 라이브러리표기 {reported or '-'}",
          flush=True)
    return {"ads": ads, "method": method, "page_id": pid, "reported": reported}


def collect_meta(display: str, force_keyword: bool = False) -> dict:
    """단일 브랜드 Meta 수집(크롤+적재+후처리). 반환: {method,found,new,updated,page_id,status}."""
    run_start = datetime.now(timezone.utc).isoformat()
    bid = (database.get_brand(display) or {}).get("id") or 0
    cr = crawl_brand_meta(display, force_keyword=force_keyword)
    ads, method = cr["ads"], cr["method"]
    ids = list(dict.fromkeys(a.get("platform_ad_id") for a in ads if a.get("platform_ad_id")))
    before = database.existing_ad_ids(ids)
    database.ingest_ad_library(ads)
    new = sum(1 for i in ids if i not in before)
    updated = len(ids) - new

    database.log_brand_collection(bid, "meta", method, "success", len(ids), new, updated, started=run_start)
    database.compute_matches()
    database.regrade()
    database.migrate_brands()
    database.finalize_meta_video_status(run_start, brands=[display])
    st = database.brand_collection_status(display)
    res = {"brand": display, "method": method, "found": len(ids),
           "new": new, "updated": updated, "page_id": st["page_id"], "status": st["status"]}
    print(f"  [meta] {display}: {method} · 발견 {len(ids)} · 신규 {new} · 갱신 {updated} · 상태 {st['status']}",
          flush=True)
    return res


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    force_kw = "--keyword" in sys.argv
    if not args:
        print("usage: python jobs/meta_collect.py <브랜드> [--keyword]")
        sys.exit(1)
    database.init_db()
    collect_meta(args[0], force_keyword=force_kw)


if __name__ == "__main__":
    main()
