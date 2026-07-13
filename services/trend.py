"""광고별 조회수 추이 분석 — 일별 증가량(delta) + 소재 피로도(fatigue) 자동 분류.

snaps: get_ad_snapshots() 반환형 [{snapshot_date, views, likes, comments}, ...] (날짜 오름차순).
"""
from __future__ import annotations


def with_deltas(snaps: list[dict]) -> list[dict]:
    """일별 증가량 추가: daily_view_delta, daily_like_delta, daily_comment_delta."""
    out = []
    prev = None
    for s in snaps:
        v, l, c = int(s.get("views") or 0), int(s.get("likes") or 0), int(s.get("comments") or 0)
        row = dict(s)
        if prev is None:
            row["daily_view_delta"] = 0
            row["daily_like_delta"] = 0
            row["daily_comment_delta"] = 0
        else:
            row["daily_view_delta"] = max(0, v - int(prev.get("views") or 0))
            row["daily_like_delta"] = max(0, l - int(prev.get("likes") or 0))
            row["daily_comment_delta"] = max(0, c - int(prev.get("comments") or 0))
        out.append(row)
        prev = s
    return out


def _avg(xs) -> float:
    xs = list(xs)
    return sum(xs) / len(xs) if xs else 0.0


# (label, 색상, 이모지)
FATIGUE_STYLE = {
    "성장 중": ("#10B981", "📈"),
    "안정": ("#0EA5E9", "➡️"),
    "정체": ("#F59E0B", "😐"),
    "피로도 의심": ("#EF4444", "⚠️"),
    "회복 중": ("#8B5CF6", "🔄"),
    "종료": ("#6B7280", "⏹"),
    "데이터 부족": ("#6B7280", "⏳"),
}


def classify_fatigue(snaps: list[dict], status: str = "live") -> dict:
    """조회수 증가량 추이로 소재 상태 분류.
    반환: {label, color, emoji, reason}"""
    def _result(label, reason):
        col, emj = FATIGUE_STYLE.get(label, FATIGUE_STYLE["데이터 부족"])
        return {"label": label, "color": col, "emoji": emj, "reason": reason}

    # 종료 상태 광고
    if status and status not in ("live", "active", "라이브"):
        return _result("종료", "종료/비활성 상태 광고입니다.")

    rows = with_deltas(snaps)
    deltas = [r["daily_view_delta"] for r in rows[1:]]   # 첫날은 delta 0(기준)
    if len(deltas) < 3:
        return _result("데이터 부족", "추이 분석에는 최소 3일 이상의 일별 데이터가 필요합니다.")

    recent3 = _avg(deltas[-3:])
    recent7 = _avg(deltas[-7:]) if len(deltas) >= 7 else _avg(deltas)
    last3 = deltas[-3:]

    # 1) 회복 중 — 최근 2일 증가량이 다시 상승(직전보다 ↑)
    if len(deltas) >= 3 and deltas[-1] > deltas[-2] and deltas[-2] >= deltas[-3] and deltas[-1] > 0:
        if recent3 < recent7:   # 전체적으론 꺾였지만 최근 반등
            return _result("회복 중", "최근 2일 조회수 증가량이 다시 상승하고 있습니다.")

    # 2) 피로도 의심 — 최근3일 평균이 최근7일 평균보다 30%+ 감소
    if recent7 > 0 and recent3 < recent7 * 0.7:
        return _result("피로도 의심",
                       f"최근 3일 평균 증가량({recent3:,.0f})이 최근 7일 평균({recent7:,.0f})보다 "
                       f"{(1-recent3/recent7)*100:.0f}% 감소했습니다.")

    # 3) 피로도 의심 — 일별 증가량 3일 연속 감소
    if len(last3) == 3 and last3[0] > last3[1] > last3[2]:
        return _result("피로도 의심", "일별 조회수 증가량이 3일 연속 감소하고 있습니다.")

    # 4) 정체 — 라이브인데 최근 2~3일 증가량이 거의 없음
    base = recent7 if recent7 > 0 else 1
    if _avg(deltas[-2:]) < max(base * 0.1, 5):
        return _result("정체", "라이브 상태지만 최근 2~3일 조회수 증가가 거의 없습니다.")

    # 5) 성장 중 — 최근3일 평균이 최근7일 평균보다 높음(가속)
    if recent3 > recent7 * 1.05:
        return _result("성장 중", "최근 조회수 증가량이 평균보다 빠르게 늘고 있습니다.")

    # 6) 안정
    return _result("안정", "조회수가 꾸준히 안정적으로 증가하고 있습니다.")
