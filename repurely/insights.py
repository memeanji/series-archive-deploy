"""repurely 내부 소재 분석 — 3개 시트 통합 + 위닝/OFF/피로도 분류 + 상태 요약.

⚠️ 시트는 캠페인/소재별 '현재 누적 합계'(날짜 컬럼 없음)라, '최근 3일 vs 7일' 같은
   일별 추세 기준은 일별 스냅샷이 쌓여야 가능 → 현재는 누적 지표 기반으로 분류.
"""
from __future__ import annotations


def load_all() -> list[dict]:
    """GFA + Meta + TikTok 통합(공통 스키마). 소스 하나 실패해도 나머지는 반환.
    Meta 행은 Marketing API 소재(썸네일/영상/이미지/랜딩/상태)를 소재명·UTM으로 매칭해 결합.
    (API 호출 1회 — 호출부에서 세션 캐시로 1시간 보관)"""
    import repurely.gfa_sheet as gfa
    import repurely.meta_sheet as meta
    import repurely.tiktok_sheet as tiktok
    import repurely.meta_api as MAPI
    import repurely.tiktok_api as TTAPI
    from concurrent.futures import ThreadPoolExecutor

    def _safe(fn):
        try:
            return fn()
        except Exception:  # noqa: BLE001
            return []
    rows: list[dict] = []
    # 3개 시트 + Meta/TikTok API 소재를 동시에 fetch(속도↑)
    with ThreadPoolExecutor(max_workers=5) as ex:
        f_gfa = ex.submit(_safe, gfa.load)
        f_meta = ex.submit(_safe, meta.load)
        f_tik = ex.submit(_safe, tiktok.load)
        f_api = ex.submit(_safe, MAPI.load_ads)
        f_ttapi = ex.submit(_safe, TTAPI.load_ads)
        rows = f_gfa.result() + f_meta.result() + f_tik.result()
        idx = MAPI.index_by_name(f_api.result())
        tt_idx = TTAPI.index_by_name(f_ttapi.result())

    # Meta 행에 API 소재(썸네일/영상/이미지/랜딩/상태) 매칭 — 소재명 또는 UTM 기준
    if idx:
        for r in rows:
            if r.get("platform") != "Meta":
                continue
            for key in (r.get("utm_value"), r.get("creative_name")):
                a = idx.get((key or "").strip().lower())
                if a:
                    r["thumbnail_url"] = a.get("thumbnail_url") or ""   # fbcdn(로컬 폴백)
                    r["thumb_local"] = a.get("thumb_local") or ""       # 영구화 로컬경로(클라우드)
                    r["video_id"] = a.get("video_id") or ""
                    r["landing"] = a.get("landing") or ""
                    r["media_type"] = "video" if a.get("video_id") else "image"
                    r["meta_status"] = a.get("status") or ""
                    break

    # TikTok 행에 소재 매칭 — 우선순위: (video_id/ad_id) > UTM > 광고명(소재) > 캠페인.
    # 매칭되면 썸네일·영상·조회/완료율/시청시간/인게이지먼트 지표를 행에 결합.
    if tt_idx:
        miss = 0
        for r in rows:
            if r.get("platform") != "TikTok":
                continue
            matched = None
            for key in (r.get("utm_value"), r.get("creative_name"), r.get("campaign_name")):
                a = tt_idx.get((key or "").strip().lower())
                if a:
                    matched = a
                    break
            if matched:
                r["thumbnail_url"] = matched.get("thumbnail_url") or ""
                r["thumb_local"] = matched.get("thumb_local") or ""
                r["video_url"] = matched.get("video_url") or ""
                r["video_id"] = matched.get("video_id") or ""
                r["tt_metrics"] = matched.get("metrics") or {}
                r["media_type"] = "video"
            else:
                miss += 1
                print(f"[tiktok-match-fail] creative={r.get('creative_name')!r} "
                      f"utm={r.get('utm_value')!r} campaign={r.get('campaign_name')!r}")
        if miss:
            print(f"[tiktok-match] TikTok 소재 매칭 실패 {miss}건 (위 로그 참고)")

    # 주간(7일) 집계 결합 — 각 소재에 week_* 지표(금일/주간/지속 분류용)
    try:
        wk = weekly_metrics(7)
        for r in rows:
            key = (r.get("platform"),
                   (r.get("creative_name") or r.get("utm_value") or "").strip().lower())
            w = wk.get(key) or {}
            r["week_spend"] = w.get("spend", 0.0)
            r["week_revenue"] = w.get("revenue", 0.0)
            r["week_conversions"] = w.get("conversions", 0.0)
            r["week_roas"] = w.get("roas", 0.0)
    except Exception:  # noqa: BLE001
        pass
    return rows


