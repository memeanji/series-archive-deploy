"""
수집 공통 실행 로직.
collect → upsert(ads) → snapshot(ad_snapshots) → 점수 재계산 → 로그.
collect_all / collect_meta / collect_tiktok 가 공유한다.
"""
from __future__ import annotations

import sys
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services import scoring  # noqa: E402
from services.supabase_client import get_store  # noqa: E402


def _now():
    return datetime.now(timezone.utc).isoformat()


def run_collectors(collectors: list[tuple[str, object]]) -> list[dict]:
    """collectors: [(name, module_with_collect()), ...] → 저장된 ad dict 목록 반환."""
    store = get_store()
    saved: list[dict] = []

    for name, mod in collectors:
        started = _now()
        try:
            ads = mod.collect()
            for ad in ads:
                ad_id = store.upsert_ad(ad)
                store.insert_snapshot(ad_id, {
                    "snapshot_date": date.today().isoformat(),
                    "status": ad.get("status"),
                    "views": ad.get("views"), "likes": ad.get("likes"),
                    "comments": ad.get("comments"), "shares": ad.get("shares"),
                    "raw_data": ad.get("raw_data"),
                })
                ad["id"] = ad_id
                saved.append(ad)
            store.log_collection(
                platform=getattr(mod, "PLATFORM", name), collector_name=name,
                status="success", collected_count=len(ads),
                error_message=None, started_at=started, finished_at=_now(),
            )
            print(f"  [{name}] 저장 {len(ads)}건")
        except Exception as e:  # noqa: BLE001
            store.log_collection(
                platform=getattr(mod, "PLATFORM", name), collector_name=name,
                status="error", collected_count=0,
                error_message=str(e), started_at=started, finished_at=_now(),
            )
            print(f"  [{name}] 오류: {e}")

    recompute_scores(store)
    return saved


def recompute_scores(store=None) -> int:
    """전체 ads 를 다시 읽어 Reference Score 재계산(전역 통계 기반)."""
    store = store or get_store()
    ads = store.fetch_all_ads()
    ctx = scoring.build_context(ads)
    for ad in ads:
        sc = scoring.compute_reference_score(ad, ctx)
        store.update_ad_score(ad["id"], sc)
    print(f"  [score] {len(ads)}건 재계산 완료")
    return len(ads)
