"""Google Sheets 읽기(읽기 전용) — 공개 CSV(export) 우선, 서비스계정 JSON 있으면 gspread 폴백.

인증: Streamlit secrets/env 의 GCP_SERVICE_ACCOUNT_JSON (또는 GOOGLE_SERVICE_ACCOUNT_JSON).
공개 시트는 인증 없이 CSV 로 읽힘. 비공개 시트는 서비스계정 필요.
원본 시트는 절대 수정하지 않음(GET 전용).
"""
from __future__ import annotations

import csv
import io
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402

# 헤더 행 자동탐지용 토큰(2개 이상 포함된 행을 헤더로 간주 — row0 합계/타임스탬프 건너뜀)
_HEADER_TOKENS = ("광고비", "캠페인", "ROAS", "매출", "조인소스", "UTM", "utm")


def csv_url(sheet_id: str, gid: str) -> str:
    return f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv&gid={gid}"


def _service_account_info():
    raw = config.secret("GCP_SERVICE_ACCOUNT_JSON") or config.secret("GOOGLE_SERVICE_ACCOUNT_JSON")
    if not raw:
        return None
    import json
    try:
        return json.loads(raw) if isinstance(raw, str) else dict(raw)
    except Exception:  # noqa: BLE001
        return None


def fetch_raw(sheet_id: str, gid: str) -> list[list[str]]:
    """시트 전체 행(list[list[str]]). 공개 CSV 우선, 실패 시 서비스계정. 실패하면 []."""
    import requests
    try:
        r = requests.get(csv_url(sheet_id, gid), timeout=25)
        if r.status_code == 200 and "csv" in r.headers.get("content-type", ""):
            return list(csv.reader(io.StringIO(r.content.decode("utf-8", errors="replace"))))
    except Exception:  # noqa: BLE001
        pass
    info = _service_account_info()
    if info:
        try:
            import gspread
            gc = gspread.service_account_from_dict(info)
            ws = gc.open_by_key(sheet_id).get_worksheet_by_id(int(gid))
            return ws.get_all_values()
        except Exception:  # noqa: BLE001
            pass
    return []


def _header_idx(raw: list) -> int:
    """헤더 행 탐지(토큰 2개+ 포함). row0 요약/타임스탬프는 건너뜀. 못 찾으면 0."""
    for i, row in enumerate(raw[:6]):
        joined = "".join(c or "" for c in row)
        if sum(1 for t in _HEADER_TOKENS if t in joined) >= 2:
            return i
    return 0


def _fill(headers: list, fill: list | None) -> list:
    """헤더 행의 빈 칸을 표준 헤더(fill)로 위치 기반 보정. 있는 헤더는 유지.
    (날짜탭은 광고비/매출 등 일부 헤더가 비어 와서 필요)."""
    if not fill:
        return headers
    return [headers[j] if headers[j] else (fill[j] if j < len(fill) else "")
            for j in range(len(headers))]


def _rows_from_raw(raw: list, fill_headers: list | None = None) -> list[dict]:
    if not raw:
        return []
    hidx = _header_idx(raw)
    headers = _fill([(h or "").strip() for h in raw[hidx]], fill_headers)
    out = []
    for row in raw[hidx + 1:]:
        if not any((c or "").strip() for c in row):
            continue
        out.append({headers[j]: (row[j] if j < len(row) else "") for j in range(len(headers))})
    return out


def fetch_raw_tab(sheet_id: str, tab_name: str) -> list[list[str]]:
    """gviz API로 '탭 이름'을 지정해 읽기(날짜별 탭 'YYMMDD 블로그 조인결과' 등). 실패 시 []."""
    import requests
    try:
        r = requests.get(f"https://docs.google.com/spreadsheets/d/{sheet_id}/gviz/tq",
                         params={"tqx": "out:csv", "sheet": tab_name}, timeout=25)
        if r.status_code == 200 and "html" not in r.headers.get("content-type", ""):
            return list(csv.reader(io.StringIO(r.content.decode("utf-8", errors="replace"))))
    except Exception:  # noqa: BLE001
        pass
    return []


def fetch_rows(sheet_id: str, gid: str) -> list[dict]:
    """헤더 자동탐지 후 dict 리스트(gid 기반). 빈 행 제외."""
    return _rows_from_raw(fetch_raw(sheet_id, gid))


def fetch_rows_tab(sheet_id: str, tab_name: str, fill_headers: list | None = None) -> list[dict]:
    """날짜별 탭 이름으로 dict 리스트. fill_headers로 빈 헤더 보정."""
    return _rows_from_raw(fetch_raw_tab(sheet_id, tab_name), fill_headers)


