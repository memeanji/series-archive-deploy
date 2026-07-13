"""
점수만 재계산:  python jobs/update_scores.py
수집 없이 기존 ads 의 Reference Score 를 전역 통계 기준으로 다시 매긴다.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jobs._runner import recompute_scores  # noqa: E402

if __name__ == "__main__":
    print("=== Reference Score 재계산 ===")
    recompute_scores()
