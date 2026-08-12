"""
매일 가벼운 조회수 스냅샷 — 구글(유튜브 연결) 광고의 YouTube 공개 지표를 받아
현재값 갱신 + 일자별 스냅샷 저장(조회수 추이 그래프용). YouTube API 무료 quota 내(수 유닛).
사용:  python jobs/snapshot_views.py
"""
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import database  # noqa: E402
import services.youtube as YT  # noqa: E402


def main() -> None:
    database.init_db()
    if not YT.is_enabled():
        print("YOUTUBE_API_KEY 없음 — 스냅샷 생략")
        return
    conn = database.get_conn()
    rows = [dict(r) for r in conn.execute(
        "SELECT id, video_url FROM ad_library_ads "
        "WHERE platform='google' AND video_url LIKE '%youtube%'").fetchall()]
    conn.close()
    id2ad: dict = {}
    for r in rows:
        vid = YT.extract_video_id(r["video_url"])
        if vid:
            id2ad.setdefault(vid, []).append(r["id"])
    vids = list(id2ad.keys())
    print(f"[{datetime.now():%Y-%m-%d %H:%M}] 조회수 스냅샷: 영상 {len(vids)}개")
    # 저장 규칙(2026-08-11 변경):
    #   · 최신 조회수 = 영상(social_id=YouTube video_id) 단위로 video_view_state 에 **매번 UPDATE**
    #     + 카드/상세가 바로 읽는 ad_library_ads.yt_* 도 함께 갱신(기존 UI 호환).
    #   · 추이 스냅샷 = **하루 1회만** 저장(같은 날 재실행해도 덮어쓰지 않음 → 과거 데이터 보존).
    n_state = n_snap = n_skip = 0
    for i in range(0, len(vids), 50):
        for v in YT.fetch_videos_detailed(vids[i:i + 50]):
            vid = v["video_id"]
            views, likes, comments = v.get("views", 0), v.get("likes", 0), v.get("comments", 0)
            database.upsert_video_view_state(vid, views, likes, comments, platform="youtube",
                                             source_url=f"https://www.youtube.com/watch?v={vid}")
            n_state += 1
            take = database.need_snapshot_today(vid)     # 영상 단위로 하루 1회
            for aid in id2ad.get(vid, []):
                conn = database.get_conn()
                conn.execute("UPDATE ad_library_ads SET yt_views=?,yt_likes=?,yt_comments=? WHERE id=?",
                             (views, likes, comments, aid))
                conn.commit()
                conn.close()
                if take:
                    database.add_ad_snapshot(aid, views, likes, comments)
                    n_snap += 1
                else:
                    n_skip += 1
            if take:
                database.mark_snapshot_taken(vid)
    print(f"최신 조회수 갱신 {n_state}건 · 신규 스냅샷 {n_snap}건 · 오늘 이미 저장돼 건너뜀 {n_skip}건")

    # ── Supabase 동기화(이전된 브랜드 광고분) ─────────────────────────────
    #   · video_view_state = 영상별 최신 조회수 (표가 아직 없으면 조용히 건너뜀)
    #   · ad_library_ads.yt_* / ad_view_snapshots = 방금 갱신된 값
    try:
        import jobs.sync_views_to_supabase as SV
        SV.sync()
    except Exception as e:  # noqa: BLE001  (동기화 실패가 수집을 막지 않게)
        print(f"Supabase 동기화 건너뜀: {type(e).__name__}: {e}")

    # 소재 피로도 상태 계산·저장(추이 기반) — 카드/브랜드에서 싸게 읽도록 컬럼에 캐시
    import services.trend as TR
    conn = database.get_conn()
    ad_rows = [dict(r) for r in conn.execute(
        "SELECT id, status FROM ad_library_ads "
        "WHERE id IN (SELECT DISTINCT ad_id FROM ad_view_snapshots)").fetchall()]
    conn.close()
    fn = 0
    for r in ad_rows:
        snaps = database.get_ad_snapshots(r["id"], days=120)
        fat = TR.classify_fatigue(snaps, r.get("status"))
        database.set_fatigue_status(r["id"], fat["label"])
        fn += 1
    print(f"피로도 상태 갱신 {fn}건")

    # demo.db 갱신 + 푸시(Cloud에 조회수 추이 반영)
    #  ⚠️Supabase 이전 이후로는 기본 비활성 — 매일 92MB 커밋이 레포 비대의 주원인이었다.
    #    필요하면 .env 의 ENABLE_AUTO_GIT_PUSH=true 로만 켠다.
    import config as _cfg
    if not getattr(_cfg, "ENABLE_AUTO_GIT_PUSH", False):
        print("demo.db 갱신·푸시 생략(ENABLE_AUTO_GIT_PUSH=false)")
        return
    import shutil
    import sqlite3
    import subprocess
    root = Path(__file__).resolve().parent.parent
    try:
        shutil.copy(root / "data" / "series_archive.db", root / "sample_data" / "demo.db")
        con = sqlite3.connect(root / "sample_data" / "demo.db")
        con.isolation_level = None
        con.execute("DELETE FROM users")
        con.execute("VACUUM")
        con.close()
        for cmd in (["git", "add", "sample_data/demo.db"],
                    ["git", "commit", "-m", f"auto: 일일 조회수 스냅샷 {datetime.now():%Y-%m-%d}"],
                    ["git", "push", "origin", "main"]):
            subprocess.run(cmd, cwd=str(root), capture_output=True, text=True, timeout=120)
        print("demo 갱신·푸시 완료")
    except Exception as e:  # noqa: BLE001
        print(f"demo/push 실패: {e}")


if __name__ == "__main__":
    main()
