# -*- coding: utf-8 -*-
"""git 히스토리 복구분을 **로컬 스테이징 DB로만** 모은다(원본 LIVE DB·git은 건드리지 않음).

supabase_estimate.py가 낸 계획(data/supabase_migration_plan.json)과 같은 기준으로 demo.db 전 버전을
훑어, 18개 브랜드의 '복구 대상 실제 행'을 data/_recovered_stage.db 에 적재한다.
차단 규칙(brand_match_rules)은 여기서 한 번 더 적용한다 — AMPLE:N 오염분 부활 방지.

사용: python jobs/supabase_stage.py
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import database  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
LIVE = ROOT / "data" / "series_archive.db"
PLAN = ROOT / "data" / "supabase_migration_plan.json"
STAGE = ROOT / "data" / "_recovered_stage.db"
TMP = ROOT / "data" / "_hist_stage_tmp.db"

DDL = """
CREATE TABLE IF NOT EXISTS r_ads (id TEXT PRIMARY KEY, brand_name TEXT, payload TEXT);
CREATE TABLE IF NOT EXISTS r_snapshots (ad_id TEXT, snapshot_date TEXT, views INTEGER,
    likes INTEGER, comments INTEGER, created_at TEXT, PRIMARY KEY(ad_id, snapshot_date));
CREATE TABLE IF NOT EXISTS r_matches (ad_id TEXT, social_id TEXT, payload TEXT,
    PRIMARY KEY(ad_id, social_id));
CREATE TABLE IF NOT EXISTS r_socials (id TEXT PRIMARY KEY, payload TEXT);
"""


def _rows(con, sql, args=()) -> list[dict]:
    con.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in con.execute(sql, args)]
    except Exception:  # noqa: BLE001
        return []


def _commits() -> list[tuple[str, str]]:
    out = subprocess.run(["git", "log", "--pretty=%h %ad", "--date=short", "--",
                          "sample_data/demo.db"], cwd=ROOT, capture_output=True,
                         text=True, encoding="utf-8", errors="ignore").stdout
    seen, rows = set(), []
    for line in out.splitlines():
        p = line.split()
        if len(p) == 2 and p[1] not in seen:
            seen.add(p[1])
            rows.append((p[1], p[0]))
    return sorted(rows)


def main() -> None:
    global PLAN, STAGE, TMP
    if "--plan" in sys.argv:
        PLAN = Path(sys.argv[sys.argv.index("--plan") + 1])
    if "--stage" in sys.argv:
        STAGE = Path(sys.argv[sys.argv.index("--stage") + 1])
        TMP = STAGE.with_name("_hist_stage_tmp_" + STAGE.stem + ".db")
    print(f"계획 {PLAN.name} → 스테이지 {STAGE.name}", flush=True)
    plan = json.loads(PLAN.read_text(encoding="utf-8"))
    brands = plan["brands"]
    want_ads = set(plan["recovered_ad_ids"])
    want_snaps = {tuple(k) for k in plan["recovered_snapshot_keys"]}
    want_matches = {tuple(k) for k in plan["recovered_match_keys"]}
    want_socials = set(plan["recovered_social_ids"])
    print(f"복구 대상: 광고 {len(want_ads):,} · 스냅샷 {len(want_snaps):,} · "
          f"매칭 {len(want_matches):,} · 소셜 {len(want_socials):,}", flush=True)

    live = sqlite3.connect(str(LIVE))
    bl = database.load_ingest_blocklist(live)
    live.close()

    STAGE.unlink(missing_ok=True)
    st = sqlite3.connect(str(STAGE))
    st.executescript(DDL)

    ads: dict[str, dict] = {}
    snaps: dict[tuple, dict] = {}
    matches: dict[tuple, dict] = {}
    socials: dict[str, dict] = {}
    ph = ",".join("?" * len(brands))
    commits = _commits()
    for i, (d, h) in enumerate(commits, 1):
        try:
            with open(TMP, "wb") as f:
                subprocess.run(["git", "show", f"{h}:sample_data/demo.db"], cwd=ROOT,
                               stdout=f, check=True)
            con = sqlite3.connect(str(TMP))
            for r in _rows(con, f"SELECT * FROM ad_library_ads WHERE brand_name IN ({ph})", brands):
                if r["id"] in want_ads:
                    ads[r["id"]] = r                      # 최신 버전이 덮어씀(과거→최신 순회)
            for r in _rows(con, "SELECT * FROM ad_view_snapshots"):
                k = (r["ad_id"], r["snapshot_date"])
                if k in want_snaps:
                    snaps[k] = r
            for r in _rows(con, "SELECT * FROM ad_social_matches"):
                # 복구 대상 광고(=LIVE에서 사라진 광고)에 딸린 매칭만. 키는 원본과 동일하게
                # (ad_id, social_id) — social_video_id 로 잡으면 전부 NULL 이 돼 광고당 1건으로 뭉개진다.
                if r.get("ad_id") in want_ads:
                    matches[(r.get("ad_id"), r.get("social_id"))] = r
            for r in _rows(con, f"SELECT * FROM social_videos WHERE brand_name IN ({ph})", brands):
                if r["id"] in want_socials:
                    socials[r["id"]] = r
            con.close()
        except Exception as e:  # noqa: BLE001
            print(f"  [{i}/{len(commits)}] {d} 실패: {e}", flush=True)
            continue
        if i % 10 == 0 or i == len(commits):
            print(f"  [{i}/{len(commits)}] {d}: 광고 {len(ads):,} · 스냅샷 {len(snaps):,}", flush=True)
    TMP.unlink(missing_ok=True)

    # 차단 규칙 재적용(2차 방어) — 통과분만 스테이징
    blocked = 0
    for aid, a in list(ads.items()):
        why = database._blocked_reason(a, bl)
        if why:
            blocked += 1
            del ads[aid]
    kept_ids = set(ads)
    snaps = {k: v for k, v in snaps.items() if k[0] in kept_ids or k[0] not in want_ads}
    #  ↑ 복구 광고분은 통과한 것만, LIVE에 이미 있는 광고의 스냅샷은 그대로 유지

    for aid, a in ads.items():
        st.execute("INSERT OR REPLACE INTO r_ads VALUES(?,?,?)",
                   (aid, a.get("brand_name") or "", json.dumps(a, ensure_ascii=False)))
    for (aid, dt_), r in snaps.items():
        st.execute("INSERT OR REPLACE INTO r_snapshots VALUES(?,?,?,?,?,?)",
                   (aid, dt_, r.get("views") or 0, r.get("likes") or 0,
                    r.get("comments") or 0, r.get("created_at") or ""))
    for (aid, sid), r in matches.items():
        st.execute("INSERT OR REPLACE INTO r_matches VALUES(?,?,?)",
                   (aid, sid, json.dumps(r, ensure_ascii=False)))
    for sid, r in socials.items():
        st.execute("INSERT OR REPLACE INTO r_socials VALUES(?,?)",
                   (sid, json.dumps(r, ensure_ascii=False)))
    st.commit()

    print(f"\n스테이징 완료: {STAGE}")
    print(f"  광고 {len(ads):,} (차단 재적용으로 제외 {blocked}) · 스냅샷 {len(snaps):,} · "
          f"매칭 {len(matches):,} · 소셜 {len(socials):,}")
    # 검증: 차단 ad_id가 스테이징에 하나도 없어야 함
    bad = [a for a in ads if a in bl["ids"]]
    print(f"  [검증] 차단 ad_id 유입: {len(bad)}건 (0이어야 정상)")
    st.close()


if __name__ == "__main__":
    main()