def benchmarks() -> dict:
    """매체별 대시보드 요약행 → {platform: {landing: {ctr,cpc,cpm,roas,...}}}.
    시트 상단 요약 행(랜딩별)이라 평균 비교 기준으로만 사용."""
    out: dict = {}
    import repurely.meta_sheet as meta
    import repurely.tiktok_sheet as tiktok
    for plat, mod in (("Meta", meta), ("TikTok", tiktok)):
        try:
            b = mod.load_benchmark()
            if b:
                out[plat] = b
        except Exception:  # noqa: BLE001
            pass
    return out


def _recent_dates(n: int = 7) -> list[str]:
    """최근 N일(오늘 포함) YYMMDD 리스트 — KST 기준."""
    import datetime
    kst = datetime.timezone(datetime.timedelta(hours=9))
    today = datetime.datetime.now(kst).date()
    return [(today - datetime.timedelta(days=i)).strftime("%y%m%d") for i in range(n)]


def _tab_tasks(days: int) -> list:
    """병렬 fetch 작업 목록 (mod, plat, dstr, landing, kind, hdrs)."""
    import repurely.meta_sheet as meta
    import repurely.tiktok_sheet as tiktok
    tasks = []
    for mod, plat in ((meta, "Meta"), (tiktok, "TikTok")):
        for dstr in _recent_dates(days):
            for landing, kind, hdrs in mod.LANDING_TABS:
                tasks.append((mod, plat, dstr, landing, kind, hdrs))
    return tasks


def _fetch_norm(task) -> tuple:
    from repurely import google_sheets_client as G
    mod, plat, dstr, landing, kind, hdrs = task
    out = []
    for r in G.fetch_rows_tab(mod.SHEET_ID, f"{dstr} {kind}", hdrs):
        n = G.normalize(r, mod.MAP, plat, mod.SHEET_ID, "")
        if n:
            out.append(n)
    return (plat, landing, dstr, out)


def weekly_metrics(days: int = 7) -> dict:
    """최근 N일 소재별 누적 지표 {(platform, 소재명): {spend,revenue,...,roas}}. 병렬 fetch."""
    from concurrent.futures import ThreadPoolExecutor
    agg: dict = {}
    with ThreadPoolExecutor(max_workers=12) as ex:
        for plat, _landing, _dstr, items in ex.map(_fetch_norm, _tab_tasks(days)):
            for n in items:
                name = (n.get("creative_name") or n.get("utm_value") or "").strip().lower()
                if not name:
                    continue
                a = agg.setdefault((plat, name), {"spend": 0.0, "revenue": 0.0,
                                                  "conversions": 0.0, "clicks": 0.0, "impressions": 0.0})
                a["spend"] += n["spend"]; a["revenue"] += n["revenue"]
                a["conversions"] += n["conversions"]; a["clicks"] += n["clicks"]
                a["impressions"] += n["impressions"]
    for a in agg.values():
        a["roas"] = round(a["revenue"] / a["spend"] * 100, 1) if a["spend"] else 0.0
    return agg


def daily_by_segment(days: int = 14) -> dict:
    """최근 days일 (매체, 랜딩, 날짜)별 합 {(platform, landing, YYMMDD): {spend,revenue,conversions}}.
    병렬 fetch. KPI를 선택 매체·랜딩 기준으로 계산하는 데 사용."""
    from concurrent.futures import ThreadPoolExecutor
    seg: dict = {}
    with ThreadPoolExecutor(max_workers=12) as ex:
        for plat, landing, dstr, items in ex.map(_fetch_norm, _tab_tasks(days)):
            d = seg.setdefault((plat, landing, dstr), {"spend": 0.0, "revenue": 0.0, "conversions": 0.0})
            for n in items:
                d["spend"] += n["spend"]; d["revenue"] += n["revenue"]; d["conversions"] += n["conversions"]
    return seg


