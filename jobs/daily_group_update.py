"""요일별 분할 자동 업데이트 — 매일 05:00 Windows 작업 스케줄러가 호출.
   오늘 요일 그룹(약 12~15개 브랜드)만 Meta 수집(page_id 우선/keyword 폴백) → 상태확정 →
   실패 재시도 → demo.db(WAL 체크포인트) → git push. 출근 전(9:30) 최신 상태 확인이 목표.
   사용:  python jobs/daily_group_update.py            (오늘 요일 그룹)
          python jobs/daily_group_update.py --weekday 1 (강제 요일 0=월..6=일)
"""
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import database  # noqa: E402
import config  # noqa: E402
from jobs.meta_collect import crawl_brand_meta  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
_WD = ["월", "화", "수", "목", "금", "토", "일"]


def _log(m: str) -> None:
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {m}", flush=True)


def _collect_one(b: str, run_start: str) -> dict:
    """브랜드 1개 수집 → {new, updated, found, method}. 예외는 호출부에서 처리."""
    cr = crawl_brand_meta(b)
    ads = cr["ads"]
    ids = list(dict.fromkeys(a.get("platform_ad_id") for a in ads if a.get("platform_ad_id")))
    before = database.existing_ad_ids(ids)
    database.ingest_ad_library(ads)
    new = sum(1 for i in ids if i not in before)
    bid = (database.get_brand(b) or {}).get("id") or 0
    database.log_brand_collection(bid, "meta", cr["method"], "success",
                                  len(ids), new, len(ids) - new, started=run_start)
    return {"found": len(ids), "new": new, "updated": len(ids) - new, "method": cr["method"]}


def main(weekday: int = None) -> None:
    database.init_db()
    run_start = datetime.now(timezone.utc).isoformat()
    wd = weekday if weekday is not None else datetime.now().weekday()
    brands = database.brands_for_weekday(wd)
    groups = database.brand_index_groups()
    idxs = [groups[b]["index"] for b in brands] if brands else []
    rng = f"{min(idxs)}~{max(idxs)}번" if idxs else "-"
    _log(f"=== {_WD[wd]}요일 그룹 수집 시작: {len(brands)}개 ({rng}) ===")

    tot_new = tot_upd = 0
    failed = []
    for i, b in enumerate(brands, 1):
        try:
            r = _collect_one(b, run_start)
            tot_new += r["new"]; tot_upd += r["updated"]
            _log(f"[{i}/{len(brands)}] {b}: {r['method']} 발견 {r['found']} 신규 {r['new']}")
        except Exception as e:  # noqa: BLE001
            failed.append(b)
            _log(f"[{i}/{len(brands)}] {b}: 실패 {str(e)[:80]}")

    # 실패 브랜드 같은 날 1회 재시도
    retried_ok = []
    if failed:
        _log(f"재시도 대상 {len(failed)}개: {failed}")
        still = []
        for b in failed:
            try:
                r = _collect_one(b, run_start)
                tot_new += r["new"]; tot_upd += r["updated"]
                retried_ok.append(b)
            except Exception as e:  # noqa: BLE001
                still.append(b)
                _log(f"재시도 실패 {b}: {str(e)[:80]}")
        failed = still

    # 상태 확정(오늘 그룹만) + 만료/비공개 집계
    vs = {}
    try:
        vs = database.finalize_meta_video_status(run_start, brands=brands)
    except Exception as e:  # noqa: BLE001
        _log(f"상태확정 실패: {e}")
    database.compute_matches()
    database.regrade()
    database.migrate_brands()

    # demo.db + push
    try:
        database.regenerate_demo_db()
    except Exception as e:  # noqa: BLE001
        _log(f"demo.db 실패: {e}")
    if config.ENABLE_AUTO_GIT_PUSH:
        msg = f"auto: {_WD[wd]}요일 그룹 수집({rng}) {datetime.now():%Y-%m-%d}"
        for cmd in (["git", "add", "sample_data/demo.db"],
                    ["git", "add", "static/thumbnails/m_*.jpg"],
                    ["git", "commit", "-m", msg],
                    ["git", "push", "origin", "main"]):
            r = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, timeout=300)
            if r.returncode != 0 and cmd[1] != "commit":
                _log(f"git {cmd[1]}: {r.stderr[:120]}")
    else:
        _log("자동 git push 비활성(ENABLE_AUTO_GIT_PUSH=False) — demo.db 로컬 갱신만")

    gone = (vs.get("private_or_deleted", 0) + vs.get("expired_url", 0))
    _log("=== 자동 업데이트 완료 ===")
    _log(f"오늘 수집 그룹: {_WD[wd]}요일 {rng} · 총 {len(brands)}개 브랜드")
    _log(f"신규 광고 {tot_new} · 갱신 광고 {tot_upd} · 만료/비공개 {gone} · "
         f"실패 브랜드 {len(failed)}{(' '+str(failed)) if failed else ''}")


if __name__ == "__main__":
    wd = None
    if "--weekday" in sys.argv:
        try:
            wd = int(sys.argv[sys.argv.index("--weekday") + 1])
        except Exception:  # noqa: BLE001
            wd = None
    main(weekday=wd)