def _benchmark_from_raw(raw: list, mapping: dict, fill_headers: list | None = None) -> dict | None:
    """헤더 바로 위의 '전체 요약/평균' 행을 mapping으로 파싱(개별 소재 아님, 평균 비교 기준)."""
    if not raw:
        return None
    hidx = _header_idx(raw)
    if hidx == 0:
        return None                       # 헤더 위에 요약행이 없음
    headers = _fill([(h or "").strip() for h in raw[hidx]], fill_headers)
    summ = raw[hidx - 1]                  # 헤더 바로 위 = 요약/합계 행
    row = {headers[j]: (summ[j] if j < len(summ) else "") for j in range(len(headers))}

    def g(field):
        src = mapping.get(field)
        if isinstance(src, (list, tuple)):
            return _pick(row, *src)
        return row.get(src, "") if src else ""

    spend = to_num(g("spend")); revenue = to_num(g("revenue"))
    impressions = to_num(g("impressions")); clicks = to_num(g("clicks"))
    ctr = to_num(g("ctr")); cpc = to_num(g("cpc")); cpm = to_num(g("cpm")); roas = to_num(g("roas"))
    if not roas and spend and revenue:    # 요약행에 ROAS 없으면 전체 매출/광고비로 보강
        roas = round(revenue / spend * 100, 1)
    if not (ctr or cpc or cpm or roas):
        return None
    return {"ctr": ctr, "cpc": cpc, "cpm": cpm, "roas": roas,
            "spend": spend, "revenue": revenue,
            "conversions": to_num(g("conversions")),
            "impressions": impressions, "clicks": clicks}


def fetch_benchmark(sheet_id: str, gid: str, mapping: dict) -> dict | None:
    """헤더 위 '전체 요약/평균' 행을 평균 비교 기준으로(gid 기반). 없으면 None."""
    return _benchmark_from_raw(fetch_raw(sheet_id, gid), mapping)


def fetch_benchmark_tab(sheet_id: str, tab_name: str, mapping: dict,
                        fill_headers: list | None = None) -> dict | None:
    """날짜별 탭의 요약/평균 행. fill_headers로 빈 헤더 보정."""
    return _benchmark_from_raw(fetch_raw_tab(sheet_id, tab_name), mapping, fill_headers)


def to_num(v) -> float:
    """쉼표·원·%·공백 제거 후 숫자형. 빈값/오류는 0.0."""
    if v is None:
        return 0.0
    s = str(v).replace(",", "").replace("원", "").replace("%", "").replace(" ", "").strip()
    if s in ("", "-", "#DIV/0!", "N/A"):
        return 0.0
    try:
        return float(s)
    except (TypeError, ValueError):
        return 0.0


def _pick(row: dict, *names) -> str:
    """여러 후보 컬럼명 중 존재하는 첫 값(컬럼명이 매체마다 달라도 안전)."""
    for n in names:
        if n in row and str(row[n]).strip():
            return row[n]
    return ""


# 공통 스키마 컬럼
COMMON_COLS = ["date", "platform", "brand_name", "campaign_name", "ad_group_name", "ad_name",
               "creative_name", "utm_value", "spend", "impressions", "clicks", "ctr", "cpc",
               "cpm", "conversions", "revenue", "roas", "status", "source_sheet", "source_gid",
               "collected_at"]


def normalize(row: dict, mapping: dict, platform: str, sheet_id: str, gid: str) -> dict | None:
    """원본 행 → 공통 스키마. mapping: {공통컬럼: 원본컬럼명(or 후보리스트)}.
    없는 지표(CTR/CPC/CPM)는 노출/클릭/광고비로 보강 계산. 의미 없는 행은 None."""
    def g(field):
        src = mapping.get(field)
        if not src:
            return ""
        if isinstance(src, (list, tuple)):
            return _pick(row, *src)
        return row.get(src, "")

    spend = to_num(g("spend"))
    impressions = to_num(g("impressions"))
    clicks = to_num(g("clicks"))
    revenue = to_num(g("revenue"))
    conversions = to_num(g("conversions"))
    roas = to_num(g("roas"))
    ctr = to_num(g("ctr"))
    cpc = to_num(g("cpc"))
    cpm = to_num(g("cpm"))
    # 보강 계산(원본에 없을 때만)
    if not ctr and impressions:
        ctr = round(clicks / impressions * 100, 2)
    if not cpc and clicks:
        cpc = round(spend / clicks, 1)
    if not cpm and impressions:
        cpm = round(spend / impressions * 1000, 1)
    if not roas and spend:
        roas = round(revenue / spend * 100, 1) if revenue else 0.0

    creative = (g("creative_name") or "").strip()
    campaign = (g("campaign_name") or "").strip()
    utm = (g("utm_value") or "").strip()
    if not (creative or campaign or utm) or (spend == 0 and revenue == 0 and impressions == 0):
        return None   # 빈/합계/무의미 행 제외

    return {
        "date": "", "platform": platform, "brand_name": "repurely",
        "campaign_name": campaign, "ad_group_name": (g("ad_group_name") or "").strip(),
        "ad_name": creative, "creative_name": creative, "utm_value": utm,
        "spend": spend, "impressions": impressions, "clicks": clicks,
        "ctr": ctr, "cpc": cpc, "cpm": cpm, "conversions": conversions,
        "revenue": revenue, "roas": roas, "status": "live",
        "source_sheet": sheet_id, "source_gid": gid,
        "collected_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
