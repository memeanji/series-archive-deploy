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
    n = 0
    for i in range(0, len(vids), 50):
        for v in YT.fetch_videos_detailed(vids[i:i + 50]):
            for aid in id2ad.get(v["video_id"], []):
                conn = database.get_conn()
                conn.execute("UPDATE ad_library_ads SET yt_views=?,yt_likes=?,yt_comments=? WHERE id=?",
                             (v.get("views", 0), v.get("likes", 0), v.get("comments", 0), aid))
                conn.commit()
                conn.close()
                database.add_ad_snapshot(aid, v.get("views", 0), v.get("likes", 0), v.get("comments", 0))
                n += 1
    print(f"스냅샷 저장 {n}건 완료")

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
