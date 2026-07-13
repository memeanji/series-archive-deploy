"""
Reference Score 계산.
실제 성과 데이터가 없으므로 공개 지표 + 집행 패턴으로 0~100 점을 산출한다.

기획서 가점 기준:
  최근 90일 내 발견:        +10
  영상 소재:                +10
  현재 라이브 중:           +20
  30일 이상 유지:           +20
  60일 이상 유지:           +30  (30일 가점과 누적 → 60일+면 +50)
  조회수 상위:              +20
  좋아요/댓글 반응 높음:    +10
  동일 광고주 변형 다수:    +15
  동일 후킹 패턴 반복:      +15
  (여러 플랫폼 유사 패턴:   +20)  ← 2차, 교차분석 붙으면 활성화
최종 0~100 으로 clamp.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Optional


def _days_since(d: Optional[str]) -> Optional[int]:
    if not d:
        return None
    try:
        dt = datetime.strptime(str(d)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None
    return (date.today() - dt).days


def build_context(ads: list[dict]) -> dict:
    """배치 점수 계산에 필요한 전역 통계(상위 컷, 광고주별 변형 수, 후킹 패턴 빈도)."""
    views = sorted([int(a.get("views") or 0) for a in ads if (a.get("views") or 0) > 0])
    likes = sorted([int(a.get("likes") or 0) for a in ads if (a.get("likes") or 0) > 0])

    def _p(arr: list[int], q: float) -> float:
        if not arr:
            return float("inf")  # 표본 없으면 아무도 상위로 안 잡힘
        idx = min(len(arr) - 1, int(len(arr) * q))
        return arr[idx]

    advertiser_counts: dict[str, int] = {}
    hook_counts: dict[str, int] = {}
    for a in ads:
        name = (a.get("advertiser_name") or "").strip().lower()
        if name:
            advertiser_counts[name] = advertiser_counts.get(name, 0) + 1
        for h in (a.get("hook_tags") or []):
            hook_counts[h] = hook_counts.get(h, 0) + 1

    return {
        "views_p75": _p(views, 0.75),
        "likes_p75": _p(likes, 0.75),
        "advertiser_counts": advertiser_counts,
        "hook_counts": hook_counts,
    }


def compute_reference_score(ad: dict, ctx: Optional[dict] = None) -> int:
    ctx = ctx or {}
    score = 0

    seen = _days_since(ad.get("last_seen")) or _days_since(ad.get("first_seen"))
    if seen is not None and seen <= 90:
        score += 10

    if (ad.get("media_type") or "").lower() == "video":
        score += 10

    if (ad.get("status") or "").lower() == "live":
        score += 20

    days = ad.get("estimated_running_days")
    if isinstance(days, int):
        if days >= 60:
            score += 50   # 30일(+20) + 60일(+30) 누적
        elif days >= 30:
            score += 20

    views = int(ad.get("views") or 0)
    if views and views >= ctx.get("views_p75", float("inf")):
        score += 20

    likes = int(ad.get("likes") or 0)
    comments = int(ad.get("comments") or 0)
    if (likes and likes >= ctx.get("likes_p75", float("inf"))) or comments >= 300:
        score += 10

    name = (ad.get("advertiser_name") or "").strip().lower()
    if name and ctx.get("advertiser_counts", {}).get(name, 0) >= 2:
        score += 15

    hook_counts = ctx.get("hook_counts", {})
    if any(hook_counts.get(h, 0) >= 2 for h in (ad.get("hook_tags") or [])):
        score += 15

    return max(0, min(100, score))


def score_label(score: int) -> str:
    if score >= 80:
        return "강력 추천 레퍼런스"
    if score >= 60:
        return "참고 가치 높음"
    if score >= 40:
        return "일반 참고"
    return "낮은 우선순위"
