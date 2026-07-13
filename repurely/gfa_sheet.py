"""네이버 GFA 대시보드 시트 로더 → 공통 스키마(platform='Naver GFA')."""
from repurely import google_sheets_client as G

SHEET_ID = "1P4PkGTQkP1G-nR8CBzqVNZxvy5EqW4rKuwc7FEWTPQI"
GID = "1569217297"
PLATFORM = "Naver GFA"

# 공통컬럼: 원본컬럼명(후보 리스트 가능 — 컬럼명 달라도 안전)
MAP = {
    "campaign_name": ["캠페인"],
    "creative_name": ["네이버_조인소스", "조인소스", "소재명"],
    "utm_value": ["utm", "UTM"],
    "ad_group_name": ["유형"],
    "spend": ["광고비"],
    "impressions": ["노출수", "노출"],
    "clicks": ["클릭수", "클릭"],
    "revenue": ["매출액", "매출"],
    "conversions": ["구매건수", "구매수"],
    "roas": ["ROAS"],
}


def load() -> list[dict]:
    out = []
    for r in G.fetch_rows(SHEET_ID, GID):
        n = G.normalize(r, MAP, PLATFORM, SHEET_ID, GID)
        if n:
            out.append(n)
    return out
