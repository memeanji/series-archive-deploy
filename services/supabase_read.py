# -*- coding: utf-8 -*-
"""앱 읽기 경로의 Supabase 전환 — **브랜드 화이트리스트 방식**(2026-08-11).

`SUPABASE_READ_BRANDS=테키라,세라블랑` 에 적힌 브랜드만 Supabase에서 읽고, 나머지는 기존 SQLite 그대로.
값을 비우면 100% 원래대로 돌아간다(코드 롤백 불필요).

동작 방식 — **Supabase에서 그 브랜드 행을 받아 로컬 미러 SQLite에 넣고, 기존 SQL을 그대로 실행**한다.
  · database.py 의 조회 SQL(윈도우 함수·중복묶기·소셜 조인)은 상당히 복잡하다. 이걸 PostgREST 문법으로
    다시 쓰면 미세하게 결과가 달라질 위험이 크다 → 쿼리는 손대지 않고 **데이터 출처만** 바꾼다.
  · 미러는 브랜드별 파일(.cache/supabase_<브랜드>.db)이며 MIRROR_TTL(기본 300초) 지나면 다시 받는다.
  · 어떤 단계든 실패하면 None 을 돌려주고, 호출측(database.py)은 즉시 SQLite로 폴백한다.
"""
from __future__ import annotations

import hashlib
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / ".cache"
MIRROR_TTL = int(config.secret("SUPABASE_MIRROR_TTL") or 300)
# 한 요청에 받는 행 수. 1,000 → 5,000 으로 올려 왕복을 1/5로 줄였다(첫 미러 생성 33s → 24s 실측).
PAGE = int(config.secret("SUPABASE_PAGE_SIZE") or 5000)
TABLES = ("ad_library_ads", "ad_view_snapshots", "ad_social_matches",
          "social_videos", "social_video_snapshots", "brands", "video_view_state")

_last_error = ""
_stats: dict = {}          # 진단용: 브랜드별 마지막 하이드레이션 정보


# ── 설정 ────────────────────────────────────────────────────────────────
def _flag(name: str) -> bool:
    """Secrets 값이 문자열이든 TOML 불리언이든 동일하게 해석.
    ⚠️ `SUPABASE_READ_ALL = true`(따옴표 없는 불리언)로 적으면 예전 코드가 .strip() 에서
       AttributeError → 상위에서 조용히 SQLite 폴백 → **화면이 텅 비는** 사고가 났다."""
    v = config.secret(name)
    if isinstance(v, bool):
        return v
    return str(v or "").strip().lower() in ("true", "1", "yes", "on")


def brands() -> list[str]:
    raw = config.secret("SUPABASE_READ_BRANDS")
    return [b.strip() for b in str(raw or "").split(",") if b.strip()]


def enabled() -> bool:
    """Supabase 읽기가 켜져 있는가.
    · 배포본: SUPABASE_READ_ALL=true 면 화이트리스트 없이도 전체를 Supabase에서 읽는다.
    · 로컬  : SUPABASE_READ_BRANDS 에 브랜드가 있을 때만 그 브랜드에 대해 켜진다."""
    if not (config.secret("SUPABASE_URL")
            and (config.secret("SUPABASE_SERVICE_KEY") or config.secret("SUPABASE_KEY"))):
        return False
    return read_all() or bool(brands())


def handles(brand: str | None) -> bool:
    if read_all():          # 전체를 Supabase에서 읽는 배포본이면 브랜드 구분 없이 True
        return enabled()
    return bool(brand) and enabled() and brand in brands()


def read_all() -> bool:
    """SUPABASE_READ_ALL 이 참이면 **로컬 DB 없이 Supabase만으로** 전 브랜드를 읽는다.
    (배포본 전용. 로컬 개발/수집 환경은 기본 false 라 기존 동작 그대로.)"""
    return _flag("SUPABASE_READ_ALL") and bool(config.secret("SUPABASE_URL"))


def last_error() -> str:
    return _last_error


def stats() -> dict:
    return dict(_stats)


# ── Supabase 조회 ───────────────────────────────────────────────────────
def _base() -> str:
    return (config.secret("SUPABASE_URL") or "").rstrip("/")


