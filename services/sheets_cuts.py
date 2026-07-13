"""구간별 자막 → 구글시트 컷별(C열~) 자동 입력.
   - 정규화: Gemini 구간 JSON → [{start,end,caption,visual}]
   - 행매칭: ad_id > video_url > 없으면 새 행 추가
   - 입력: C열부터 컷별 caption 가로 입력(기존 레이아웃 유지, 부족하면 열 확장)
   인증: 서비스계정(GCP_SERVICE_ACCOUNT_JSON / GOOGLE_SERVICE_ACCOUNT_JSON), 시트=GOOGLE_SHEET_ID.
   ※ 이 앱은 Streamlit 이므로 별도 REST 서버 없이 이 서비스 함수를 버튼에서 호출함.
"""
from __future__ import annotations

import json

import config

CAPTION_START_COL = 3   # C열(1-based)


def _sa_info():
    """서비스계정 정보 — JSON 문자열(클라우드 secrets) 또는 파일 경로(로컬 .env) 모두 지원."""
    import os
    raw = config.secret("GCP_SERVICE_ACCOUNT_JSON") or config.secret("GOOGLE_SERVICE_ACCOUNT_JSON")
    # 파일 경로로 지정한 경우(GCP_SERVICE_ACCOUNT_FILE) 또는 raw 가 실제 파일 경로면 파일을 읽음
    path = config.secret("GCP_SERVICE_ACCOUNT_FILE")
    if not raw and path and os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:  # noqa: BLE001
            return None
    if raw and not raw.lstrip().startswith("{") and os.path.exists(raw.strip()):
        try:
            with open(raw.strip(), encoding="utf-8") as f:
                return json.load(f)
        except Exception:  # noqa: BLE001
            return None
    if not raw:
        return None
    try:
        return json.loads(raw) if isinstance(raw, str) else dict(raw)
    except Exception:  # noqa: BLE001
        return None


import re

# 'MM:SS–MM:SS  대사' / 'M:SS 대사' 등 타임코드 줄
_TS_LINE = re.compile(r"^\s*(\d{1,2}:\d{2}(?::\d{2})?)\s*[–\-~]?\s*"
                      r"(\d{1,2}:\d{2}(?::\d{2})?)?\s+(.+?)\s*$")


def normalize_cuts(script_text: str) -> list[dict]:
    """저장 포맷이 무엇이든 컷 리스트 [{start,end,caption,visual}] 로 정규화.
       1) Gemini 구간 JSON  2) 평문 타임코드(MM:SS–MM:SS 대사)  3) 그 외 → []."""
    t = (script_text or "").strip()
    if not t:
        return []
    # 1) 구간 JSON
    if t.startswith("[") and t.endswith("]"):
        try:
            segs = json.loads(t)
            if isinstance(segs, list):
                out = []
                for s in segs:
                    if isinstance(s, dict):
                        out.append({"start": str(s.get("start") or "").strip(),
                                    "end": str(s.get("end") or "").strip(),
                                    "caption": (s.get("script") or s.get("caption") or "").strip(),
                                    "visual": (s.get("visual_summary") or s.get("visual") or "").strip()})
                if out:
                    return out
        except Exception:  # noqa: BLE001
            pass
    # 2) 평문 타임코드 줄 파싱(youtube_transcript 등)
    cuts = []
    for raw in t.splitlines():
        ln = raw.strip()
        if not ln or (ln.startswith("[") and ln.endswith("]")):   # '[영상 스크립트]' 헤더 건너뜀
            continue
        m = _TS_LINE.match(ln)
        if m and m.group(3):
            cuts.append({"start": m.group(1), "end": m.group(2) or "",
                         "caption": m.group(3).strip(), "visual": ""})
    return cuts


