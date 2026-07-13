"""
매일 자동 수집 파이프라인 (Windows 작업 스케줄러가 매일 05:00 호출).
[모든 브랜드 메타+구글 크롤 → 매칭/재등급 → demo.db 갱신 → git 커밋·푸시(Cloud 반영)]
매일 재크롤로 만료되는 fbcdn 영상/썸네일 서명 URL도 갱신됨.
사용:  python jobs/scheduled_update.py
"""
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import database  # noqa: E402
from jobs.crawl_brand import crawl_one  # noqa: E402
from services import notify  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def _log(msg: str) -> None:
    print(f"[{datetime.now():%Y-%m-%d %H:%M}] {msg}", flush=True)


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


def _ad_counts() -> dict:
    """브랜드별 ad_library_ads 건수 {brand_name: count}."""
    conn = database.get_conn()
    rows = conn.execute(
        "SELECT brand_name, COUNT(*) FROM ad_library_ads GROUP BY brand_name").fetchall()
    conn.close()
    return {r[0]: r[1] for r in rows}


def main() -> None:
    from datetime import timezone
    database.init_db()
    run_start = datetime.now(timezone.utc).isoformat()   # 갱신여부 판정 기준
    conn = database.get_conn()
    brands = [r[0] for r in conn.execute(
        "SELECT display_name FROM brands WHERE COALESCE(is_active,1)=1 ORDER BY display_name").fetchall()]
    conn.close()
    # 만료/재생불가 영상이 있는 브랜드를 앞으로 — 우선 갱신
    try:
        priority = database.expired_video_brands()
        if priority:
            pset = set(priority)
            brands = priority + [b for b in brands if b not in pset]
            _log(f"우선 갱신 대상(만료 영상 보유) {len(priority)}개 브랜드 먼저 처리")
    except Exception as e:  # noqa: BLE001
        _log(f"우선순위 계산 건너뜀: {e}")
    before_counts = _ad_counts()   # 크롤 전 브랜드별 광고 건수(0→N 감지용)
    _log(f"=== 매일 수집 시작: {len(brands)}개 브랜드 ===")
    total = 0
    for i, b in enumerate(brands, 1):
        try:
            r = crawl_one(b)
            total += r["ad"]
            _log(f"[{i}/{len(brands)}] {b}: 광고 {r['ad']} · 소셜 {r['social']}")
        except Exception as e:  # noqa: BLE001
            _log(f"[{i}/{len(brands)}] {b}: 실패 {str(e)[:80]}")
    database.compute_matches()
    database.regrade()
    database.migrate_brands()

    # 신규 광고 감지: 크롤 전 0건이던 브랜드가 이번에 광고가 잡히면 알림
    after_counts = _ad_counts()
    newly = sorted(
        b for b, n in after_counts.items()
        if n > 0 and before_counts.get(b, 0) == 0)
    if newly:
        detail = ", ".join(f"{b}({after_counts[b]}건)" for b in newly)
        _log(f"🆕 신규 광고 감지: {detail}")
        sent = notify.slack(
            f"🆕 *Series Archive* 신규 광고 감지\n"
            f"그동안 광고가 없던 브랜드에서 처음으로 광고가 잡혔어요: {detail}")
        _log(f"Slack 알림 {'전송' if sent else '건너뜀(웹훅 미설정/실패)'}")

    # 크롤 후 Meta 영상 재생상태 확정(ok/expired_url/private_or_deleted/unavailable)
    try:
        vs = database.finalize_meta_video_status(run_start)
        _log(f"영상 상태: {vs}")
    except Exception as e:  # noqa: BLE001
        _log(f"영상 상태 확정 실패: {e}")
    _log(f"크롤 완료 · 누적 적재 {total}")

    # demo.db 갱신(WAL 체크포인트 + 계정 제거 + VACUUM)
    try:
        database.regenerate_demo_db()
        _log("demo.db 갱신 완료")
    except Exception as e:  # noqa: BLE001
        _log(f"demo.db 갱신 실패: {e}")

    # git 커밋·푸시(Cloud 자동 반영)
    n_thumb = _git_add_new_google_thumbs()   # 새 구글 이미지 썸네일(CR*.png) 반영
    _log(f"신규 구글 썸네일 {n_thumb}개 스테이징")
    msg = f"auto: 매일 수집 갱신 {datetime.now():%Y-%m-%d}"
    for cmd in (["git", "add", "sample_data/demo.db"],
                ["git", "add", "static/thumbnails/m_*.jpg"],   # 새 메타 썸네일도 클라우드 반영
                ["git", "add", "static/thumbnails/rep_tt_*.png"],
                ["git", "commit", "-m", msg],
                ["git", "push", "origin", "main"]):
        try:
            subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, timeout=120)
        except Exception as e:  # noqa: BLE001
            _log(f"git 실패({cmd[1]}): {e}")
    _log("=== 완료(배포 푸시) ===")


if __name__ == "__main__":
    main()
