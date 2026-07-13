"""요일별 분할 자동 업데이트 (구글) — 매일 02:00 Windows 작업 스케줄러가 호출.
   메타(daily_group_update.py, 05:00)와 '같은 요일 그룹'을 대상으로 하되, 수집 시간대는
   겹치지 않게 별도 시각(02:00)에 구글(투명성센터)만 크롤 → 매칭/재등급 → demo.db → git push.
   법인명(google_advertiser_name)이 있으면 그걸로 검색, 없으면 디스플레이명으로 검색.
   사용:  python jobs/google_group_update.py            (오늘 요일 그룹)
          python jobs/google_group_update.py --weekday 1 (강제 요일 0=월..6=일)
"""
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import database  # noqa: E402
import config  # noqa: E402
from collectors import google_library_crawler  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
_WD = ["월", "화", "수", "목", "금", "토", "일"]
GOOGLE_LIMIT = 300


def _log(m: str) -> None:
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {m}", flush=True)


def _git_add_new_google_thumbs() -> int:
    """새로 생긴 구글 이미지 썸네일(CR*.png)만 스테이징(신규 미커밋 파일만).
       재촬영으로 수정된 기존 CR 파일은 repo 용량 폭증 방지를 위해 제외."""
    out = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard", "-z", "--", "static/thumbnails/"],
        cwd=str(ROOT), capture_output=True, text=True, timeout=120).stdout
    new = [f for f in out.split("\0")
           if f and f.rsplit("/", 1)[-1].startswith("CR") and f.endswith(".png")]
    for i in range(0, len(new), 500):   # 인자 길이 제한 회피(500개씩 add)
        subprocess.run(["git", "add", "--", *new[i:i + 500]],
                       cwd=str(ROOT), capture_output=True, text=True, timeout=300)
    return len(new)


def _collect_one(display: str, run_start: str) -> dict:
    """구글 브랜드 1개 수집 → {found, new, updated, term}. 예외는 호출부에서 처리."""
    brand = database.get_brand(display) or {}
    bid = brand.get("id") or 0
    term = (brand.get("google_advertiser_name") or "").strip() or display

    gads, glog = google_library_crawler.search_brand(term, limit=GOOGLE_LIMIT, scrolls=25)
    gads = [{**a, "brand_name": display} for a in gads]
    ids = list(dict.fromkeys(a.get("platform_ad_id") for a in gads if a.get("platform_ad_id")))
    before = database.existing_ad_ids(ids)
    database.ingest_ad_library(gads)
    new = sum(1 for i in ids if i not in before)
    database.log_brand_collection(bid, "google", term, "success",
                                  glog["found"], new, len(ids) - new, started=run_start)
    return {"found": glog["found"], "new": new, "updated": len(ids) - new, "term": term}


def main(weekday: int = None) -> None:
    database.init_db()
    run_start = datetime.now(timezone.utc).isoformat()
    wd = weekday if weekday is not None else datetime.now().weekday()
    brands = database.brands_for_weekday(wd)
    groups = database.brand_index_groups()
    idxs = [groups[b]["index"] for b in brands] if brands else []
    rng = f"{min(idxs)}~{max(idxs)}번" if idxs else "-"
    _log(f"=== {_WD[wd]}요일 그룹 구글 수집 시작: {len(brands)}개 ({rng}) ===")

    tot_new = tot_upd = 0
    failed = []
    for i, b in enumerate(brands, 1):
        try:
            r = _collect_one(b, run_start)
            tot_new += r["new"]; tot_upd += r["updated"]
            _log(f"[{i}/{len(brands)}] {b}: '{r['term']}' 발견 {r['found']} 신규 {r['new']}")
        except Exception as e:  # noqa: BLE001
            failed.append(b)
            _log(f"[{i}/{len(brands)}] {b}: 실패 {str(e)[:80]}")

    # 실패 브랜드 같은 날 1회 재시도
    if failed:
        _log(f"재시도 대상 {len(failed)}개: {failed}")
        still = []
        for b in failed:
            try:
                r = _collect_one(b, run_start)
                tot_new += r["new"]; tot_upd += r["updated"]
            except Exception as e:  # noqa: BLE001
                still.append(b)
                _log(f"재시도 실패 {b}: {str(e)[:80]}")
        failed = still

    database.compute_matches()
    database.regrade()
    database.migrate_brands()

    # demo.db + push
    try:
        database.regenerate_demo_db()
    except Exception as e:  # noqa: BLE001
        _log(f"demo.db 실패: {e}")
    if config.ENABLE_AUTO_GIT_PUSH:
        n_thumb = _git_add_new_google_thumbs()   # 새 구글 이미지 썸네일(CR*.png) 반영
        _log(f"신규 구글 썸네일 {n_thumb}개 스테이징")
        msg = f"auto: {_WD[wd]}요일 그룹 구글수집({rng}) {datetime.now():%Y-%m-%d}"
        for cmd in (["git", "add", "sample_data/demo.db"],
                    ["git", "commit", "-m", msg],
                    ["git", "push", "origin", "main"]):
            r = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, timeout=300)
            if r.returncode != 0 and cmd[1] != "commit":
                _log(f"git {cmd[1]}: {r.stderr[:120]}")
    else:
        _log("자동 git push 비활성(ENABLE_AUTO_GIT_PUSH=False) — demo.db 로컬 갱신만")

    _log("=== 구글 자동 업데이트 완료 ===")
    _log(f"오늘 수집 그룹: {_WD[wd]}요일 {rng} · 총 {len(brands)}개 브랜드")
    _log(f"신규 광고 {tot_new} · 갱신 광고 {tot_upd} · "
         f"실패 브랜드 {len(failed)}{(' '+str(failed)) if failed else ''}")


if __name__ == "__main__":
    wd = None
    if "--weekday" in sys.argv:
        try:
            wd = int(sys.argv[sys.argv.index("--weekday") + 1])
        except Exception:  # noqa: BLE001
            wd = None
    main(weekday=wd)