def normalize_script_data(row: dict) -> dict:
    """저장된 스크립트 데이터 정규화(구버전 호환).
       반환 {segments, full_script, source, has_cuts, parse}."""
    text = (row.get("script_text") or row.get("script") or row.get("transcript") or "").strip()
    src = row.get("script_source") or ("plain" if text else "")
    cuts = normalize_cuts(text)
    parse = "json" if (text.startswith("[") and text.endswith("]")) else ("timecode" if cuts else ("text" if text else "empty"))
    # 전체 스크립트(흐르는 문장): 컷이 있으면 caption 이어붙임, 없으면 원문
    if cuts and any(c["caption"] for c in cuts):
        full = " ".join(c["caption"] for c in cuts if c["caption"])
    else:
        full = text
    return {"segments": cuts, "full_script": full, "source": src,
            "has_cuts": bool(cuts), "parse": parse}


def _col_letter(n: int) -> str:
    s = ""
    while n > 0:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def write_storyboard(new_title: str, cuts: list[dict], template_tab: str = "",
                     caption_row: int = 1, visual_row: int = 3,
                     sheet_id: str = "", keep_cols=("A",), keep_cells=("B2",)) -> dict:
    """템플릿 탭 복사 → 새 탭(new_title) → A열·B2 등 보존 셀 빼고 비우기 →
       caption_row(기본 1행) C열~ 컷별 대사, visual_row(기본 3행) C열~ 편집가이드 입력.
       반환 {success,message,tab,cut_count}."""
    cuts = cuts or []
    if not cuts:
        return {"success": False, "message": "구간별 자막(컷) 데이터가 없습니다."}
    info = _sa_info()
    if not info:
        return {"success": False, "message": "구글시트 인증 정보를 확인해주세요 (서비스계정 미설정)."}
    sheet_id = sheet_id or config.secret("GOOGLE_SHEET_ID")
    if not sheet_id:
        return {"success": False, "message": "GOOGLE_SHEET_ID 가 설정되지 않았습니다."}
    template_tab = template_tab or config.secret("GOOGLE_SHEET_TEMPLATE_TAB", "")
    try:
        import gspread
        gc = gspread.service_account_from_dict(info)
        sh = gc.open_by_key(sheet_id)
        titles = [w.title for w in sh.worksheets()]
        tpl = None
        for w in sh.worksheets():
            if w.title == template_tab or (template_tab and template_tab in w.title):
                tpl = w
                break
        if tpl is None:
            return {"success": False, "message": f"템플릿 탭 '{template_tab}' 를 찾을 수 없습니다."}
        # 이름 충돌 방지
        title = new_title
        i = 2
        while title in titles:
            title = f"{new_title}-{i}"
            i += 1
        new_ws = sh.duplicate_sheet(source_sheet_id=tpl.id, new_sheet_name=title)
    except Exception as e:  # noqa: BLE001
        return {"success": False, "message": f"템플릿 복사 실패: {str(e)[:120]}"}

    captions = [c.get("caption", "") for c in cuts]
    visuals = [c.get("visual", "") for c in cuts]
    rows_n = new_ws.row_count
    last_col_idx = 2 + len(cuts)            # C(3) + (len-1)
    end_col = _col_letter(last_col_idx)
    try:
        if new_ws.col_count < last_col_idx:
            new_ws.add_cols(last_col_idx - new_ws.col_count)
        # A열·B2 보존, 나머지 텍스트 비우기
        clears = [f"B1:{end_col}1", f"C2:{end_col}2", f"B3:{end_col}{rows_n}"]
        new_ws.batch_clear(clears)
        # 입력: 1행 C열~ 대사, 3행 C열~ 편집가이드
        cap_range = f"C{caption_row}:{end_col}{caption_row}"
        new_ws.update(cap_range, [captions], value_input_option="RAW")
        new_ws.update(f"C{visual_row}:{end_col}{visual_row}", [visuals], value_input_option="RAW")
        # 대사 셀(C1~) 서식: 글자 크기 10, 굵게 해제
        try:
            new_ws.format(cap_range, {"textFormat": {"fontSize": 10, "bold": False}})
        except Exception:  # noqa: BLE001
            pass
    except Exception as e:  # noqa: BLE001
        return {"success": False, "message": f"시트 입력 실패: {str(e)[:120]}", "tab": title}
    return {"success": True, "message": "스토리보드 탭 생성·입력 완료",
            "tab": title, "cut_count": len(cuts)}


