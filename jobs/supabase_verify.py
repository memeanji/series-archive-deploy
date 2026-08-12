# -*- coding: utf-8 -*-
"""이전 결과 검증 — Supabase에 실제로 들어간 것을 조회해 브랜드별 최종 보고서를 만든다.

로컬 기대치와 대조하고, 오염 차단(AMPLE:N 등)이 유지됐는지, 썸네일 공개 URL이 실제로 열리는지까지 확인.
읽기 전용 — 아무것도 지우거나 바꾸지 않는다.

사용: python jobs/supabase_verify.py
산출: data/supabase_verification.json
"""
from __future__ import annotations

import json
import random
import sqlite3
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402
import database  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
LIVE = ROOT / "data" / "series_archive.db"
STAGE = ROOT / "data" / "_recovered_stage.db"
PLAN = ROOT / "data" / "supabase_migration_plan.json"
OUT = ROOT / "data" / "supabase_verification.json"
BUCKET = "series-archive"


def _base() -> str:
    return (config.secret("SUPABASE_URL") or "").rstrip("/")


def _h(extra: dict | None = None) -> dict:
    k = config.secret("SUPABASE_SERVICE_KEY") or config.secret("SUPABASE_KEY")
    h = {"apikey": k, "Authorization": f"Bearer {k}"}
    h.update(extra or {})
    return h


def count(table: str, q: str = "") -> int:
    url = f"{_base()}/rest/v1/{table}?select=*" + (f"&{q}" if q else "")
    r = requests.get(url, headers=_h({"Prefer": "count=exact", "Range": "0-0"}), timeout=60)
    try:
        return int(r.headers.get("content-range", "*/-1").split("/")[-1])
    except Exception:  # noqa: BLE001
        return -1


def page_all(table: str, select: str, page: int = 1000) -> list[dict]:
    """PostgREST 페이지네이션으로 전체 행을 긁어온다(집계용, 필요한 컬럼만)."""
    out, start = [], 0
    while True:
        r = requests.get(f"{_base()}/rest/v1/{table}?select={select}",
                         headers=_h({"Range": f"{start}-{start + page - 1}"}), timeout=120)
        if r.status_code not in (200, 206):
            print(f"  [경고] {table} 조회 실패 {r.status_code}: {r.text[:120]}")
            break
        rows = r.json()
        out.extend(rows)
        if len(rows) < page:
            break
        start += page
    return out


def storage_count(prefix: str = "thumbnails") -> int:
    total, offset = 0, 0
    while True:
        r = requests.post(f"{_base()}/storage/v1/object/list/{BUCKET}",
                          headers=_h({"Content-Type": "application/json"}),
                          json={"prefix": prefix + "/", "limit": 1000, "offset": offset}, timeout=90)
        if r.status_code != 200:
            print(f"  [경고] Storage 목록 실패 {r.status_code}")
            break
        items = r.json()
        total += len(items)
        if len(items) < 1000:
            break
        offset += 1000
    return total