def _headers() -> dict:
    k = config.secret("SUPABASE_SERVICE_KEY") or config.secret("SUPABASE_KEY")
    return {"apikey": k, "Authorization": f"Bearer {k}"}


# 테이블별 **고정 정렬키**(pagination 안정성의 핵심). 정렬이 고정되지 않으면 페이지 경계가
# 매 요청마다 달라져 같은 행이 두 번 오거나(중복) 빠질 수 있다 — 실제로 37,334건이
# 22,711 / 22,676 처럼 들쭉날쭉 적재된 원인이었다.
_ORDER_KEY = {
    "ad_library_ads": "id",
    "social_videos": "id",
    "brands": "id",
    "ad_social_matches": "ad_id,social_id",
    "ad_view_snapshots": "ad_id,snapshot_date",
    "social_video_snapshots": "social_video_id,snapshot_date",
    "video_view_state": "social_id",
}


def _order_clause(table: str) -> str:
    key = _ORDER_KEY.get(table)
    return "&order=" + ",".join(f"{k}.asc" for k in key.split(",")) if key else ""


def _fetch(table: str, query: str, verify: bool = True) -> list[dict]:
    """PostgREST 전량 조회 — **고정 정렬키 + 총건수 검증**.

    ① 항상 `order=<unique key>.asc` 를 붙여 페이지 경계를 고정한다.
    ② 서버가 알려준 총건수(content-range)만큼 받을 때까지 돈다.
    ③ 받은 뒤 총건수와 대조하고, 정렬키 기준 중복이 있으면 예외를 던진다.
    """
    import requests  # lazy
    url = f"{_base()}/rest/v1/{table}?{query}{_order_clause(table)}"
    out, start, total = [], 0, None
    while True:
        r = requests.get(url, headers={**_headers(), "Range": f"{start}-{start + PAGE - 1}",
                                       "Prefer": "count=exact"}, timeout=90)
        if r.status_code not in (200, 206):
            raise RuntimeError(f"{table} {r.status_code}: {r.text[:120]}")
        rows = r.json()
        if total is None:
            try:
                total = int((r.headers.get("content-range") or "*/0").split("/")[-1])
            except Exception:  # noqa: BLE001
                total = -1
        out.extend(rows)
        if not rows or (total >= 0 and len(out) >= total):
            break
        start += len(rows)
    if verify and total is not None and total >= 0:
        if len(out) != total:
            raise RuntimeError(f"{table} 적재 검증 실패 — 서버 {total:,} / 받은 {len(out):,}")
        key = _ORDER_KEY.get(table)
        # 고유성 검사는 **정렬키 컬럼이 응답에 실제로 있을 때만**.
        # (select 에 키를 안 넣으면 전부 None 이 돼 '중복'으로 오탐한다 — brands?select=display_name)
        if key and out and all(c in out[0] for c in key.split(",")):
            cols = key.split(",")
            uniq = {tuple(r.get(c) for c in cols) for r in out}
            if len(uniq) != len(out):
                raise RuntimeError(
                    f"{table} 적재 검증 실패 — 받은 {len(out):,} / 고유 {len(uniq):,} "
                    f"(중복 {len(out) - len(uniq):,})")
    return out


def _in_list(vals: list[str]) -> str:
    return "(" + ",".join('"' + str(v).replace('"', '') + '"' for v in vals) + ")"


# ── 로컬 미러 ───────────────────────────────────────────────────────────
def _mirror_path(brand: str) -> Path:
    CACHE.mkdir(parents=True, exist_ok=True)
    h = hashlib.sha1(brand.encode("utf-8")).hexdigest()[:10]
    return CACHE / f"supabase_{h}.db"


