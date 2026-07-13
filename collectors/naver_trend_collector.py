"""
Naver DataLab 검색량 추이 수집기 — placeholder (3차 개발).

광고 수집량과 검색량 추이를 함께 보기 위한 Trends 페이지용.
교체 시: Naver DataLab API(검색어 트렌드) → keyword_trends 테이블에 적재.
지금은 빈 리스트를 반환한다.
"""
from __future__ import annotations

import config  # noqa: F401  (NAVER_CLIENT_ID/SECRET 향후 사용)


def collect() -> list[dict]:
    """반환 형식(향후): [{keyword, source, trend_date, value, raw_data}, ...]"""
    print("  [naver] placeholder — 3차 개발 예정(DataLab 검색량 추이)")
    return []
