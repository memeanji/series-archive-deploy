"""
'터진 광고' 등급 — 소셜 원본 영상(조회수/좋아요/댓글/공유) 기준.
⚠️ 광고 라이브러리 성과가 아니라 TikTok/IG/YT 원본 반응 기준이다.

- absolute_grade : 고정 절대 기준(S/A/B/C/미분류). 데이터 적을 때 사용.
- engagement_rate / engagement_level : 참여율.
- internal_grade : social_videos 100건 이상 쌓이면 비교군 내 분위수 기준.
- final_grade : 초기엔 absolute, 100건+면 internal 우선(단 absolute=S면 최소 A 보정).
"""
from __future__ import annotations

from typing import Optional

GRADE_RANK = {"S": 4, "A": 3, "B": 2, "C": 1, "미분류": 0, None: 0, "": 0}

INTERNAL_MIN = 100      # 내부 등급 계산 시작 표본 수(전체)
GROUP_MIN = 20          # 비교군(platform/category) 최소 표본 수


def _i(x) -> int:
    try:
        return int(x or 0)
    except (TypeError, ValueError):
        return 0


def absolute_grade(views, likes, comments, shares) -> str:
    v, l, c, s = _i(views), _i(likes), _i(comments), _i(shares)
    if v >= 500_000 or l >= 5_000 or c >= 200 or s >= 200:
        return "S"
    if v >= 100_000 or l >= 1_000 or c >= 50 or s >= 50:
        return "A"
    if v >= 50_000 or l >= 500 or c >= 30 or s >= 30:
        return "B"
    if v >= 10_000 or l >= 100 or c >= 10 or s >= 10:
        return "C"
    return "미분류"


def engagement_rate(views, likes, comments, shares):
    v = _i(views)
    if v <= 0:
        return None
    return round((_i(likes) + _i(comments) + _i(shares)) / v, 4)


def engagement_level(rate) -> str:
    if rate is None:
        return "Unknown"
    if rate >= 0.03:
        return "High"
    if rate >= 0.01:
        return "Medium"
    return "Low"


def _percentile(value: float, sorted_vals: list[float]) -> float:
    """비교군 내 백분위(0~1): value 이하인 비율."""
    n = len(sorted_vals)
    if n == 0:
        return 0.0
    cnt = sum(1 for x in sorted_vals if x <= value)
    return cnt / n


def _group_key(v: dict, mode: str) -> tuple:
    if mode == "platform_category":
        return (v.get("platform"), v.get("category") or "")
    if mode == "platform":
        return (v.get("platform"),)
    return ("__global__",)


def _internal_from_percentile(p: float) -> str:
    # p = engagement_score(0~1, 클수록 상위). 상위 5%/10%/25%/50%.
    if p >= 0.95:
        return "S"
    if p >= 0.90:
        return "A"
    if p >= 0.75:
        return "B"
    if p >= 0.50:
        return "C"
    return "미분류"


def grade_all(socials: list[dict], now_iso: str = "") -> list[dict]:
    """각 social_video dict 에 등급 필드를 채워 반환(원본 dict 수정)."""
    # 1) 절대 등급 + 참여율
    for v in socials:
        v["absolute_grade"] = absolute_grade(v.get("views"), v.get("likes"),
                                             v.get("comments"), v.get("shares"))
        er = engagement_rate(v.get("views"), v.get("likes"), v.get("comments"), v.get("shares"))
        v["engagement_rate"] = er
        v["engagement_level"] = engagement_level(er)
        v["internal_grade"] = None
        v["engagement_score"] = None
        v["internal_percentile"] = None

    total = len(socials)
    use_internal = total >= INTERNAL_MIN

    if use_internal:
        # 2) 비교군 모드 결정: platform+category → platform → global
        from collections import Counter
        cat_sizes = Counter(_group_key(v, "platform_category") for v in socials)
        plat_sizes = Counter(_group_key(v, "platform") for v in socials)

        def mode_for(v) -> str:
            if cat_sizes[_group_key(v, "platform_category")] >= GROUP_MIN:
                return "platform_category"
            if plat_sizes[_group_key(v, "platform")] >= GROUP_MIN:
                return "platform"
            return "global"

        # 비교군별로 percentile 계산
        groups: dict = {}
        for v in socials:
            mode = mode_for(v)
            groups.setdefault((mode, _group_key(v, mode)), []).append(v)

        basis_name = {"platform_category": "platform_category_percentile",
                      "platform": "platform_percentile", "global": "global_percentile"}

        for (mode, _key), members in groups.items():
            def col(name):
                return sorted(_i(m.get(name)) for m in members)
            vv, ll, cc, ss = col("views"), col("likes"), col("comments"), col("shares")
            rr = sorted((m.get("engagement_rate") or 0.0) for m in members)
            scores = []
            for m in members:
                es = (_percentile(_i(m.get("views")), vv) * 0.40
                      + _percentile(_i(m.get("likes")), ll) * 0.25
                      + _percentile(_i(m.get("comments")), cc) * 0.15
                      + _percentile(_i(m.get("shares")), ss) * 0.15
                      + _percentile(m.get("engagement_rate") or 0.0, rr) * 0.05)
                m["engagement_score"] = round(es, 4)
                m["_grading_basis"] = basis_name[mode]
                scores.append(es)
            scores_sorted = sorted(scores)
            for m in members:
                p = _percentile(m["engagement_score"], scores_sorted)
                m["internal_percentile"] = round(p, 4)
                m["internal_grade"] = _internal_from_percentile(p)

    # 3) final_grade
    for v in socials:
        ag = v["absolute_grade"]
        ig = v.get("internal_grade")
        if not use_internal or ig is None:
            v["final_grade"] = ag
            v["grading_basis"] = "absolute_only"
        else:
            fg = ig
            # absolute=S 인데 internal 낮으면 최소 A 보정
            if ag == "S" and GRADE_RANK[fg] < GRADE_RANK["A"]:
                fg = "A"
            v["final_grade"] = fg
            v["grading_basis"] = v.get("_grading_basis", "global_percentile")
        v.pop("_grading_basis", None)
        v["graded_at"] = now_iso
    return socials


def passes_grade_filter(grade: Optional[str], flt: str) -> bool:
    """flt: 전체/S급/A급 이상/B급 이상/C급 이상."""
    r = GRADE_RANK.get(grade, 0)
    if flt == "S급":
        return r >= GRADE_RANK["S"]
    if flt == "A급 이상":
        return r >= GRADE_RANK["A"]
    if flt == "B급 이상":
        return r >= GRADE_RANK["B"]
    if flt == "C급 이상":
        return r >= GRADE_RANK["C"]
    return True
