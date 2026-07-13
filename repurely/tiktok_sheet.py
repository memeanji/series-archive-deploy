"""틱톡 대시보드 시트 로더 → 공통 스키마(platform='TikTok'). 구조는 Meta 시트와 동일."""
from repurely import google_sheets_client as G

SHEET_ID = "1wLQcxziZZKoBrEPmrV8Ckb9VU-32eYn5LAPSMJ2YiHQ"
GID = "1153373295"
PLATFORM = "TikTok"

MAP = {
    "creative_name": ["틱톡_조인소스", "조인소스", "소재명"],
    "campaign_name": ["캠페인"],
    "utm_value": ["UTM", "utm"],
    "spend": ["광고비"],
    "revenue": ["매출액", "매출"],
    "cpc": ["CPC(전체)", "CPC"],
    "cpm": ["CPM"],
    "ctr": ["CTR"],
    "conversions": ["구매건수", "구매수"],
    "roas": ["ROAS"],
}


import datetime

# 날짜탭은 헤더 일부(광고비/매출 등)가 비어 와서 위치 기반 표준 헤더로 보정
_BLOG_H = ["틱톡_조인소스", "캠페인", "UTM", "광고비", "매출액",
           "CPC(전체)", "CPM", "CTR", "구매건수", "유입수", "ROAS"]
_LAND_H = ["틱톡_조인소스", "캠페인", "세그먼트", "UTM", "광고비", "매출액",
           "CPC(전체)", "CPM", "CTR", "구매건수", "유입수", "ROAS"]
# (랜딩 라벨, 탭 종류, 표준 헤더) — 'YYMMDD 블로그/직접랜딩 조인결과'
LANDING_TABS = [("블로그", "블로그 조인결과", _BLOG_H), ("직접랜딩", "직접랜딩 조인결과", _LAND_H)]


def _date_strs():
    # KST(UTC+9) 고정 — 클라우드(UTC)에서도 한국 기준 '오늘' 탭을 읽도록
    now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9)))
    return now.strftime("%y%m%d"), (now - datetime.timedelta(days=1)).strftime("%y%m%d")


def load() -> list[dict]:
    """오늘 날짜 탭(블로그/직접랜딩)을 읽어 landing_type 태그 부여. 오늘 탭 없으면 어제로 폴백."""
    out = []
    today, yday = _date_strs()
    for landing, kind, hdrs in LANDING_TABS:
        rows = G.fetch_rows_tab(SHEET_ID, f"{today} {kind}", hdrs)
        if not rows:                       # 새벽 등 오늘 탭 미생성 → 어제 탭 폴백
            rows = G.fetch_rows_tab(SHEET_ID, f"{yday} {kind}", hdrs)
        for r in rows:
            n = G.normalize(r, MAP, PLATFORM, SHEET_ID, "")
            if n:
                n["landing_type"] = landing
                out.append(n)
    return out


def load_benchmark() -> dict:
    """랜딩별 평균 기준 {랜딩: {ctr,cpc,cpm,roas,...}} — 각 날짜 탭의 첫 요약행."""
    today, yday = _date_strs()
    out = {}
    for landing, kind, hdrs in LANDING_TABS:
        b = G.fetch_benchmark_tab(SHEET_ID, f"{today} {kind}", MAP, hdrs)
        if not b:
            b = G.fetch_benchmark_tab(SHEET_ID, f"{yday} {kind}", MAP, hdrs)
        if b:
            out[landing] = b
    return out
