# -*- coding: utf-8 -*-
"""장기 보존 18개 브랜드 — Supabase 이전 건수 산정(읽기 전용, 아무것도 쓰지 않음).

현재 LIVE DB + git 히스토리(sample_data/demo.db 전 버전) 복구분을 합쳐 브랜드별 최종 이전 건수를 낸다.
히스토리 복구분은 **반드시 brand_match_rules 차단 규칙(database.load_ingest_blocklist/_blocked_reason)을
통과한 행만** 센다 — 과거 의도적으로 지운 AMPLE:N 오염분(exclude_ad_id 1,321건 등)이 되살아나지 않게.

산출물: data/supabase_migration_plan.json  (이전 잡이 그대로 입력으로 사용)
사용:   python jobs/supabase_estimate.py
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
TMP = ROOT / "data" / "_hist_scan.db"
OUT = ROOT / "data" / "supabase_migration_plan.json"

# 대상 브랜드/출력 파일은 CLI로 바꿀 수 있다(브랜드 추가 이전 시 기존 계획을 덮지 않게).
#   python jobs/supabase_estimate.py --brands 메라블 --tag merable

BRANDS = ["리칼지메디", "테키라", "오도리스", "세라블랑", "샤르드", "안티칼알파",
          "셀라딕스", "네리티아", "미라클시드니", "마미케어", "옵티아", "닥터솔라",
          "파파레서피", "AMPLE:N", "repurely", "EOA", "veroza", "아임힐"]


def _rows(con, sql, args=()) -> list[dict]:
    con.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in con.execute(sql, args)]
    except Exception:  # noqa: BLE001  (옛 스키마에 없는 테이블/컬럼)
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
    return sorted(rows)          # 과거 → 최신 (최신 메타데이터가 덮어쓰도록)


def main() -> None:
    global BRANDS, OUT, TMP
    if "--brands" in sys.argv:
        BRANDS = [b.strip() for b in sys.argv[sys.argv.index("--brands") + 1].split(",") if b.strip()]
    if "--tag" in sys.argv:
        tag = sys.argv[sys.argv.index("--tag") + 1]
        OUT = ROOT / "data" / f"supabase_migration_plan_{tag}.json"
        TMP = ROOT / "data" / f"_hist_scan_{tag}.db"
    print(f"대상 브랜드 {len(BRANDS)}개: {', '.join(BRANDS)} → {OUT.name}", flush=True)
    ph = ",".join("?" * len(BRANDS))
    live = sqlite3.connect(str(LIVE))
    bl = database.load_ingest_blocklist(live)
    print(f"차단 규칙: ad_id {len(bl['ids'])} · 도메인 {len(bl['domains'])} · "
          f"키워드필수 {list(bl['require'])}", flush=True)

    live_ads = {r["id"]: r for r in _rows(live, f"SELECT * FROM ad_library_ads WHERE brand_name IN ({ph})", BRANDS)}
    live_all_ids = {r[0] for r in live.execute("SELECT id FROM ad_library_ads")}
    live_snaps = {(r["ad_id"], r["snapshot_date"]): r for r in _rows(
        live, f"""SELECT s.* FROM ad_view_snapshots s JOIN ad_library_ads a ON a.id=s.ad_id
                  WHERE a.brand_name IN ({ph})""", BRANDS)}
    live_matches = _rows(live, f"""SELECT m.* FROM ad_social_matches m JOIN ad_library_ads a ON a.id=m.ad_id
                                   WHERE a.brand_name IN ({ph})""", BRANDS)
    live_socials = {r["id"]: r for r in _rows(live, f"SELECT * FROM social_videos WHERE brand_name IN ({ph})", BRANDS)}
    print(f"LIVE(18개 브랜드): 광고 {len(live_ads):,} · 스냅샷 {len(live_snaps):,} · "
          f"매칭 {len(live_matches):,} · 소셜 {len(live_socials):,}", flush=True)

    # ── git 히스토리 스캔 ────────────────────────────────────────────────
    hist_ads: dict[str, dict] = {}            # 18개 브랜드 광고(최신 버전 우선)
    hist_snaps: dict[tuple, dict] = {}        # 전체 스냅샷(뒤에서 브랜드로 거름)
    hist_matches: dict[tuple, dict] = {}
    hist_socials: dict[str, dict] = {}
    commits = _commits()
    print(f"demo.db 히스토리 {len(commits)}개 버전 스캔", flush=True)
    for i, (d, h) in enumerate(commits, 1):
        try:
            with open(TMP, "wb") as f:
                subprocess.run(["git", "show", f"{h}:sample_data/demo.db"], cwd=ROOT,
                               stdout=f, check=True)
            con = sqlite3.connect(str(TMP))
            for r in _rows(con, f"SELECT * FROM ad_library_ads WHERE brand_name IN ({ph})", BRANDS):
                hist_ads[r["id"]] = r
            for r in _rows(con, "SELECT * FROM ad_view_snapshots"):
                hist_snaps[(r["ad_id"], r["snapshot_date"])] = r
            for r in _rows(con, "SELECT * FROM ad_social_matches"):
                hist_matches[(r.get("ad_id"), r.get("social_id"))] = r
            for r in _rows(con, f"SELECT * FROM social_videos WHERE brand_name IN ({ph})", BRANDS):
                hist_socials[r["id"]] = r
            con.close()
        except Exception as e:  # noqa: BLE001
            print(f"  [{i}/{len(commits)}] {d} 실패: {e}", flush=True)
            continue
        if i % 10 == 0 or i == len(commits):
            print(f"  [{i}/{len(commits)}] {d}: 브랜드광고 누적 {len(hist_ads):,} · "
                  f"스냅샷 누적 {len(hist_snaps):,}", flush=True)
    TMP.unlink(missing_ok=True)

    # ── 차단 규칙 적용 ──────────────────────────────────────────────────
    blocked_detail: dict[str, dict] = {}
    clean_hist_ads: dict[str, dict] = {}
    for aid, a in hist_ads.items():
        why = database._blocked_reason(a, bl)
        if why:
            b = a.get("brand_name") or ""
            blocked_detail.setdefault(b, {})
            blocked_detail[b][why.split(":")[0]] = blocked_detail[b].get(why.split(":")[0], 0) + 1
            continue
        clean_hist_ads[aid] = a

    # LIVE에 이미 있는 광고 = 이전 대상(이미 검증된 데이터) / 없는 광고 = 복구 대상
    recovered_ads = {aid: a for aid, a in clean_hist_ads.items() if aid not in live_all_ids}
    brand_ad_ids = set(live_ads) | set(clean_hist_ads)

    rec_snaps = {k: v for k, v in hist_snaps.items()
                 if k[0] in brand_ad_ids and k not in live_snaps
                 and k[0] not in bl["ids"] and k[0] in clean_hist_ads or
                 (k[0] in live_ads and k not in live_snaps)}
    # 위 조건 단순화: 브랜드 광고(차단 통과분)에 속하고 LIVE에 없는 스냅샷
    rec_snaps = {k: v for k, v in hist_snaps.items()
                 if k not in live_snaps and (k[0] in live_ads or k[0] in clean_hist_ads)}
    rec_matches = {k: v for k, v in hist_matches.items()
                   if k[0] in brand_ad_ids and k[0] in clean_hist_ads and k[0] not in live_all_ids}
    rec_socials = {sid: s for sid, s in hist_socials.items() if sid not in live_socials}

    # ── 브랜드별 집계 ───────────────────────────────────────────────────
    def _brand_of(aid: str) -> str:
        r = live_ads.get(aid) or clean_hist_ads.get(aid) or {}
        return r.get("brand_name") or "(미상)"

    per: dict[str, dict] = {b: {"live_ads": 0, "recovered_ads": 0, "live_snapshots": 0,
                                "recovered_snapshots": 0, "matches": 0, "thumbnails": 0,
                                "blocked": 0} for b in BRANDS}
    for a in live_ads.values():
        per[a["brand_name"]]["live_ads"] += 1
    for a in recovered_ads.values():
        b = a.get("brand_name")
        if b in per:
            per[b]["recovered_ads"] += 1
    for (aid, _), _v in live_snaps.items():
        b = _brand_of(aid)
        if b in per:
            per[b]["live_snapshots"] += 1
    for (aid, _), _v in rec_snaps.items():
        b = _brand_of(aid)
        if b in per:
            per[b]["recovered_snapshots"] += 1
    for m in live_matches:
        b = _brand_of(m["ad_id"])
        if b in per:
            per[b]["matches"] += 1
    for (aid, _sid) in rec_matches:
        b = _brand_of(aid)
        if b in per:
            per[b]["matches"] += 1
    for b, d in blocked_detail.items():
        if b in per:
            per[b]["blocked"] = sum(d.values())

    # 썸네일(로컬 실파일 존재분만) — LIVE + 복구분
    def _thumb_files(rowset) -> dict[str, set]:
        out: dict[str, set] = {}
        for a in rowset:
            b = a.get("brand_name")
            if b not in per:
                continue
            for k in ("local_thumbnail_path", "thumbnail_url", "preview_url"):
                v = (a.get(k) or "").strip()
                if not v or v.startswith("http") or v.startswith("data:"):
                    continue
                p = ROOT / v.replace("app/static", "static").lstrip("/")
                if p.exists():
                    out.setdefault(b, set()).add(str(p))
        return out

    thumbs = _thumb_files(list(live_ads.values()) + list(recovered_ads.values()))
    for b, s in thumbs.items():
        per[b]["thumbnails"] = len(s)

    total_thumb_bytes = sum(Path(f).stat().st_size for s in thumbs.values() for f in s)

    plan = {
        "brands": BRANDS,
        "per_brand": per,
        "totals": {
            "live_ads": len(live_ads), "recovered_ads": len(recovered_ads),
            "live_snapshots": len(live_snaps), "recovered_snapshots": len(rec_snaps),
            "matches": len(live_matches) + len(rec_matches),
            "social_videos": len(live_socials) + len(rec_socials),
            "thumbnails": sum(len(s) for s in thumbs.values()),
            "thumbnail_bytes": total_thumb_bytes,
            "blocked_total": sum(sum(d.values()) for d in blocked_detail.values()),
        },
        "blocked_detail": blocked_detail,
        "recovered_ad_ids": sorted(recovered_ads),
        "recovered_snapshot_keys": [list(k) for k in sorted(rec_snaps)],
        "recovered_match_keys": [list(k) for k in sorted(rec_matches)],
        "recovered_social_ids": sorted(rec_socials),
    }
    OUT.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")

    print("\n=== 브랜드별 이전 예정 건수 ===", flush=True)
    hdr = f"{'브랜드':<12}{'광고(현재)':>10}{'광고(복구)':>10}{'스냅샷(현재)':>12}{'스냅샷(복구)':>12}{'매칭':>7}{'썸네일':>8}{'차단':>7}"
    print(hdr)
    for b in BRANDS:
        d = per[b]
        print(f"{b:<12}{d['live_ads']:>10,}{d['recovered_ads']:>10,}{d['live_snapshots']:>12,}"
              f"{d['recovered_snapshots']:>12,}{d['matches']:>7,}{d['thumbnails']:>8,}{d['blocked']:>7,}")
    t = plan["totals"]
    print(f"\n합계: 광고 {t['live_ads']:,}+{t['recovered_ads']:,} · 스냅샷 {t['live_snapshots']:,}+"
          f"{t['recovered_snapshots']:,} · 매칭 {t['matches']:,} · 소셜 {t['social_videos']:,} · "
          f"썸네일 {t['thumbnails']:,}개 {t['thumbnail_bytes']/1e6:.0f}MB · 차단 {t['blocked_total']:,}")
    print(f"차단 상세: {json.dumps(blocked_detail, ensure_ascii=False)}")
    print(f"\n계획 저장: {OUT}")


if __name__ == "__main__":
    main()
