"""
전체 수집 잡:  python jobs/collect_all.py
모든 플랫폼 수집기를 돌려 Supabase(또는 로컬 DB)에 적재하고 점수를 재계산한다.
스케줄러(GitHub Actions / cron / NAS 작업 스케줄러)는 이 파일을 매일 실행하면 된다.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from collectors import (  # noqa: E402
    google_collector, meta_collector, tiktok_collector,
)
from jobs._runner import run_collectors  # noqa: E402


def main() -> None:
    print("=== Creative Radar 수집 시작 ===")
    saved = run_collectors([
        ("meta", meta_collector),
        ("tiktok", tiktok_collector),
        ("google", google_collector),
    ])
    print(f"=== 완료: 총 {len(saved)}건 처리 ===")


if __name__ == "__main__":
    main()
