# -*- coding: utf-8 -*-
"""읽기 경로 검증 — 같은 조회를 SQLite / Supabase 두 경로로 돌려 **결과가 같은지 + 얼마나 걸리는지**.

읽기 전용. 앱이 실제로 쓰는 함수(count_ads / load_ads_page / get_ad_full / get_ad_snapshots)를
그대로 호출하고, Supabase 경로는 services.supabase_read 미러를 탄다.
폴백 검증(잘못된 키로 미러 실패 → SQLite로 자동 회귀)까지 포함.

사용: python jobs/supabase_read_test.py
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import database  # noqa: E402
import services.supabase_read as sr  # noqa: E402
from components import get_display_thumbnail  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "supabase_read_test.json"
TABS = ("전체", "Meta")


def _timed(fn, *a, **k):
    t0 = time.time()
    v = fn(*a, **k)
    return v, (time.time() - t0) * 1000


def _sqlite_only(fn, *a, **k):
    """Supabase 경로를 잠시 끄고 같은 함수를 호출(= 기존 동작 기준선)."""
    orig = sr.handles
    sr.handles = lambda b: False
    try:
        return _timed(fn, *a, **k)
    finally:
        sr.handles = orig


def _cmp_rows(a: list[dict], b: list[dict], keys: tuple) -> list[str]:
    diffs = []
    if len(a) != len(b):
        diffs.append(f"행수 {len(a)} vs {len(b)}")
        return diffs
    for x, y in zip(a, b):
        for k in keys:
            if (x.get(k) or "") != (y.get(k) or ""):
                diffs.append(f"{x.get('id')}·{k}: {str(x.get(k))[:30]} vs {str(y.get(k))[:30]}")
    return diffs


def main() -> None:
    brands = sr.brands()
    print(f"화이트리스트: {brands or '(없음)'} · 활성={sr.enabled()}")
    if not brands:
        print("SUPABASE_READ_BRANDS 미설정 — 종료"); return

    import sqlite3 as _sq
    rec_by_brand: dict = {}
    for f in sorted(ROOT.glob("data/_recovered_stage*.db")):
        c = _sq.connect(str(f))
        for aid, b in c.execute("SELECT id, brand_name FROM r_ads"):
            rec_by_brand.setdefault(b, set()).add(aid)
        c.close()

    def _count_excluding_recovered(brand: str, tab: str) -> int:
        """Supabase 미러에서 git 복구분을 뺀 건수 — SQLite와 1:1로 비교하기 위한 기준."""
        rec = sorted(rec_by_brand.get(brand, ()))
        c = sr.conn(brand)
        if c is None:
            return -1
        where, prm = database._where(tab, {"brand": brand})
        grp = database._grp({"brand": brand})
        sql = f"SELECT COUNT(DISTINCT {grp}) {database._JOIN} WHERE {where}"
        if rec:
            sql += f" AND a.id NOT IN ({','.join(['?'] * len(rec))})"
        n = c.execute(sql, prm + rec).fetchone()[0]
        c.close()
        return n

    report = {"brands": {}, "fallback": {}, "thumbnails": {}}
    for b in brands:
        print(f"\n=== {b} ===")
        st = sr.refresh(b)                       # 미러 새로 받기(순수 왕복 시간 측정)
        print(f"  미러 하이드레이션: 광고 {st['ad_library_ads']:,} · 스냅샷 {st['ad_view_snapshots']:,} · "
              f"매칭 {st['ad_social_matches']:,} · 소셜 {st['social_videos']:,} "
              f"(네트워크 {st['_fetch_sec']}s / 총 {st['_total_sec']}s)")
        r = {"hydrate": st, "queries": {}, "diffs": []}
        for tab in TABS:
            f = {"brand": b}
            (n_lite, t_lite) = _sqlite_only(database.count_ads, tab, f)
            (n_sb, t_sb) = _timed(database.count_ads, tab, f)
            (rows_lite, tl2) = _sqlite_only(database.load_ads_page, tab, f, 1, 12)
            (rows_sb, ts2) = _timed(database.load_ads_page, tab, f, 1, 12)
            d = _cmp_rows(rows_lite, rows_sb, ("id", "brand_name", "ad_copy_short", "platform"))
            r["queries"][tab] = {"count": {"sqlite": n_lite, "supabase": n_sb,
                                           "ms_sqlite": round(t_lite, 1), "ms_supabase": round(t_sb, 1)},
                                 "page": {"rows": len(rows_sb), "ms_sqlite": round(tl2, 1),
                                          "ms_supabase": round(ts2, 1), "diffs": len(d)}}
            r["diffs"] += d
            n_norec = _count_excluding_recovered(b, tab)
            r["queries"][tab]["count"]["supabase_excl_recovered"] = n_norec
            mark = "✅" if (n_lite == n_norec and not d) else "❌"
            print(f"  [{tab}] {mark} count SQLite {n_lite} vs Supabase {n_sb} "
                  f"(복구분 제외 {n_norec} → 기준선과 {'일치' if n_norec == n_lite else '불일치'}) "
                  f"· {t_lite:.0f}ms→{t_sb:.0f}ms · page {len(rows_lite)}행 "
                  f"({tl2:.0f}ms→{ts2:.0f}ms) · 필드차이 {len(d)}")

        # 상세 1건 + 조회수 추이
        rows, _ = _timed(database.load_ads_page, "전체", {"brand": b}, 1, 12)
        if rows:
            # 조회수 추이가 실제로 있는 광고를 우선 고른다(추이 경로까지 검증되게)
            aid = rows[0]["id"]
            mc = sr.conn(b)
            if mc is not None:
                hit = mc.execute(
                    "SELECT ad_id FROM ad_view_snapshots WHERE ad_id IN (%s) "
                    "GROUP BY ad_id ORDER BY COUNT(*) DESC LIMIT 1" % ",".join("?" * len(rows)),
                    [r["id"] for r in rows]).fetchone()
                mc.close()
                if hit:
                    aid = hit[0]
            (full_l, tl), (full_s, ts) = _sqlite_only(database.get_ad_full, aid), _timed(database.get_ad_full, aid)
            same = all((full_l or {}).get(k) == (full_s or {}).get(k)
                       for k in ("id", "ad_copy", "landing_url", "platform", "brand_name"))
            (sn_l, _), (sn_s, tsn) = _sqlite_only(database.get_ad_snapshots, aid, 30), _timed(database.get_ad_snapshots, aid, 30)
            print(f"  상세 {aid}: 필드일치={'✅' if same else '❌'} ({tl[1] if isinstance(tl,tuple) else 0:.0f}ms) · "
                  f"조회수추이 {len(sn_l)} vs {len(sn_s)}행")
            r["detail"] = {"ad_id": aid, "fields_match": bool(same),
                           "snapshots_sqlite": len(sn_l), "snapshots_supabase": len(sn_s)}
            th = get_display_thumbnail(full_s or {})
            r["thumbnail"] = {"source": th.get("source"), "method": th.get("method"),
                              "src": (th.get("src") or "")[:110]}
            print(f"  썸네일: source={th.get('source')} method={th.get('method')} {(th.get('src') or '')[:80]}")
            if th.get("method") == "url" and (th.get("src") or "").startswith("http"):
                import requests
                t0 = time.time()
                code = requests.get(th["src"], timeout=30).status_code
                cold = round((time.time() - t0) * 1000, 1)
                t1 = time.time()
                requests.get(th["src"], timeout=30)
                warm = round((time.time() - t1) * 1000, 1)
                r["thumbnail"].update({"http": code, "ms_cold": cold, "ms_warm": warm})
                print(f"           HTTP {code} · 첫 로드 {cold}ms · 재요청(CDN 캐시) {warm}ms")
        report["brands"][b] = r

    # 폴백 검증: 키를 망가뜨려 미러 갱신을 실패시키고도 조회가 되는지
    print("\n=== 폴백 검증(Supabase 강제 실패) ===")
    import config
    orig_secret = config.secret
    b0 = brands[0]
    sr._mirror_path(b0).unlink(missing_ok=True)          # 캐시 제거 → 반드시 네트워크 시도
    config.secret = lambda k, *a, **kw: ("BROKEN" if k in ("SUPABASE_SERVICE_KEY", "SUPABASE_KEY")
                                         else orig_secret(k, *a, **kw))
    try:
        n, ms = _timed(database.count_ads, "전체", {"brand": b0})
        print(f"  {b0} count={n} ({ms:.0f}ms) · 최종 소스={database.LAST_READ_SOURCE} "
              f"{'✅ SQLite 폴백 정상' if database.LAST_READ_SOURCE == 'sqlite' else '❌ 폴백 실패'}")
        report["fallback"] = {"brand": b0, "count": n, "source": database.LAST_READ_SOURCE}
    finally:
        config.secret = orig_secret
        sr.refresh(b0)                                    # 미러 원복

    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(f"\n저장: {OUT}")


if __name__ == "__main__":
    main()
