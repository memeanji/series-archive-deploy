# -*- coding: utf-8 -*-
"""장기 보존 18개 브랜드 → Supabase 이전(멱등). 기존 SQLite/git은 **읽기만** 한다.

데이터 출처
  · LIVE   : data/series_archive.db (현재 운영 DB)
  · 복구분 : data/_recovered_stage.db (jobs/supabase_stage.py가 git 히스토리에서 뽑아 차단규칙 통과분만 적재)

이전 방식
  · DB     : PostgREST upsert(Prefer: resolution=merge-duplicates). PK 충돌 시 갱신 → 몇 번 돌려도 동일 결과.
             ad_view_snapshots 는 (ad_id, snapshot_date) 복합 PK 그대로 유지.
  · 썸네일 : Supabase Storage 'series-archive' 버킷의 thumbnails/<파일명> 으로 업로드(x-upsert).
             DB에는 storage_path(경로) + thumbnail_url(공개 URL)만 저장하고 원본 URL은 orig_thumbnail_url 에 보존.

사용
  python jobs/supabase_migrate.py                      # DRY-RUN(집계만)
  python jobs/supabase_migrate.py --apply --thumbs     # 썸네일만 업로드
  python jobs/supabase_migrate.py --apply --tables     # DB 행만 이전
  python jobs/supabase_migrate.py --apply --all        # 둘 다
  python jobs/supabase_migrate.py --verify             # Supabase 실제 적재량 검증/보고
  옵션: --brand 테키라 (반복 가능, 기본 18개 전체) · --workers 8 · --limit N(테스트용)
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
LIVE = ROOT / "data" / "series_archive.db"
STAGE = ROOT / "data" / "_recovered_stage.db"
PLAN = ROOT / "data" / "supabase_migration_plan.json"
REPORT = ROOT / "data" / "supabase_migration_report.json"

BUCKET = "series-archive"
PREFIX = "thumbnails/"
BATCH = 500
TABLES = ("ad_library_ads", "ad_view_snapshots", "ad_social_matches",
          "social_videos", "social_video_snapshots", "brands")


# ── Supabase 기본 ────────────────────────────────────────────────────────
def _base() -> str:
    return (config.secret("SUPABASE_URL") or "").rstrip("/")


def _key() -> str:
    return (config.secret("SUPABASE_SERVICE_KEY") or config.secret("SUPABASE_SERVICE_ROLE_KEY")
            or config.secret("SUPABASE_KEY") or "")


def _h(extra: dict | None = None) -> dict:
    k = _key()
    h = {"apikey": k, "Authorization": f"Bearer {k}"}
    h.update(extra or {})
    return h


def public_url(key: str) -> str:
    return f"{_base()}/storage/v1/object/public/{BUCKET}/{key}"


# ── 로컬 데이터 로딩 ─────────────────────────────────────────────────────
def _conn(p: Path) -> sqlite3.Connection:
    c = sqlite3.connect(str(p))
    c.row_factory = sqlite3.Row
    return c


def _cols(con, table: str) -> list[str]:
    return [r[1] for r in con.execute(f"PRAGMA table_info({table})")]


def load_all(brands: list[str]) -> dict:
    """이전 대상 전체를 메모리로. 반환 {table: [row dict]} + 썸네일 파일 목록.

    ★차단 규칙(brand_match_rules)은 **복구분뿐 아니라 LIVE 행에도** 적용한다.
      AMPLE:N 오염 정리 때 만든 도메인 규칙에 걸리는 광고가 LIVE에도 14건 남아 있어(웹소설·드라마 앱
      랜딩이 repurely로 들어옴), 그대로 옮기면 오염이 Supabase로 따라간다. 로컬 DB는 건드리지 않고
      **이전 대상에서만** 뺀다."""
    import database  # noqa: PLC0415  (잡 실행 시에만 필요)
    ph = ",".join("?" * len(brands))
    live = _conn(LIVE)
    bl = database.load_ingest_blocklist(live)
    live_ads, blocked_rows = [], []
    for r in live.execute(f"SELECT * FROM ad_library_ads WHERE brand_name IN ({ph})", brands):
        a = dict(r)
        why = database._blocked_reason(a, bl)
        (blocked_rows if why else live_ads).append(a if not why else {**a, "_why": why})
    if blocked_rows:
        print(f"  [차단] LIVE 행 중 오염 규칙 해당 {len(blocked_rows)}건 이전 제외"
              f"(로컬 DB는 그대로 둠)")
    ad_ids = {a["id"] for a in live_ads}

    stage = _conn(STAGE) if STAGE.exists() else None
    rec_ads = []
    if stage:
        for r in stage.execute("SELECT payload FROM r_ads"):
            a = json.loads(r["payload"])
            a["_recovered"] = 1
            rec_ads.append(a)
    rec_ids = {a["id"] for a in rec_ads}
    all_ids = ad_ids | rec_ids

    snaps = [dict(r) for r in live.execute(
        f"""SELECT s.* FROM ad_view_snapshots s JOIN ad_library_ads a ON a.id=s.ad_id
            WHERE a.brand_name IN ({ph})""", brands)]
    for s in snaps:
        s["view_snapshot_source"] = "live"
    if stage:
        for r in stage.execute("SELECT * FROM r_snapshots"):
            d = dict(r)
            if d["ad_id"] in all_ids:
                d["view_snapshot_source"] = "git_restore"
                snaps.append(d)

    matches = [dict(r) for r in live.execute(
        f"""SELECT m.* FROM ad_social_matches m JOIN ad_library_ads a ON a.id=m.ad_id
            WHERE a.brand_name IN ({ph})""", brands)]
    if stage:
        for r in stage.execute("SELECT payload FROM r_matches"):
            m = json.loads(r["payload"])
            if m.get("ad_id") in all_ids:
                matches.append(m)

    socials = [dict(r) for r in live.execute(
        f"SELECT * FROM social_videos WHERE brand_name IN ({ph})", brands)]
    sids = {s["id"] for s in socials}
    if stage:
        for r in stage.execute("SELECT payload FROM r_socials"):
            s = json.loads(r["payload"])
            if s["id"] not in sids:
                socials.append(s)
                sids.add(s["id"])
    soc_snaps = [dict(r) for r in live.execute(
        "SELECT * FROM social_video_snapshots WHERE social_video_id IN (%s)"
        % ",".join("?" * len(sids)), list(sids))] if sids else []

    brand_rows = [dict(r) for r in live.execute(
        f"SELECT * FROM brands WHERE display_name IN ({ph})", brands)]

    live_cols = {t: _cols(live, t) for t in TABLES}
    live.close()
    if stage:
        stage.close()

    return {"ad_library_ads": live_ads + rec_ads, "ad_view_snapshots": snaps,
            "ad_social_matches": matches, "social_videos": socials,
            "social_video_snapshots": soc_snaps, "brands": brand_rows,
            "_cols": live_cols, "_recovered_ids": rec_ids, "_blocked": blocked_rows}


def thumb_files(ads: list[dict]) -> dict[str, list[str]]:
    """ad_id → 로컬 썸네일 파일 경로 목록(실존 파일만, 첫 번째가 대표)."""
    out: dict[str, list[str]] = {}
    for a in ads:
        fs = []
        for k in ("local_thumbnail_path", "thumbnail_url", "preview_url"):
            v = (a.get(k) or "").strip()
            if not v or v.startswith("http") or v.startswith("data:"):
                continue
            p = ROOT / v.replace("app/static", "static").lstrip("/")
            if p.exists() and str(p) not in fs:
                fs.append(str(p))
        if fs:
            out[a["id"]] = fs
    return out


# ── Storage ─────────────────────────────────────────────────────────────
def ensure_bucket(apply: bool) -> bool:
    r = requests.get(f"{_base()}/storage/v1/bucket/{BUCKET}", headers=_h(), timeout=30)
    if r.status_code == 200:
        print(f"  버킷 '{BUCKET}' 있음(public={r.json().get('public')})")
        return True
    if not apply:
        print(f"  [dry-run] 버킷 '{BUCKET}' 생성 예정(public)")
        return False
    r = requests.post(f"{_base()}/storage/v1/bucket",
                      headers=_h({"Content-Type": "application/json"}),
                      json={"id": BUCKET, "name": BUCKET, "public": True}, timeout=30)
    print(f"  버킷 생성: {r.status_code} {r.text[:160]}")
    return r.status_code in (200, 201)


def upload_one(path: str) -> tuple[str, bool, str]:
    key = PREFIX + Path(path).name
    ct = "image/png" if path.lower().endswith(".png") else "image/jpeg"
    try:
        with open(path, "rb") as f:
            data = f.read()
        r = requests.post(f"{_base()}/storage/v1/object/{BUCKET}/{key}",
                          headers=_h({"Content-Type": ct, "x-upsert": "true"}),
                          data=data, timeout=120)
        return key, r.status_code in (200, 201), ("" if r.status_code in (200, 201) else f"{r.status_code} {r.text[:100]}")
    except Exception as e:  # noqa: BLE001
        return key, False, str(e)[:120]


def upload_thumbs(files: list[str], apply: bool, workers: int) -> dict:
    print(f"\n[썸네일] 대상 {len(files):,}개 "
          f"({sum(Path(f).stat().st_size for f in files)/1e6:.0f} MB)")
    if not apply:
        print("  [dry-run] 업로드 안 함")
        return {"uploaded": 0, "failed": 0}
    ok = fail = 0
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for i, (key, good, err) in enumerate(ex.map(upload_one, files), 1):
            if good:
                ok += 1
            else:
                fail += 1
                if fail <= 10:
                    print(f"  실패 {key}: {err}")
            if i % 500 == 0:
                print(f"  {i:,}/{len(files):,} · 성공 {ok:,} 실패 {fail:,} · "
                      f"{time.time()-t0:.0f}s", flush=True)
    print(f"  완료: 성공 {ok:,} · 실패 {fail:,} · {time.time()-t0:.0f}s")
    return {"uploaded": ok, "failed": fail}


# ── DB upsert ───────────────────────────────────────────────────────────
def _clean(row: dict, cols: list[str], extra: dict | None = None) -> dict:
    d = {c: row.get(c) for c in cols}
    d.update(extra or {})
    return d


def upsert(table: str, rows: list[dict], on_conflict: str | None, apply: bool) -> dict:
    if not rows:
        return {"sent": 0, "failed": 0}
    if not apply:
        print(f"  [dry-run] {table}: {len(rows):,}행 upsert 예정")
        return {"sent": 0, "failed": 0}
    url = f"{_base()}/rest/v1/{table}"
    if on_conflict:
        url += f"?on_conflict={on_conflict}"
    hdr = _h({"Content-Type": "application/json",
              "Prefer": "resolution=merge-duplicates,return=minimal"})
    sent = failed = 0
    for i in range(0, len(rows), BATCH):
        chunk = rows[i:i + BATCH]
        r = requests.post(url, headers=hdr, data=json.dumps(chunk, ensure_ascii=False,
                                                            default=str).encode("utf-8"), timeout=180)
        if r.status_code in (200, 201, 204):
            sent += len(chunk)
        else:
            failed += len(chunk)
            print(f"  [{table}] {i}~{i+len(chunk)} 실패 {r.status_code}: {r.text[:240]}")
        if (i // BATCH) % 10 == 0:
            print(f"  [{table}] {min(i+BATCH, len(rows)):,}/{len(rows):,}", flush=True)
    print(f"  {table}: 전송 {sent:,} · 실패 {failed:,}")
    return {"sent": sent, "failed": failed}


def migrate_tables(data: dict, thumbs: dict[str, list[str]], apply: bool,
                   preserve: bool = True) -> dict:
    cols = data["_cols"]
    res = {}
    ads = []
    for a in data["ad_library_ads"]:
        fs = thumbs.get(a["id"]) or []
        key = PREFIX + Path(fs[0]).name if fs else ""
        ads.append(_clean(a, cols["ad_library_ads"], {
            "storage_path": key,
            "thumbnail_url": public_url(key) if key else "",
            "orig_thumbnail_url": a.get("thumbnail_url") or "",
            "local_thumbnail_path": key,          # 로컬 경로 대신 Storage 경로만 저장
            # 장기보존 브랜드만 1. 나머지는 원본 값(0) 그대로 둬서 60일 retention이 적용되게 한다.
            "is_preserved": 1 if preserve else (a.get("is_preserved") or 0),
        }))
    res["ad_library_ads"] = upsert("ad_library_ads", ads, "id", apply)
    res["ad_view_snapshots"] = upsert(
        "ad_view_snapshots",
        [_clean(s, cols["ad_view_snapshots"] + ["view_snapshot_source"]) for s in data["ad_view_snapshots"]],
        "ad_id,snapshot_date", apply)
    res["ad_social_matches"] = upsert(
        "ad_social_matches", [_clean(m, cols["ad_social_matches"]) for m in data["ad_social_matches"]],
        "ad_id,social_id", apply)
    res["social_videos"] = upsert(
        "social_videos", [_clean(s, cols["social_videos"]) for s in data["social_videos"]], "id", apply)
    res["social_video_snapshots"] = upsert(
        "social_video_snapshots",
        [_clean(s, cols["social_video_snapshots"]) for s in data["social_video_snapshots"]],
        "social_video_id,snapshot_date", apply)
    res["brands"] = upsert("brands", [_clean(b, cols["brands"]) for b in data["brands"]], "id", apply)
    return res


# ── 검증 ────────────────────────────────────────────────────────────────
def _count(table: str, params: str = "") -> int:
    url = f"{_base()}/rest/v1/{table}?select=*{('&' + params) if params else ''}"
    r = requests.get(url, headers=_h({"Prefer": "count=exact", "Range": "0-0"}), timeout=60)
    cr = r.headers.get("content-range", "")
    try:
        return int(cr.split("/")[-1])
    except Exception:  # noqa: BLE001
        return -1


def verify(brands: list[str]) -> dict:
    print("\n[검증] Supabase 실제 적재량")
    out = {"tables": {}, "per_brand": {}}
    for t in TABLES:
        n = _count(t)
        out["tables"][t] = n
        print(f"  {t:24}{n:>10,}")
    for b in brands:
        q = requests.utils.quote(f'"{b}"')
        ads = _count("ad_library_ads", f"brand_name=eq.{q}")
        out["per_brand"][b] = {"ads": ads}
    rec = _count("ad_view_snapshots", "view_snapshot_source=eq.git_restore")
    live = _count("ad_view_snapshots", "view_snapshot_source=eq.live")
    out["snapshots"] = {"live": live, "git_restore": rec}
    print(f"  스냅샷 출처별 — live {live:,} · git_restore {rec:,}")
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--thumbs", action="store_true")
    ap.add_argument("--tables", action="store_true")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--verify", action="store_true")
    ap.add_argument("--brand", action="append")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--plan")
    ap.add_argument("--stage")
    ap.add_argument("--no-preserve", action="store_true",
                    help="장기보존 표시를 하지 않는다(=60일 retention 대상으로 이전)")
    a = ap.parse_args()

    global STAGE
    if a.plan:
        globals()["PLAN"] = Path(a.plan)
    if a.stage:
        STAGE = Path(a.stage)
    plan = json.loads(Path(a.plan or PLAN).read_text(encoding="utf-8"))
    brands = a.brand or plan["brands"]
    if not _base() or not _key():
        print("❌ SUPABASE_URL / SUPABASE_SERVICE_KEY 없음"); sys.exit(1)
    print(f"대상 브랜드 {len(brands)}개 · Supabase {_base()}")

    if a.verify:
        REPORT.write_text(json.dumps(verify(brands), ensure_ascii=False, indent=1), encoding="utf-8")
        return

    data = load_all(brands)
    thumbs = thumb_files(data["ad_library_ads"])
    files = sorted({f for fs in thumbs.values() for f in fs})
    if a.limit:
        files = files[:a.limit]
    print(f"\n이전 대상: 광고 {len(data['ad_library_ads']):,}"
          f"(복구 {len(data['_recovered_ids']):,}) · 스냅샷 {len(data['ad_view_snapshots']):,} · "
          f"매칭 {len(data['ad_social_matches']):,} · 소셜 {len(data['social_videos']):,} · "
          f"소셜스냅샷 {len(data['social_video_snapshots']):,} · 썸네일 {len(files):,}")

    res = {}
    if a.thumbs or a.all:
        ensure_bucket(a.apply)
        res["storage"] = upload_thumbs(files, a.apply, a.workers)
    if a.tables or a.all:
        res["db"] = migrate_tables(data, thumbs, a.apply, preserve=not a.no_preserve)
    if not (a.thumbs or a.tables or a.all):
        print("\n(dry-run 집계만 수행 — 실제 이전은 --apply 와 --thumbs/--tables/--all 필요)")
    REPORT.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