def _schema_sql() -> list[str]:
    """로컬 DB의 **모든 테이블 DDL**을 복사 — 미러가 같은 스키마여야 같은 SQL이 돈다.

    ★ 예전엔 동기화 대상 7개 테이블만 만들었는데, 그러면 `users`·`video_script_cache` 처럼
      Supabase로 옮기지 않는 테이블을 건드리는 코드가 `no such table` 로 죽는다
      (실측: 로그인 시 verify_user 가 users 를 찾다가 실패). 데이터는 대상 테이블만 채우고,
      나머지는 **빈 테이블로 만들어 두어** 기존 쿼리가 그대로 돌게 한다."""
    import database
    con = sqlite3.connect(str(database.DB_PATH))
    try:
        rows = con.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND sql IS NOT NULL "
            "AND name NOT LIKE 'sqlite_%'").fetchall()
        return [r[0] for r in rows if r[0]]
    finally:
        con.close()


# 한 요청에 넣는 ad_id 개수. 200 → 400 으로 올려 왕복 횟수를 절반으로(매칭 85행에 3.7초 걸리던 원인).
ID_CHUNK = int(config.secret("SUPABASE_ID_CHUNK") or 400)


def _build_mirror(brand: str, path: Path) -> dict:
    """Supabase → 미러 SQLite. **목록 화면에 필요한 것만** 담는다.

    ★2026-08-11 최적화: 예전엔 조회수 스냅샷까지 전부 받아 테키라 기준 14.1초가 걸렸다
      (스냅샷 13,952행 = 8.7초, 전체의 63%). 스냅샷은 **상세 모달의 추이 그래프에서만** 쓰이므로
      미러에서 빼고, 상세 진입 시 그 광고 것만 직접 조회한다(`fetch_snapshots`).
    """
    t0 = time.time()
    ads = _fetch("ad_library_ads", f"select=*&brand_name=eq.{brand}")
    ids = [a["id"] for a in ads]
    snaps, matches, soc_snaps = [], [], []
    for i in range(0, len(ids), ID_CHUNK):
        matches += _fetch("ad_social_matches", f"select=*&ad_id=in.{_in_list(ids[i:i + ID_CHUNK])}")
    socials = _fetch("social_videos", f"select=*&brand_name=eq.{brand}")
    brand_rows = _fetch("brands", f"select=*&display_name=eq.{brand}")
    fetched = time.time() - t0

    tmp = path.with_suffix(".tmp")
    tmp.unlink(missing_ok=True)
    con = sqlite3.connect(str(tmp))
    for ddl in _schema_sql():
        con.execute(ddl)

    def _put(table: str, rows: list[dict]) -> int:
        if not rows:
            return 0
        cols = [r[1] for r in con.execute(f"PRAGMA table_info({table})")]
        use = [c for c in cols if c in rows[0]]
        ph = ",".join("?" * len(use))
        con.executemany(f"INSERT OR REPLACE INTO {table}({','.join(use)}) VALUES({ph})",
                        [tuple(r.get(c) for c in use) for r in rows])
        return len(rows)

    n = {"ad_library_ads": _put("ad_library_ads", ads),
         "ad_view_snapshots": _put("ad_view_snapshots", snaps),
         "ad_social_matches": _put("ad_social_matches", matches),
         "social_videos": _put("social_videos", socials),
         "social_video_snapshots": _put("social_video_snapshots", soc_snaps),
         "brands": _put("brands", brand_rows)}
    con.commit()
    con.close()
    tmp.replace(path)                           # 원자적 교체 — 조회 중 깨진 미러를 읽지 않게
    n["_fetch_sec"] = round(fetched, 2)
    n["_total_sec"] = round(time.time() - t0, 2)
    n["_built_at"] = time.time()
    return n


ALL_KEY = "__all__"