def kpi_from_daily_seg(seg: dict, platform: str, landing, view_rows: list) -> dict:
    """선택 매체·랜딩 기준 요약 KPI: 분류 개수(view) + 오늘 + 직전 완결 주(월~일) + 전일·전주 대비 ROAS.
    주간은 '진행중 이번 주'가 아니라 데이터가 다 찬 '직전 완결 주(지난 월~일)'를 기준으로 한다."""
    import datetime
    kst = datetime.timezone(datetime.timedelta(hours=9))
    today = datetime.datetime.now(kst).date()
    this_monday = today - datetime.timedelta(days=today.weekday())    # 이번 주 월요일(진행중 → 미집계)
    week_mon = this_monday - datetime.timedelta(days=7)               # 직전 '완결' 주(월)
    week_sun = this_monday - datetime.timedelta(days=1)               # 직전 완결 주(일)
    pweek_mon = week_mon - datetime.timedelta(days=7)                 # 그 전 주(월)
    pweek_sun = week_mon - datetime.timedelta(days=1)                 # 그 전 주(일)

    def _ds(start, end):                       # start~end 날짜를 YYMMDD 리스트로
        out = []; d = start
        while d <= end:
            out.append(d.strftime("%y%m%d")); d += datetime.timedelta(days=1)
        return out

    def roas(s, r):
        return round(r / s * 100, 1) if s else 0.0

    def period(ds):
        s = r = 0.0
        for d in ds:
            x = seg.get((platform, landing, d), {})
            s += x.get("spend", 0); r += x.get("revenue", 0)
        return s, r

    t_s, t_r = period([today.strftime("%y%m%d")])
    y_s, y_r = period([(today - datetime.timedelta(days=1)).strftime("%y%m%d")])
    w_s, w_r = period(_ds(week_mon, week_sun))           # 직전 완결 주(월~일)
    pw_s, pw_r = period(_ds(pweek_mon, pweek_sun))       # 그 전 주(월~일)
    week_label = f"{week_mon.strftime('%m/%d')}~{week_sun.strftime('%m/%d')}"
    pweek_label = f"{pweek_mon.strftime('%m/%d')}~{pweek_sun.strftime('%m/%d')}"

    def chg(cur, prev):
        return round((cur - prev) / prev * 100, 1) if prev else None

    return {
        "cnt_today": sum(1 for r in view_rows if r.get("is_today_eff")),
        "cnt_week": sum(1 for r in view_rows if r.get("is_week_eff")),
        "cnt_sustained": sum(1 for r in view_rows if r.get("is_sustained")),
        "today_spend": t_s, "today_revenue": t_r, "today_roas": roas(t_s, t_r),
        "week_spend": w_s, "week_revenue": w_r, "week_roas": roas(w_s, w_r),
        "week_label": week_label, "pweek_label": pweek_label,
        "roas_vs_yday": chg(roas(t_s, t_r), roas(y_s, y_r)),
        "roas_vs_pweek": chg(roas(w_s, w_r), roas(pw_s, pw_r)),
    }


def averages(rows: list[dict]) -> dict:
    def avg(key):
        xs = [r[key] for r in rows if r.get(key, 0) > 0]
        return sum(xs) / len(xs) if xs else 0.0
    return {"roas": avg("roas"), "ctr": avg("ctr"), "cpc": avg("cpc"), "cpm": avg("cpm")}


def winning_score(r: dict, av: dict) -> int:
    s = 0
    if r.get("conversions", 0) >= 1:
        s += 2
    if av["roas"] and r.get("roas", 0) >= av["roas"]:
        s += 3
    if av["ctr"] and r.get("ctr", 0) >= av["ctr"]:
        s += 1
    if av["cpc"] and 0 < r.get("cpc", 0) <= av["cpc"]:
        s += 1
    if r.get("spend", 0) >= 50000 and (r.get("conversions", 0) > 0 or r.get("revenue", 0) > 0):
        s += 1
    # +1: 최근 3일 ROAS ≥ 최근 7일 평균 → 일별 스냅샷 필요(현재 데이터 없음, 생략)
    return s


def winning_label(score: int) -> str:
    return ("위닝 소재" if score >= 7 else "위닝 후보" if score >= 5
            else "모니터링" if score >= 3 else "일반 소재")


def off_reasons(r: dict) -> list[str]:
    out = []
    if r.get("spend", 0) >= 50000 and r.get("conversions", 0) == 0:
        out.append("광고비 5만원 이상 사용했지만 구매 0건")
    if r.get("spend", 0) > 0 and r.get("roas", 0) == 0:
        out.append("ROAS 0")
    # 일별 기준(최근 3일 구매 0 / CTR·CPC·CPM·ROAS 추세)은 스냅샷 누적 후 적용
    return out


