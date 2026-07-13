"""
광고 0건 브랜드를 '법인명'으로 재수집.
법인명 추정: watchlist 모회사 → "주식회사 {모회사}", 없으면 "주식회사 {브랜드}".
(법인명은 브랜드명과 다를 수 있어 추정 — 결과로 검증)
사용:  python jobs/recollect_empty.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import database  # noqa: E402
from jobs.crawl_brand import crawl_one  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def _parent_map() -> dict:
    try:
        wl = json.loads((ROOT / "data" / "watchlist.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}
    m = {}
    for parent, subs in (wl.get("groups") or {}).items():
        if parent.startswith("_"):
            continue
        for s in subs:
            if isinstance(s, dict) and s.get("name"):
                m[s["name"]] = parent
    return m


def main() -> None:
    database.init_db()
    pmap = _parent_map()
    empty = [b["name"] for b in database.brand_counts() if b["ad"] == 0]
    print(f"=== 광고 0건 브랜드 {len(empty)}개 재수집(법인명 추정) ===")
    for name in empty:
        br = database.get_brand(name) or {}
        gadv = (br.get("google_advertiser_name") or "").strip()
        if not gadv:
            parent = pmap.get(name, name)
            gadv = f"주식회사 {parent}"
            database.add_brand(name, [], extra={"google_advertiser_name": gadv})
        print(f"\n--- {name}  (구글 법인명: {gadv}) ---")
        r = crawl_one(name)
        print(f"  → 광고 {r['ad']}건 적재")
    m = database.compute_matches()
    g = database.regrade()
    database.migrate_brands()
    print(f"=== 완료: 매칭 {m} · 재등급 {g} ===")


if __name__ == "__main__":
    main()