def write_cut_captions(ad_id: str, video_url: str, cuts: list[dict],
                       sheet_id: str = "", sheet_name: str = "") -> dict:
    """선택 광고의 컷별 caption 을 구글시트 행(C열~)에 입력. 반환 {success,message,updated_row,cut_count,warning}."""
    cuts = cuts or []
    if not cuts:
        return {"success": False, "message": "구간별 자막 데이터가 없습니다."}
    info = _sa_info()
    if not info:
        return {"success": False, "message": "구글시트 인증 정보를 확인해주세요 (GCP_SERVICE_ACCOUNT_JSON 미설정)."}
    sheet_id = sheet_id or config.secret("GOOGLE_SHEET_ID")
    if not sheet_id:
        return {"success": False, "message": "GOOGLE_SHEET_ID 가 설정되지 않았습니다."}
    sheet_name = sheet_name or config.secret("GOOGLE_SHEET_NAME", "") or ""
    try:
        import gspread
        gc = gspread.service_account_from_dict(info)
        sh = gc.open_by_key(sheet_id)
        ws = sh.worksheet(sheet_name) if sheet_name else sh.sheet1
        values = ws.get_all_values()
    except Exception as e:  # noqa: BLE001
        return {"success": False, "message": f"구글시트 연결 실패: {str(e)[:120]}"}

    header = values[0] if values else []

    def _col_idx(name):   # 0-based 컬럼 인덱스(헤더에 있으면)
        for i, h in enumerate(header):
            if (h or "").strip().lower() == name:
                return i
        return None
    ai = _col_idx("ad_id")
    vi = _col_idx("video_url")
    captions = [c.get("caption", "") for c in cuts]

    # 행 매칭: ad_id > video_url
    matches = []
    for r in range(1, len(values)):
        row = values[r]
        key = (row[ai] if ai is not None and ai < len(row) else (row[0] if row else "")).strip()
        if ad_id and key == str(ad_id):
            matches.append(r)
    if not matches and video_url:
        for r in range(1, len(values)):
            row = values[r]
            v = (row[vi] if vi is not None and vi < len(row) else (row[1] if len(row) > 1 else "")).strip()
            if v == video_url:
                matches.append(r)

    warning = ""
    needed_cols = CAPTION_START_COL - 1 + len(captions)
    try:
        if ws.col_count < needed_cols:   # C열 이후 부족하면 자동 확장
            ws.add_cols(needed_cols - ws.col_count)
    except Exception:  # noqa: BLE001
        pass

    end_col = _col_letter(CAPTION_START_COL - 1 + len(captions))
    try:
        if matches:
            row_num = matches[0] + 1   # 1-based(헤더 포함)
            if len(matches) > 1:
                warning = "동일 ad_id 가 여러 행에 있어 첫 번째 행만 업데이트했습니다."
            ws.update(f"C{row_num}:{end_col}{row_num}", [captions], value_input_option="RAW")
            msg = "구글시트 업데이트 완료"
        else:
            # 새 행: A=ad_id, B=video_url, C~=captions (기존 행은 건드리지 않음)
            new_row = [str(ad_id or ""), video_url or ""] + captions
            ws.append_row(new_row, value_input_option="RAW")
            row_num = len(values) + 1
            msg = "ad_id 매칭 실패로 새 행에 추가했습니다."
    except Exception as e:  # noqa: BLE001
        return {"success": False, "message": f"구글시트 쓰기 실패: {str(e)[:120]}"}

    return {"success": True, "message": msg, "updated_row": row_num,
            "cut_count": len(cuts), "warning": warning}
