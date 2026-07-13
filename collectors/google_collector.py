"""
Google 레퍼런스 수집기 — placeholder (2~3차 개발).

경쟁사 광고 레퍼런스는 Google Ads Transparency Center 기반으로 검토한다.
교체 후보: SerpAPI / SearchAPI / Apify / Playwright 직접 수집.
지금은 빈 리스트를 반환해 파이프라인이 깨지지 않게만 한다.
"""
from __future__ import annotations

import config  # noqa: F401  (SERPAPI_KEY 등 향후 사용)
from collectors.base import finalize

PLATFORM = "google"


def collect() -> list[dict]:
    print("  [google] placeholder — 2차 개발 예정(Transparency Center / SerpAPI / Apify)")
    return finalize([])