def is_new_test(r: dict) -> bool:
    return r.get("spend", 0) < 50000 and r.get("conversions", 0) == 0 and r.get("roas", 0) == 0


def is_fatigue(r: dict, av: dict) -> bool:
    # 일별 데이터 없음 → 프록시: 광고비 충분 + 구매 있는데 ROAS가 평균의 70% 미만
    return (r.get("spend", 0) >= 50000 and r.get("conversions", 0) > 0
            and av["roas"] and r.get("roas", 0) < av["roas"] * 0.7)


def status_summary(r: dict, av: dict) -> str:
    if r.get("is_off"):
        if r.get("conversions", 0) == 0:
            return f"광고비 {int(r.get('spend',0)):,}원 사용했지만 구매가 없어 OFF 후보입니다."
        return "성과 지표가 낮아 OFF 후보로 분류됩니다."
    if r.get("winning_label") in ("위닝 소재", "위닝 후보"):
        return f"구매수와 ROAS가 양호해 {r['winning_label']}로 분류됩니다."
    if r.get("is_fatigue"):
        return "ROAS가 repurely 평균 대비 낮아 피로도가 의심됩니다."
    if r.get("is_new_test"):
        return "아직 데이터가 적은 신규 테스트 소재입니다."
    return "안정적으로 운영 중인 소재입니다."


def enrich(rows: list[dict]) -> tuple[list[dict], dict]:
    """각 행에 분석 필드 부여 + repurely 평균 반환."""
    av = averages(rows)
    for r in rows:
        sc = winning_score(r, av)
        r["winning_score"] = sc
        r["winning_label"] = winning_label(sc)
        r["off_reasons"] = off_reasons(r)
        r["is_off"] = bool(r["off_reasons"])
        r["is_new_test"] = is_new_test(r)
        r["is_fatigue"] = is_fatigue(r, av)
        r["status_text"] = status_summary(r, av)
        # 3분류: 금일/주간/지속 효율 (매체별 ROAS 기준 + 매출·전환 동반 — 단순 노출/클릭 제외)
        thr = 70 if r.get("platform") == "TikTok" else 100
        _today_ok = (r.get("roas", 0) >= thr) and (r.get("revenue", 0) > 0 or r.get("conversions", 0) > 0)
        _week_ok = (r.get("week_roas", 0) >= thr) and (r.get("week_revenue", 0) > 0)
        r["is_today_eff"] = bool(_today_ok)
        r["is_week_eff"] = bool(_week_ok)
        # 지속: 오늘·주간 둘 다 효율 + 주간 광고비 10만 이상(확장 가능 핵심)
        r["is_sustained"] = bool(_today_ok and _week_ok and r.get("week_spend", 0) >= 100000)
    return rows, av


def summary(rows: list[dict]) -> dict:
    tot_spend = sum(r.get("spend", 0) for r in rows)
    tot_rev = sum(r.get("revenue", 0) for r in rows)
    tot_conv = sum(r.get("conversions", 0) for r in rows)
    av = averages(rows)
    return {
        "spend": tot_spend, "revenue": tot_rev, "conversions": tot_conv,
        "roas": (tot_rev / tot_spend * 100) if tot_spend else 0.0,
        "cpc": av["cpc"], "cpm": av["cpm"], "ctr": av["ctr"],
        "n": len(rows),
        "off": sum(1 for r in rows if r.get("is_off")),
        "winning": sum(1 for r in rows if r.get("winning_label") in ("위닝 소재", "위닝 후보")),
    }


def by_platform(rows: list[dict]) -> list[dict]:
    """매체별 성과 비교."""
    agg: dict = {}
    for r in rows:
        p = r.get("platform", "?")
        a = agg.setdefault(p, {"platform": p, "spend": 0.0, "revenue": 0.0,
                               "conversions": 0.0, "n": 0})
        a["spend"] += r.get("spend", 0)
        a["revenue"] += r.get("revenue", 0)
        a["conversions"] += r.get("conversions", 0)
        a["n"] += 1
    for a in agg.values():
        a["roas"] = (a["revenue"] / a["spend"] * 100) if a["spend"] else 0.0
    return sorted(agg.values(), key=lambda x: -x["spend"])