def main() -> None:
    plan = json.loads(PLAN.read_text(encoding="utf-8"))
    brands = plan["brands"]
    # 나중에 추가 이전한 브랜드까지 한 번에 검증(계획 파일이 여러 개일 수 있음)
    for extra in sorted(ROOT.glob("data/supabase_migration_plan_*.json")):
        p2 = json.loads(extra.read_text(encoding="utf-8"))
        for b in p2["brands"]:
            if b not in brands:
                brands.append(b)
        for b, d in p2.get("blocked_detail", {}).items():
            plan["blocked_detail"].setdefault(b, d)
    live = sqlite3.connect(str(LIVE))
    live.row_factory = sqlite3.Row
    bl = database.load_ingest_blocklist(live)

    # 로컬 기준 ad_id → 브랜드 (LIVE + 복구 스테이징)
    ph = ",".join("?" * len(brands))
    ad_brand = {r["id"]: r["brand_name"] for r in
                live.execute(f"SELECT id, brand_name FROM ad_library_ads WHERE brand_name IN ({ph})", brands)}
    rec_ids = set()
    for stage_db in sorted(ROOT.glob("data/_recovered_stage*.db")):   # 브랜드 추가분 스테이지까지 전부
        st = sqlite3.connect(str(stage_db))
        try:
            for aid, b in st.execute("SELECT id, brand_name FROM r_ads"):
                ad_brand[aid] = b
                rec_ids.add(aid)
        finally:
            st.close()

    print("=== Supabase 적재량 ===")
    tables = {t: count(t) for t in ("ad_library_ads", "ad_view_snapshots", "ad_social_matches",
                                    "social_videos", "social_video_snapshots", "brands")}
    for t, n in tables.items():
        print(f"  {t:24}{n:>10,}")
    thumbs_total = storage_count()
    print(f"  {'Storage thumbnails':24}{thumbs_total:>10,}")

    print("\n=== 브랜드별 집계(Supabase 실측) ===")
    snap_rows = page_all("ad_view_snapshots", "ad_id,view_snapshot_source")
    match_rows = page_all("ad_social_matches", "ad_id")
    ad_rows = page_all("ad_library_ads", "id,brand_name,storage_path")

    per = {b: {"ads": 0, "recovered_ads": 0, "snapshots": 0, "restored_snapshots": 0,
               "matches": 0, "thumbnails": 0, "blocked": 0} for b in brands}
    for a in ad_rows:
        b = a.get("brand_name")
        if b in per:
            per[b]["ads"] += 1
            if a["id"] in rec_ids:
                per[b]["recovered_ads"] += 1
            if a.get("storage_path"):
                per[b]["thumbnails"] += 1
    for s in snap_rows:
        b = ad_brand.get(s["ad_id"])
        if b in per:
            per[b]["snapshots"] += 1
            if s.get("view_snapshot_source") == "git_restore":
                per[b]["restored_snapshots"] += 1
    for m in match_rows:
        b = ad_brand.get(m["ad_id"])
        if b in per:
            per[b]["matches"] += 1
    for b, d in plan["blocked_detail"].items():
        if b in per:
            per[b]["blocked"] = sum(d.values())
    per.get("repurely", {})["blocked"] = per.get("repurely", {}).get("blocked", 0)

    hdr = (f"{'브랜드':<12}{'광고':>7}{'(복구)':>7}{'매칭':>7}{'스냅샷':>8}{'(복구)':>8}"
           f"{'썸네일':>8}{'오염제외':>8}")
    print(hdr)
    tot = dict.fromkeys(["ads", "recovered_ads", "matches", "snapshots", "restored_snapshots",
                         "thumbnails", "blocked"], 0)
    for b in brands:
        d = per[b]
        for k in tot:
            tot[k] += d[k]
        print(f"{b:<12}{d['ads']:>7,}{d['recovered_ads']:>7,}{d['matches']:>7,}{d['snapshots']:>8,}"
              f"{d['restored_snapshots']:>8,}{d['thumbnails']:>8,}{d['blocked']:>8,}")
    print(f"{'합계':<12}{tot['ads']:>7,}{tot['recovered_ads']:>7,}{tot['matches']:>7,}"
          f"{tot['snapshots']:>8,}{tot['restored_snapshots']:>8,}{tot['thumbnails']:>8,}{tot['blocked']:>8,}")

    print("\n=== 무결성 검증 ===")
    checks = {}
    # ① 오염 ad_id 가 Supabase 에 하나라도 들어갔는가
    sample_blocked = random.Random(7).sample(sorted(bl["ids"]), min(200, len(bl["ids"])))
    inlist = ",".join(f'"{i}"' for i in sample_blocked)
    n_bad = count("ad_library_ads", f"id=in.({inlist})")
    checks["오염 ad_id 유입(표본 200)"] = n_bad
    print(f"  오염 ad_id 유입(표본 {len(sample_blocked)}건 조회): {n_bad}건  (0이어야 정상)")
    # ② AMPLE:N 건수
    n_amp = count("ad_library_ads", "brand_name=eq.AMPLE%3AN")
    checks["AMPLE:N 광고"] = n_amp
    print(f"  AMPLE:N 이전 건수: {n_amp}건  (LIVE 진짜 광고 15건과 일치해야 정상)")
    # ③ 스냅샷 UNIQUE — 총건수 vs 고유(ad_id,date)
    uniq = len({(s["ad_id"], s.get("snapshot_date")) for s in
                page_all("ad_view_snapshots", "ad_id,snapshot_date")})
    checks["스냅샷 중복"] = tables["ad_view_snapshots"] - uniq
    print(f"  스냅샷 (ad_id,snapshot_date) 중복: {tables['ad_view_snapshots'] - uniq}건  (0이어야 정상)")
    # ④ 썸네일 공개 URL 실제 접근
    with_path = [a for a in ad_rows if a.get("storage_path")]
    ok = 0
    picks = random.Random(11).sample(with_path, min(10, len(with_path)))
    for a in picks:
        u = f"{_base()}/storage/v1/object/public/{BUCKET}/{a['storage_path']}"
        try:
            ok += 1 if requests.head(u, timeout=30).status_code == 200 else 0
        except Exception:  # noqa: BLE001
            pass
    checks["썸네일 URL 200"] = f"{ok}/{len(picks)}"
    print(f"  썸네일 공개 URL 표본 {len(picks)}건 중 200 응답: {ok}건")
    # ⑤ 광고 필드 대조(표본)
    live_sample = random.Random(3).sample([a for a in ad_rows if a["id"] not in rec_ids],
                                          min(20, len(ad_rows)))
    diff = 0
    for a in live_sample:
        lr = live.execute("SELECT ad_copy, landing_url, platform, started_at FROM ad_library_ads WHERE id=?",
                          (a["id"],)).fetchone()
        rr = requests.get(f"{_base()}/rest/v1/ad_library_ads?id=eq.{a['id']}"
                          "&select=ad_copy,landing_url,platform,started_at",
                          headers=_h(), timeout=30).json()
        if not rr or any((lr[k] or "") != (rr[0].get(k) or "") for k in
                         ("ad_copy", "landing_url", "platform", "started_at")):
            diff += 1
    checks["필드 대조 불일치(표본 20)"] = diff
    print(f"  광고 필드 대조 표본 {len(live_sample)}건 중 불일치: {diff}건  (0이어야 정상)")
    # ⑥ is_preserved
    n_pres = count("ad_library_ads", "is_preserved=eq.1")
    checks["is_preserved=1"] = n_pres
    print(f"  장기보존 표시(is_preserved=1): {n_pres:,}건 / 전체 {tables['ad_library_ads']:,}건")

    OUT.write_text(json.dumps({"tables": tables, "storage_thumbnails": thumbs_total,
                               "per_brand": per, "totals": tot, "checks": checks},
                              ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n저장: {OUT}")
    live.close()


if __name__ == "__main__":
    main()