def _build_mirror_all(path: Path) -> dict:
    """전 브랜드 목록 데이터를 한 번에 받아 미러 생성(스냅샷 제외 — 상세에서 지연 로딩)."""
    t0 = time.time()
    ads = _fetch("ad_library_ads", "select=*")
    matches = _fetch("ad_social_matches", "select=*")
    socials = _fetch("social_videos", "select=*")
    brand_rows = _fetch("brands", "select=*")
    fetched = time.time() - t0
    tmp = path.with_suffix(".tmp")
    tmp.unlink(missing_ok=True)
    con = sqlite3.connect(str(tmp))
    for ddl in _schema_sql():
        con.execute(ddl)

    def _put(table: str, rows: list[dict]) -> int:
        if not rows:
            return 0
        cols = [r[1] for r in con.execute(f"PRAGMA table_info({table})")]
        use = [c for c in cols if c in rows[0]]
        ph = ",".join("?" * len(use))
        con.executemany(f"INSERT OR REPLACE INTO {table}({','.join(use)}) VALUES({ph})",
                        [tuple(r.get(c) for c in use) for r in rows])
        return len(rows)

    n = {"ad_library_ads": _put("ad_library_ads", ads),
         "ad_social_matches": _put("ad_social_matches", matches),
         "social_videos": _put("social_videos", socials),
         "brands": _put("brands", brand_rows)}
    con.commit()
    con.close()
    tmp.replace(path)
    n["_fetch_sec"] = round(fetched, 2)
    n["_total_sec"] = round(time.time() - t0, 2)
    return n


def conn_all():
    """전 브랜드 미러 커넥션. 실패하면 None."""
    global _last_error
    if not enabled():
        return None
    try:
        p = _mirror_path(ALL_KEY)
        if not (p.exists() and (time.time() - p.stat().st_mtime) < MIRROR_TTL):
            _stats[ALL_KEY] = _build_mirror_all(p)
        c = sqlite3.connect(str(p), timeout=15)
        c.row_factory = sqlite3.Row
        return c
    except Exception as e:  # noqa: BLE001
        _last_error = f"{type(e).__name__}: {e}"
        print("[supabase_read] 전체 미러 실패 — 화면이 비어 보일 수 있음: "
              + _last_error + " / 확인: SUPABASE_URL · SUPABASE_SERVICE_KEY · SUPABASE_READ_ALL",
              flush=True)
        return None


def conn(brand: str):
    """화이트리스트 브랜드의 Supabase 미러 커넥션. 실패하면 None(→ 호출측이 SQLite 폴백)."""
    global _last_error
    if read_all():
        return conn_all()
    if not handles(brand):
        return None
    try:
        p = _mirror_path(brand)
        fresh = p.exists() and (time.time() - p.stat().st_mtime) < MIRROR_TTL
        if not fresh:
            _stats[brand] = _build_mirror(brand, p)
        c = sqlite3.connect(str(p), timeout=15)
        c.row_factory = sqlite3.Row
        return c
    except Exception as e:  # noqa: BLE001
        _last_error = f"{type(e).__name__}: {e}"
        print(f"  [supabase_read] {brand} 미러 실패 → SQLite 폴백: {_last_error}")
        return None


def ad_brand(ad_id: str) -> str:
    """이 광고가 화이트리스트 브랜드 소속이면 브랜드명, 아니면 ''(=SQLite로)."""
    if not (ad_id and enabled()):
        return ""
    if read_all():
        return ALL_KEY
    for b in brands():
        p = _mirror_path(b)
        if not p.exists():
            continue
        try:
            c = sqlite3.connect(str(p), timeout=5)
            hit = c.execute("SELECT 1 FROM ad_library_ads WHERE id=? LIMIT 1", (ad_id,)).fetchone()
            c.close()
            if hit:
                return b
        except Exception:  # noqa: BLE001
            continue
    return ""


def fetch_snapshots(ad_id: str, days: int = 120) -> list[dict]:
    """상세 모달용 — 그 광고의 조회수 추이만 Supabase에서 직접(요청 1회). 미러에는 담지 않는다."""
    if not (ad_id and enabled()):
        return []
    rows = _fetch("ad_view_snapshots",
                  f"select=snapshot_date,views,likes,comments&ad_id=eq.{ad_id}"
                  f"&order=snapshot_date.desc&limit={int(days)}")
    return list(reversed(rows))


def storage_url(path: str) -> str:
    """Storage 키('thumbnails/x.jpg') → 공개 URL. SUPABASE_URL 없으면 ''."""
    base = _base()
    if not base or not path:
        return ""
    return f"{base}/storage/v1/object/public/series-archive/{path.lstrip('/')}"


def refresh(brand: str) -> dict:
    """미러 강제 갱신(진단/검증용)."""
    return _build_mirror(brand, _mirror_path(brand))
