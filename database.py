"""
Series Archive 저장 계층 (SQLite) — 광고 라이브러리 ↔ 소셜 영상 분리 설계.

테이블
  ad_library_ads   : 메타/구글 광고 라이브러리 (게재 정보 — 성과지표 없음)
  social_videos    : TikTok/IG/YouTube 원본 (조회수/좋아요/댓글/공유 — 소셜 반응)
  ad_social_matches: 두 데이터 연결(브랜드/카피·캡션/URL/미디어 유사도 → match_score)
  users            : 로그인 계정

⚠️ 조회수/좋아요/댓글/공유는 '광고 성과'가 아니라 '매칭된 소셜 원본 영상의 반응'이다.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
STATIC_THUMBS = ROOT / "static" / "thumbnails"
THUMB_URL_PREFIX = "app/static/thumbnails"
DB_PATH = DATA / "series_archive.db"
LOCAL_DB = DATA / "local_db.json"
USER_STATE = DATA / "user_state.json"

AD_COLS = [
    "id", "brand_name", "ad_title", "ad_copy", "platform", "media_type", "ad_format",
    "thumbnail_url", "video_url", "landing_url", "original_ad_url", "transparency_url",
    "media_url", "preview_url", "status",
    "started_at", "collected_at", "score", "category", "tags",
    "is_bookmarked", "memo", "created_at", "updated_at",
    "scrape_status", "error_message", "platforms", "local_thumbnail_path",
    "script_text", "script_source", "script_status", "script_error_message",
    "script_created_at", "script_updated_at", "cta", "ad_variant_count",
    "yt_views", "yt_likes", "yt_comments", "detail_status",
    # Meta fbcdn video_url 은 만료되는 임시값 → 갱신시각/마지막크롤/재생상태를 추적
    "video_url_updated_at", "last_crawled_at", "video_status",
    "page_id", "last_seen_at",   # page_id 기반 수집 + 마지막으로 라이브러리에서 본 시각
    # Google: 법인(광고주)과 브랜드 분리 + 광고단위 브랜드 재매칭
    "advertiser_name", "brand_status", "match_method", "match_confidence", "manual_override",
]
SOCIAL_COLS = [
    "id", "brand_name", "platform", "video_id", "embed_url", "title", "channel_title",
    "video_url", "thumbnail_url", "caption",
    "views", "likes", "comments", "shares", "posted_at", "source_url",
    "collected_at", "created_at", "updated_at",
    "brand_match_score", "brand_match_reason", "review_status",
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def hash_pw(pw: str) -> str:
    return hashlib.sha256(str(pw).encode("utf-8")).hexdigest()


def get_conn() -> sqlite3.Connection:
    DATA.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    # WAL: 05:00 크롤(쓰기) 중에도 앱은 락 없이 읽기 가능 / busy_timeout: 락 충돌 시 대기
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("PRAGMA synchronous=NORMAL")
    except Exception:  # noqa: BLE001
        pass
    return conn


def _db_build_of(path) -> str:
    """해당 DB 파일의 db_build.built_at(빌드 시각) 읽기. 없으면 ''."""
    try:
        c = sqlite3.connect(path)
        r = c.execute("SELECT built_at FROM db_build LIMIT 1").fetchone()
        c.close()
        return r[0] if r else ""
    except Exception:  # noqa: BLE001
        return ""


def init_db(seed_users: Optional[dict] = None) -> None:
    # 번들 데모 DB 로 시드/재시드.
    #  - DB 없음(클라우드 첫 실행) → 시드
    #  - 클라우드(/mount/src)에서 demo.db 빌드가 현재 DB 와 다름 → 재시드
    #    (Streamlit Cloud 가 재부팅해도 옛 series_archive.db 가 남아 새 demo.db 를 안 읽던 문제 해결)
    #  - 로컬(Windows 등)에서는 라이브 크롤 DB 를 절대 덮어쓰지 않음(데이터 보호)
    import shutil
    is_cloud = "/mount/src" in str(ROOT).replace("\\", "/")
    seed = ROOT / "sample_data" / "demo.db"
    if not DB_PATH.exists():
        if seed.exists():
            DATA.mkdir(parents=True, exist_ok=True)
            shutil.copy(seed, DB_PATH)
    elif is_cloud and seed.exists():
        seed_build = _db_build_of(seed)
        if seed_build and seed_build != _db_build_of(DB_PATH):
            for ext in ("-wal", "-shm"):   # 스테일 WAL/SHM 제거 후 최신 demo.db 로 교체
                p = Path(str(DB_PATH) + ext)
                if p.exists():
                    try:
                        p.unlink()
                    except Exception:  # noqa: BLE001
                        pass
            shutil.copy(seed, DB_PATH)
    conn = get_conn()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS ad_library_ads (
        id TEXT PRIMARY KEY, brand_name TEXT, ad_title TEXT, ad_copy TEXT,
        platform TEXT, media_type TEXT, ad_format TEXT DEFAULT 'unknown',
        thumbnail_url TEXT, video_url TEXT,
        landing_url TEXT, original_ad_url TEXT, transparency_url TEXT,
        media_url TEXT, preview_url TEXT, status TEXT, started_at TEXT,
        collected_at TEXT, score INTEGER DEFAULT 0, category TEXT, tags TEXT,
        is_bookmarked INTEGER DEFAULT 0, memo TEXT DEFAULT '',
        created_at TEXT, updated_at TEXT
    );
    CREATE TABLE IF NOT EXISTS social_videos (
        id TEXT PRIMARY KEY, brand_name TEXT, platform TEXT,
        video_id TEXT, embed_url TEXT, title TEXT, channel_title TEXT,
        video_url TEXT, thumbnail_url TEXT, caption TEXT,
        views INTEGER DEFAULT 0, likes INTEGER DEFAULT 0,
        comments INTEGER DEFAULT 0, shares INTEGER DEFAULT 0, posted_at TEXT,
        source_url TEXT, collected_at TEXT, created_at TEXT, updated_at TEXT,
        absolute_grade TEXT, internal_grade TEXT, final_grade TEXT,
        engagement_rate REAL, engagement_level TEXT, engagement_score REAL,
        internal_percentile REAL, grading_basis TEXT, graded_at TEXT,
        script_text TEXT, script_status TEXT DEFAULT 'none',
        brand_match_score REAL, brand_match_reason TEXT,
        review_status TEXT DEFAULT 'needs_review'
    );
    CREATE TABLE IF NOT EXISTS social_video_snapshots (
        id INTEGER PRIMARY KEY AUTOINCREMENT, social_video_id TEXT, snapshot_date TEXT,
        views INTEGER, likes INTEGER, comments INTEGER, shares INTEGER, created_at TEXT,
        UNIQUE(social_video_id, snapshot_date)
    );
    CREATE TABLE IF NOT EXISTS ad_social_matches (
        id INTEGER PRIMARY KEY AUTOINCREMENT, ad_id TEXT, social_id TEXT,
        match_score REAL, brand_match INTEGER, copy_sim REAL, url_sim REAL,
        media_sim REAL, created_at TEXT,
        UNIQUE(ad_id, social_id)
    );
    CREATE TABLE IF NOT EXISTS youtube_ad_candidates (
        video_id TEXT PRIMARY KEY, brand_name TEXT, query TEXT,
        advertiser_legal_name TEXT, source_account_name TEXT,
        title TEXT, description TEXT, channel_title TEXT,
        duration_sec INTEGER, published_at TEXT, has_caption INTEGER,
        thumbnail_url TEXT, source_url TEXT,
        views INTEGER, likes INTEGER, comments INTEGER,
        matching_score REAL, matching_confidence TEXT, match_status TEXT,
        matched_by TEXT, classification TEXT, signals TEXT,
        matched_ad_id TEXT, collected_at TEXT
    );
    CREATE TABLE IF NOT EXISTS video_script_cache (
        cache_key TEXT PRIMARY KEY, segments_json TEXT, source TEXT, created_at TEXT
    );
    CREATE TABLE IF NOT EXISTS ad_view_snapshots (
        ad_id TEXT, snapshot_date TEXT, views INTEGER, likes INTEGER, comments INTEGER,
        created_at TEXT, UNIQUE(ad_id, snapshot_date)
    );
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE,
        password_hash TEXT, role TEXT DEFAULT 'member', created_at TEXT
    );
    CREATE TABLE IF NOT EXISTS brands (
        id INTEGER PRIMARY KEY AUTOINCREMENT, display_name TEXT UNIQUE,
        search_keywords TEXT, official_domain TEXT, meta_page_name TEXT,
        google_advertiser_name TEXT, youtube_channel_name TEXT, tiktok_handle TEXT,
        instagram_handle TEXT, category TEXT, is_active INTEGER DEFAULT 1,
        created_at TEXT, updated_at TEXT
    );
    CREATE TABLE IF NOT EXISTS brand_collection_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT, brand_id INTEGER, platform TEXT,
        keyword TEXT, status TEXT, found_count INTEGER DEFAULT 0,
        saved_count INTEGER DEFAULT 0, skipped_count INTEGER DEFAULT 0,
        error_message TEXT, started_at TEXT, finished_at TEXT
    );
    """)
    # 안전한 컬럼 마이그레이션(이미 있으면 건너뜀)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(ad_library_ads)").fetchall()]
    for c, t in (("ad_format", "TEXT DEFAULT 'unknown'"), ("transparency_url", "TEXT"),
                 ("media_url", "TEXT"), ("preview_url", "TEXT"), ("brand_id", "INTEGER"),
                 ("scrape_status", "TEXT DEFAULT 'ok'"), ("error_message", "TEXT"),
                 ("platforms", "TEXT"), ("local_thumbnail_path", "TEXT"),
                 ("script_text", "TEXT"), ("script_source", "TEXT"),
                 ("script_status", "TEXT DEFAULT 'pending'"), ("script_error_message", "TEXT"),
                 ("script_created_at", "TEXT"), ("script_updated_at", "TEXT"),
                 ("cta", "TEXT"), ("ad_variant_count", "INTEGER DEFAULT 1"),
                 ("yt_views", "INTEGER DEFAULT 0"), ("yt_likes", "INTEGER DEFAULT 0"),
                 ("yt_comments", "INTEGER DEFAULT 0"), ("detail_status", "TEXT DEFAULT ''"),
                 ("yt_embeddable", "INTEGER"),   # 1=임베드가능 0=제한 NULL=미확인(지연조회)
                 ("is_excluded", "INTEGER DEFAULT 0"),   # 사용자가 '제외'한 잘못 수집 광고
                 ("fatigue_status", "TEXT"),    # 성장중/안정/정체/피로도의심/회복중/종료(일별잡이 계산)
                 ("video_url_updated_at", "TEXT"),   # video_url 마지막 갱신시각(만료 임시URL 추적)
                 ("last_crawled_at", "TEXT"),        # 이 row 마지막 재크롤 시각
                 ("video_status", "TEXT DEFAULT ''"),  # ok/expired_url/private_or_deleted/unavailable
                 ("page_id", "TEXT"),                # 광고주 page_id(page_id 기반 수집)
                 ("last_seen_at", "TEXT"),           # 마지막으로 라이브러리에서 본 시각
                 ("advertiser_name", "TEXT"),        # Google 투명성센터 법인/광고주명
                 ("brand_status", "TEXT DEFAULT ''"),  # confirmed/estimated/company_only/unmatched
                 ("match_method", "TEXT"),           # domain/brand_text/product_keyword/company_only/unmatched/manual
                 ("match_confidence", "TEXT"),       # high/medium/low/none
                 ("manual_override", "INTEGER DEFAULT 0"),
                 ("match_reason", "TEXT")):          # 왜 매칭됐는지(화면 표시용)
        if c not in cols:
            conn.execute(f"ALTER TABLE ad_library_ads ADD COLUMN {c} {t}")
    # brands 테이블 page_id 컬럼
    bcols = [r[1] for r in conn.execute("PRAGMA table_info(brands)").fetchall()]
    for c, t in (("meta_page_id", "TEXT"),
                 ("page_id_status", "TEXT DEFAULT 'none'"),   # none/candidate/confirmed
                 ("meta_reported_count", "INTEGER DEFAULT 0"),  # 라이브러리 표기 결과수(수집률 비교)
                 ("sort_order", "INTEGER"),   # 사이드바/요일그룹 정렬 순서(기본 id, 수동 교체용)
                 ("brand_aliases", "TEXT"),       # JSON: 브랜드명 별칭(영문/표기변형)
                 ("product_keywords", "TEXT"),    # JSON: 제품명/라인명/대표 키워드
                 ("brand_domains", "TEXT")):      # JSON: 공식몰/상세페이지 도메인 패턴
        if c not in bcols:
            conn.execute(f"ALTER TABLE brands ADD COLUMN {c} {t}")
    conn.execute("UPDATE brands SET sort_order=id WHERE sort_order IS NULL")  # 최초 1회 백필
    # Google 광고-브랜드 학습 규칙(수동 매칭 → 동일 도메인/키워드 자동매칭)
    conn.execute("""CREATE TABLE IF NOT EXISTS brand_match_rules (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        pattern_type TEXT, pattern TEXT, brand_name TEXT, created_at TEXT,
        UNIQUE(pattern_type, pattern))""")
    scols = [r[1] for r in conn.execute("PRAGMA table_info(social_videos)").fetchall()]
    for c, t in (("video_id", "TEXT"), ("embed_url", "TEXT"), ("title", "TEXT"),
                 ("channel_title", "TEXT"),
                 ("absolute_grade", "TEXT"), ("internal_grade", "TEXT"), ("final_grade", "TEXT"),
                 ("engagement_rate", "REAL"), ("engagement_level", "TEXT"),
                 ("engagement_score", "REAL"), ("internal_percentile", "REAL"),
                 ("grading_basis", "TEXT"), ("graded_at", "TEXT"),
                 ("script_text", "TEXT"), ("script_status", "TEXT DEFAULT 'none'"),
                 ("brand_match_score", "REAL"), ("brand_match_reason", "TEXT"),
                 ("review_status", "TEXT DEFAULT 'needs_review'")):
        if c not in scols:
            conn.execute(f"ALTER TABLE social_videos ADD COLUMN {c} {t}")
    yacols = [r[1] for r in conn.execute("PRAGMA table_info(youtube_ad_candidates)").fetchall()]
    for c, t in (("advertiser_legal_name", "TEXT"), ("source_account_name", "TEXT"),
                 ("matching_confidence", "TEXT"), ("match_status", "TEXT"), ("matched_by", "TEXT")):
        if c not in yacols:
            conn.execute(f"ALTER TABLE youtube_ad_candidates ADD COLUMN {c} {t}")
    conn.commit()

    # 계정 동기화(secrets.toml 단일 소스)
    if seed_users is not None:
        for uname, pw in seed_users.items():
            if conn.execute("SELECT 1 FROM users WHERE username=?", (uname,)).fetchone():
                conn.execute("UPDATE users SET password_hash=? WHERE username=?", (hash_pw(pw), uname))
            else:
                conn.execute("INSERT INTO users(username,password_hash,role,created_at) VALUES(?,?,?,?)",
                             (uname, hash_pw(pw), "admin" if uname == "admin" else "member", _now()))
        if seed_users:
            qs = ",".join("?" * len(seed_users))
            conn.execute(f"DELETE FROM users WHERE username NOT IN ({qs})", tuple(seed_users.keys()))
        conn.commit()

    _fix_google_urls(conn)
    # 구버전 ads 테이블 → ad_library_ads 이관(최초 1회)
    if conn.execute("SELECT COUNT(*) FROM ad_library_ads").fetchone()[0] == 0:
        _migrate_legacy(conn)
    _ensure_indexes(conn)   # 조회 성능(브랜드 카운트·필터·정렬·매칭 조인) 보조 인덱스
    conn.commit()
    conn.close()


def _ensure_indexes(conn: sqlite3.Connection) -> None:
    """목록/카운트/매칭 조인이 매 새로고침마다 1만+행을 풀스캔하지 않도록 보조 인덱스 생성.
    모두 IF NOT EXISTS — 이미 있으면 무시. 데이터/스키마 변경 없음(읽기 성능만 개선)."""
    for ddl in (
        # brand_counts 상관 서브쿼리(브랜드별 광고 수) + 브랜드 필터 — 가장 큰 병목
        "CREATE INDEX IF NOT EXISTS idx_ala_brand ON ad_library_ads(brand_name)",
        # Meta/Google 탭 필터
        "CREATE INDEX IF NOT EXISTS idx_ala_platform ON ad_library_ads(platform)",
        # 기본 정렬(최근 수집순)
        "CREATE INDEX IF NOT EXISTS idx_ala_collected ON ad_library_ads(collected_at)",
        "CREATE INDEX IF NOT EXISTS idx_ala_brandid ON ad_library_ads(brand_id)",
        # 소셜 매칭 조인(ROW_NUMBER PARTITION BY ad_id ORDER BY match_score)
        "CREATE INDEX IF NOT EXISTS idx_asm_ad ON ad_social_matches(ad_id, match_score, social_id)",
        # 소셜 브랜드 카운트/조인
        "CREATE INDEX IF NOT EXISTS idx_sv_brand ON social_videos(brand_name)",
        # 브랜드별 최근 수집 로그 조회
        "CREATE INDEX IF NOT EXISTS idx_bcl_brand ON brand_collection_logs(brand_id, platform, id)",
    ):
        try:
            conn.execute(ddl)
        except Exception:  # noqa: BLE001
            pass
    migrate_base64_thumbnails()
    migrate_brands()
    restore_scripts_from_store()   # Supabase에 백업된 스크립트 복원(영구 보존)
    restore_bookmarks_from_store()  # Supabase 북마크 복원(Cloud 재배포 후 유실 방지·팀 공유)


def _migrate_legacy(conn: sqlite3.Connection) -> int:
    has_old = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='ads'").fetchone()
    if not has_old:
        return 0
    n = 0
    for r in conn.execute("SELECT * FROM ads").fetchall():
        a = dict(r)
        aid = a["id"]
        plat = a.get("platform")
        orig = (f"https://www.facebook.com/ads/library/?id={aid}" if plat == "meta" else "")
        row = {
            "id": aid, "brand_name": a.get("brand_name"), "ad_title": a.get("ad_title"),
            "ad_copy": a.get("ad_copy"), "platform": plat, "media_type": a.get("media_type"),
            "ad_format": a.get("media_type") or "unknown",
            "thumbnail_url": a.get("thumbnail_url"), "video_url": a.get("video_url"),
            "landing_url": a.get("landing_url"), "original_ad_url": orig,
            "transparency_url": "", "media_url": "", "preview_url": a.get("thumbnail_url") or "",
            "status": a.get("status"), "started_at": "",
            "collected_at": a.get("collected_at"), "score": a.get("score") or 0,
            "category": a.get("category"), "tags": a.get("tags") or "[]",
            "is_bookmarked": a.get("is_bookmarked") or 0, "memo": a.get("memo") or "",
            "created_at": a.get("created_at") or _now(), "updated_at": _now(),
            "scrape_status": "ok", "error_message": "", "platforms": "",
            "local_thumbnail_path": a.get("thumbnail_url") or "",
            "script_text": "", "script_source": "", "script_status": "pending",
            "script_error_message": "", "script_created_at": "", "script_updated_at": "", "cta": "",
            "ad_variant_count": 1, "yt_views": 0, "yt_likes": 0, "yt_comments": 0,
            "detail_status": "",
        }
        conn.execute(f"INSERT OR REPLACE INTO ad_library_ads({','.join(AD_COLS)}) "
                     f"VALUES({','.join(['?']*len(AD_COLS))})", tuple(row[c] for c in AD_COLS))
        n += 1
    conn.commit()
    return n


def migrate_base64_thumbnails() -> int:
    """DB에 박힌 base64 썸네일 → static 파일로 빼고 URL만 남김(렌더 성능)."""
    import base64
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, thumbnail_url FROM ad_library_ads WHERE thumbnail_url LIKE 'data:image%'"
    ).fetchall()
    if not rows:
        conn.close()
        return 0
    STATIC_THUMBS.mkdir(parents=True, exist_ok=True)
    n = 0
    for r in rows:
        try:
            data = base64.b64decode(r["thumbnail_url"].split(",", 1)[-1])
        except Exception:  # noqa: BLE001
            continue
        safe = "".join(ch for ch in r["id"] if ch.isalnum() or ch in "_-")
        (STATIC_THUMBS / f"{safe}.png").write_bytes(data)
        conn.execute("UPDATE ad_library_ads SET thumbnail_url=?, preview_url=? WHERE id=?",
                     (f"{THUMB_URL_PREFIX}/{safe}.png", f"{THUMB_URL_PREFIX}/{safe}.png", r["id"]))
        n += 1
    conn.commit()
    conn.close()
    return n


# ── 탭별 페이지 조회(요약 컬럼만, SQL LIMIT/OFFSET) ──────
_SUMMARY_COLS = (
    "a.id, a.brand_name, a.ad_title, substr(a.ad_copy,1,90) AS ad_copy_short, "
    "a.platform, a.status, a.thumbnail_url, a.local_thumbnail_path, a.preview_url, a.video_url, a.score, "
    "a.media_type, a.ad_format, a.collected_at, a.started_at, a.is_bookmarked, "
    "a.scrape_status, a.error_message, a.platforms, a.detail_status, a.video_status, a.brand_status, "
    "a.yt_views, a.yt_likes, a.yt_comments, a.yt_embeddable, a.fatigue_status, "
    "(CASE WHEN length(a.memo)>0 THEN 1 ELSE 0 END) AS has_memo, "
    "m.match_score AS match_score, s.final_grade AS social_final_grade, "
    "s.views AS social_views, s.likes AS social_likes, "
    "s.engagement_score AS social_engagement_score, s.platform AS social_platform, "
    "COUNT(*) AS dup_rows, MAX(a.ad_variant_count) AS variant_count"
)
_JOIN = (" FROM ad_library_ads a "
         "LEFT JOIN (SELECT ad_id, social_id, match_score, "
         "ROW_NUMBER() OVER (PARTITION BY ad_id ORDER BY match_score DESC, social_id) rn "
         "FROM ad_social_matches) m ON m.ad_id=a.id AND m.rn=1 "
         "LEFT JOIN social_videos s ON s.id=m.social_id")

_GRADE_SET = {"S급": "('S')", "A급 이상": "('S','A')", "B급 이상": "('S','A','B')",
              "C급 이상": "('S','A','B','C')"}


def _where(tab: str, f: dict) -> tuple[str, list]:
    w, p = ["1=1"], []
    if tab in ("meta", "google"):
        w.append("a.platform=?"); p.append(tab)
    if tab == "google" and not f.get("only_unavailable"):
        # B안: 확정+추정+미확정 모두 노출(빈 탭 방지), 배지로 상태 구분. '레퍼런스 제외'만 숨김(제외 탭)
        w.append("COALESCE(a.brand_status,'') NOT IN ('excluded','reference_excluded')")
    if tab == "TOP":
        w.append("s.final_grade IN ('S','A','B')")
        w.append("(a.thumbnail_url<>'' OR a.video_url<>'')")  # placeholder 카드 제외
        w.append("a.ad_format NOT IN ('search_text','unknown')")
    if tab == "views":   # 📈 조회수 탭 — 조회수 데이터가 있는 영상광고만
        w.append("a.yt_views > 0")
        w.append("(a.thumbnail_url<>'' OR a.video_url<>'')")
    if f.get("only_unavailable"):
        w.append("COALESCE(a.detail_status,'')='unavailable'")   # '상세 확인 불가'만 보기
    elif not f.get("show_hidden"):
        w.append("a.ad_format NOT IN ('search_text','unknown')")
        w.append("(a.thumbnail_url<>'' OR a.video_url<>'')")
        w.append("COALESCE(a.detail_status,'')<>'unavailable'")   # 기본 목록에선 제외
    w.append("COALESCE(a.is_excluded,0)=0")   # '제외'한 광고는 모든 탭에서 숨김
    if f.get("brand") and f["brand"] != "전체":
        w.append("a.brand_name=?"); p.append(f["brand"])
    if f.get("platforms"):
        w.append("a.platform IN (%s)" % ",".join("?" * len(f["platforms"]))); p += f["platforms"]
    if f.get("media"):
        w.append("a.media_type IN (%s)" % ",".join("?" * len(f["media"]))); p += f["media"]
    st_ = f.get("status")
    if st_ == "라이브":
        w.append("a.status='live'")
    elif st_ == "종료":
        w.append("a.status IN ('ended','inactive')")
    elif st_ == "OFF":
        w.append("a.status<>'live'")
    if f.get("only_bookmark"):
        w.append("a.is_bookmarked=1")
    if f.get("grade") and f["grade"] in _GRADE_SET:
        w.append(f"s.final_grade IN {_GRADE_SET[f['grade']]}")
    if f.get("search"):
        w.append("(a.brand_name LIKE ? OR a.ad_title LIKE ? OR a.ad_copy LIKE ?)")
        p += [f"%{f['search']}%"] * 3
    return " AND ".join(w), p


def _order(tab: str, sort: str) -> str:
    rank = ("CASE s.final_grade WHEN 'S' THEN 4 WHEN 'A' THEN 3 WHEN 'B' THEN 2 "
            "WHEN 'C' THEN 1 ELSE 0 END")
    if tab == "TOP" or sort == "🔥 터진순(추천)":
        return (f"ORDER BY {rank} DESC, s.engagement_score DESC NULLS LAST, "
                "s.views DESC NULLS LAST, a.collected_at DESC")
    empty_last = "(a.started_at='' OR a.started_at IS NULL) ASC"
    grow_rank = ("CASE a.fatigue_status WHEN '성장 중' THEN 3 WHEN '회복 중' THEN 2 "
                 "WHEN '안정' THEN 1 ELSE 0 END")
    tired_rank = "CASE a.fatigue_status WHEN '피로도 의심' THEN 2 WHEN '정체' THEN 1 ELSE 0 END"
    base = {
        "조회수 높은순": "ORDER BY a.yt_views DESC NULLS LAST, a.collected_at DESC",  # 유튜브 조회수
        "좋아요 높은순": "ORDER BY a.yt_likes DESC NULLS LAST, a.collected_at DESC",
        "급성장순": f"ORDER BY {grow_rank} DESC, a.yt_views DESC NULLS LAST",
        "피로도 의심순": f"ORDER BY {tired_rank} DESC, a.yt_views DESC NULLS LAST",
        "최근 수집순": "ORDER BY a.collected_at DESC",
        "오래된순": "ORDER BY a.collected_at ASC",
        "게재기간 긴순": f"ORDER BY {empty_last}, a.started_at ASC",   # 오래 게재(시작 이른 순)
        "게재기간 짧은순": f"ORDER BY {empty_last}, a.started_at DESC",  # 최근 게재
        "저장 많은순": "ORDER BY a.is_bookmarked DESC, a.score DESC",
    }
    # 조회수 탭 기본 정렬은 조회수 높은순
    if tab == "views" and sort not in base:
        return base["조회수 높은순"]
    return base.get(sort, "ORDER BY a.collected_at DESC")


# 같은 크리에이티브+문구를 쓰는 A/B 변형을 1개로 묶는다(브랜드+매체+카피 시그니처).
# 카피가 비면(구글 등) 영상URL>썸네일>id 로 폴백 → 과합치기 방지.
_DEDUP = ("a.brand_name || '|' || a.media_type || '|' || "
          "COALESCE(NULLIF(TRIM(a.ad_copy),''), NULLIF(a.video_url,''), "
          "NULLIF(a.thumbnail_url,''), a.id)")


# 공유법인(__브랜드 꼬리표)으로 같은 광고가 형제 브랜드마다 복제 저장됨 →
# id에서 '__브랜드' 꼬리표를 떼어낸 base creative id(= 진짜 광고 1개 단위)
_BASE_ID = "CASE WHEN instr(a.id,'__')>0 THEN substr(a.id,1,instr(a.id,'__')-1) ELSE a.id END"


def _group_key(f: dict) -> str:
    """카드 묶음 기준. 기본은 광고별로 노출, 토글 시 A/B(문구) 묶기.
    단 특정 브랜드 필터가 없으면(전체/Meta/Google 탭) 공유법인 복제행을 base creative로
    1개만 노출 → 같은 광고가 형제 브랜드 수만큼 중복으로 보이던 문제 제거."""
    if f.get("merge_variants"):
        return _DEDUP
    if not f.get("brand") or f.get("brand") == "전체":
        return _BASE_ID
    return "a.id"


def count_ads(tab: str, f: dict) -> int:
    where, p = _where(tab, f)
    conn = get_conn()
    n = conn.execute(f"SELECT COUNT(DISTINCT {_group_key(f)}) {_JOIN} WHERE {where}", p).fetchone()[0]
    conn.close()
    return n


def load_ads_page(tab: str, f: dict, page: int = 1, page_size: int = 12) -> list[dict]:
    where, p = _where(tab, f)
    order = _order(tab, f.get("sort", ""))
    conn = get_conn()
    rows = conn.execute(
        f"SELECT {_SUMMARY_COLS} {_JOIN} WHERE {where} "
        f"GROUP BY {_group_key(f)} {order} LIMIT ? OFFSET ?",
        p + [page_size, max(0, (page - 1) * page_size)]).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_ads_by_ids(ids: list) -> list[dict]:
    """광고 ID 다중 검색 — id(=메타 라이브러리 ID 저장 컬럼) 일치 광고만 카드 포맷으로.
       (스키마상 메타 광고 ID는 ad_library_ads.id 에만 저장됨)."""
    ids = [str(i).strip() for i in (ids or []) if str(i).strip()]
    if not ids:
        return []
    conn = get_conn()
    ph = ",".join("?" * len(ids))
    rows = conn.execute(
        f"SELECT {_SUMMARY_COLS} {_JOIN} WHERE a.id IN ({ph}) "
        f"GROUP BY a.id ORDER BY a.collected_at DESC", ids).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_ad_full(ad_id: str) -> Optional[dict]:
    """상세 모달용 — 1건 전체 + 매칭 소셜 + 등급."""
    conn = get_conn()
    r = conn.execute(f"SELECT a.*, m.match_score AS match_score, s.id AS social_id, "
                     "s.views AS social_views, s.likes AS social_likes, "
                     "s.comments AS social_comments, s.shares AS social_shares, "
                     "s.source_url AS social_source_url, s.platform AS social_platform, "
                     "s.engagement_rate AS social_engagement_rate, "
                     "s.engagement_level AS social_engagement_level, "
                     "s.absolute_grade AS social_absolute_grade, "
                     "s.internal_grade AS social_internal_grade, "
                     "s.final_grade AS social_final_grade, "
                     "s.internal_percentile AS social_internal_percentile, "
                     "s.grading_basis AS social_grading_basis "
                     f"{_JOIN} WHERE a.id=?", (ad_id,)).fetchone()
    conn.close()
    if not r:
        return None
    d = dict(r)
    try:
        d["tags"] = json.loads(d.get("tags") or "[]")
    except Exception:  # noqa: BLE001
        d["tags"] = []
    return d


# ── 브랜드 ───────────────────────────────────────────────
def migrate_brands() -> int:
    """ad_library_ads + social_videos 의 brand_name 기준으로 brands 생성 + brand_id 채움."""
    conn = get_conn()
    # 잘못 저장된 URL brand_name 정리(소셜)
    conn.execute("UPDATE social_videos SET brand_name='(미상)' "
                 "WHERE brand_name LIKE 'http%' OR brand_name LIKE '%/%'")
    names = set()
    for tbl in ("ad_library_ads", "social_videos"):
        for r in conn.execute(f"SELECT DISTINCT brand_name FROM {tbl} WHERE brand_name<>''").fetchall():
            nm = (r[0] or "").strip()
            if nm and nm != "(미상)" and not nm.lower().startswith("http"):
                names.add(nm)
    n = 0
    for nm in names:
        if not conn.execute("SELECT 1 FROM brands WHERE display_name=?", (nm,)).fetchone():
            conn.execute("INSERT INTO brands(display_name, search_keywords, is_active, "
                         "created_at, updated_at) VALUES(?,?,?,?,?)",
                         (nm, json.dumps([nm], ensure_ascii=False), 1, _now(), _now()))
            n += 1
    conn.execute("UPDATE ad_library_ads SET brand_id=("
                 "SELECT id FROM brands WHERE brands.display_name=ad_library_ads.brand_name) "
                 "WHERE brand_id IS NULL")
    conn.commit()
    conn.close()
    return n


def brand_exists(display_name: str) -> bool:
    conn = get_conn()
    r = conn.execute("SELECT 1 FROM brands WHERE display_name=?", (display_name,)).fetchone()
    conn.close()
    return bool(r)


def add_brand(display_name: str, keywords: list, official_domain: str = "",
              category: str = "", extra: Optional[dict] = None) -> int:
    """있으면 갱신(upsert): 법인명/도메인/카테고리 채우고 키워드 병합."""
    extra = extra or {}
    conn = get_conn()
    exist = conn.execute("SELECT id, search_keywords FROM brands WHERE display_name=?",
                         (display_name,)).fetchone()
    kws = list(dict.fromkeys(keywords))
    gadv = extra.get("google_advertiser_name", "")
    if exist:
        try:
            kws = list(dict.fromkeys((json.loads(exist["search_keywords"] or "[]")) + kws))
        except Exception:  # noqa: BLE001
            pass
        sets, vals = ["search_keywords=?", "updated_at=?"], [json.dumps(kws, ensure_ascii=False), _now()]
        if official_domain:
            sets.append("official_domain=?"); vals.append(official_domain)
        if category:
            sets.append("category=?"); vals.append(category)
        if gadv:
            sets.append("google_advertiser_name=?"); vals.append(gadv)
        vals.append(display_name)
        conn.execute(f"UPDATE brands SET {','.join(sets)} WHERE display_name=?", vals)
        rid = exist["id"]
    else:
        conn.execute(
            "INSERT INTO brands(display_name, search_keywords, official_domain, "
            "meta_page_name, google_advertiser_name, youtube_channel_name, tiktok_handle, "
            "instagram_handle, category, is_active, created_at, updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,1,?,?)",
            (display_name, json.dumps(kws, ensure_ascii=False),
             official_domain, extra.get("meta_page_name", ""), gadv,
             extra.get("youtube_channel_name", ""), extra.get("tiktok_handle", ""),
             extra.get("instagram_handle", ""), category, _now(), _now()))
        rid = conn.execute("SELECT id FROM brands WHERE display_name=?", (display_name,)).fetchone()[0]
    conn.commit()
    conn.close()
    return rid


def get_brand_keywords(display_name: str) -> list:
    conn = get_conn()
    r = conn.execute("SELECT search_keywords FROM brands WHERE display_name=?",
                     (display_name,)).fetchone()
    conn.close()
    if not r:
        return [display_name]
    try:
        return json.loads(r[0]) or [display_name]
    except Exception:  # noqa: BLE001
        return [display_name]


def find_brand_candidates(query: str, domain: str = "", keywords: Optional[list] = None) -> list[dict]:
    """이미 수집된 데이터(ad_library_ads/social_videos)에서 후보 검색. 크롤 안 함."""
    terms = [t.strip() for t in ([query] + (keywords or [])) if t and t.strip()]
    if not terms and not domain:
        return []
    conn = get_conn()
    cand: dict = {}

    def add(name, source, reason, count, thumb):
        if not name:
            return
        c = cand.setdefault(name, {"name": name, "sources": set(), "reasons": set(),
                                   "ad_count": 0, "thumbs": []})
        c["sources"].add(source)
        c["reasons"].add(reason)
        c["ad_count"] = max(c["ad_count"], count)
        if thumb and len(c["thumbs"]) < 3:
            c["thumbs"].append(thumb)

    like = " OR ".join(["brand_name LIKE ?"] * len(terms)) or "0"
    params = [f"%{t}%" for t in terms]
    if terms:
        for r in conn.execute(
                f"SELECT brand_name, platform, COUNT(*) n, "
                "MAX(thumbnail_url) th FROM ad_library_ads "
                f"WHERE {like} GROUP BY brand_name, platform", params).fetchall():
            add(r["brand_name"], r["platform"] or "DB", "브랜드명 유사", r["n"], r["th"])
        for r in conn.execute(
                f"SELECT brand_name, platform, COUNT(*) n, MAX(thumbnail_url) th "
                f"FROM social_videos WHERE {like} GROUP BY brand_name, platform", params).fetchall():
            add(r["brand_name"], r["platform"] or "social", "브랜드명 유사", r["n"], r["th"])
    if domain:
        d = domain.replace("https://", "").replace("http://", "").split("/")[0]
        for r in conn.execute(
                "SELECT brand_name, platform, COUNT(*) n, MAX(thumbnail_url) th "
                "FROM ad_library_ads WHERE landing_url LIKE ? GROUP BY brand_name, platform",
                (f"%{d}%",)).fetchall():
            add(r["brand_name"], r["platform"] or "DB", f"도메인 일치({d})", r["n"], r["th"])
    conn.close()
    out = [{**c, "sources": sorted(c["sources"]), "reasons": sorted(c["reasons"])}
           for c in cand.values()]
    return sorted(out, key=lambda x: -x["ad_count"])


def log_brand_collection(brand_id: int, platform: str, keyword: str, status: str,
                         found: int = 0, saved: int = 0, skipped: int = 0,
                         error: str = "", started: str = "", finished: str = "") -> None:
    conn = get_conn()
    conn.execute(
        "INSERT INTO brand_collection_logs(brand_id, platform, keyword, status, found_count, "
        "saved_count, skipped_count, error_message, started_at, finished_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?)",
        (brand_id, platform, keyword, status, found, saved, skipped, error,
         started or _now(), finished or _now()))
    conn.commit()
    conn.close()


def latest_brand_status(display_name: str) -> Optional[dict]:
    conn = get_conn()
    r = conn.execute(
        "SELECT l.status, l.finished_at FROM brand_collection_logs l "
        "JOIN brands b ON b.id=l.brand_id WHERE b.display_name=? "
        "ORDER BY l.id DESC LIMIT 1", (display_name,)).fetchone()
    conn.close()
    return dict(r) if r else None


def filter_options() -> dict:
    conn = get_conn()
    plats = [r[0] for r in conn.execute(
        "SELECT DISTINCT platform FROM ad_library_ads WHERE platform<>''").fetchall()]
    media = [r[0] for r in conn.execute(
        "SELECT DISTINCT media_type FROM ad_library_ads WHERE media_type<>''").fetchall()]
    cats: set = set()
    for r in conn.execute("SELECT tags FROM ad_library_ads WHERE tags NOT IN ('','[]') "
                          "AND tags IS NOT NULL").fetchall():
        try:
            cats.update(json.loads(r[0]))
        except Exception:  # noqa: BLE001
            pass
    conn.close()
    return {"platforms": sorted(plats), "media": sorted(media), "categories": sorted(cats)}


def brand_counts() -> list[dict]:
    """등록 브랜드 전체. 📺소셜수는 approved 기준 + needs/rejected 별도(툴팁용)."""
    conn = get_conn()
    rows = conn.execute("""
        SELECT b.display_name,
          (SELECT COUNT(*) FROM ad_library_ads a WHERE a.brand_name=b.display_name) ad_n,
          (SELECT COUNT(*) FROM ad_library_ads a WHERE a.brand_name=b.display_name
             AND a.platform='meta') meta_n,
          (SELECT COUNT(*) FROM ad_library_ads a WHERE a.brand_name=b.display_name
             AND a.platform='google') google_n,
          (SELECT COALESCE(MAX(CASE WHEN a.status='live' THEN 1 ELSE 0 END),0)
             FROM ad_library_ads a WHERE a.brand_name=b.display_name) live,
          (SELECT COUNT(*) FROM social_videos s WHERE s.brand_name=b.display_name
             AND s.review_status='approved') soc_ok,
          (SELECT COUNT(*) FROM social_videos s WHERE s.brand_name=b.display_name
             AND s.review_status='needs_review') soc_rev,
          (SELECT COUNT(*) FROM social_videos s WHERE s.brand_name=b.display_name
             AND s.review_status='rejected') soc_rej
        FROM brands b WHERE b.is_active=1
        ORDER BY (ad_n + soc_ok + soc_rev) DESC, b.display_name
    """).fetchall()
    conn.close()
    return [{"name": r["display_name"], "ad": r["ad_n"],
             "meta": r["meta_n"], "google": r["google_n"], "live": r["live"],
             "approved": r["soc_ok"], "needs": r["soc_rev"], "rejected": r["soc_rej"]}
            for r in rows]


def brand_diagnostics() -> list[dict]:
    """브랜드별 수집/매칭 상태 진단(읽기 전용). 0건 원인 분류 포함."""
    conn = get_conn()
    out = []
    for b in conn.execute("SELECT * FROM brands WHERE is_active=1").fetchall():
        bn, bid = b["display_name"], b["id"]
        ad = conn.execute("SELECT COUNT(*) FROM ad_library_ads WHERE brand_name=?", (bn,)).fetchone()[0]
        sc = conn.execute(
            "SELECT review_status, COUNT(*) n FROM social_videos WHERE brand_name=? "
            "GROUP BY review_status", (bn,)).fetchall()
        scd = {r["review_status"]: r["n"] for r in sc}
        ap, rv, rj = scd.get("approved", 0), scd.get("needs_review", 0), scd.get("rejected", 0)
        logs = conn.execute(
            "SELECT platform, SUM(found_count) f, SUM(saved_count) s, COUNT(*) tries "
            "FROM brand_collection_logs WHERE brand_id=? GROUP BY platform", (bid,)).fetchall()
        plat = {r["platform"]: {"found": r["f"] or 0, "saved": r["s"] or 0, "tries": r["tries"]}
                for r in logs}
        has_kw = len((b["search_keywords"] or "[]")) > 4
        has_official = any(b[k] for k in ("official_domain", "youtube_channel_name",
                                          "tiktok_handle", "instagram_handle", "meta_page_name"))
        total = ap + rv + rj
        if (ap + ad) > 0:
            cause = "ok"                       # 광고 있거나 소셜 승인 있으면 정상
        elif not logs and total == 0:
            cause = "not_collected"
        elif total == 0:
            cause = "no_result"
        elif rv > 0:
            cause = "needs_review_only"
        elif rj > 0:
            cause = "rejected_only"
        else:
            cause = "unknown"
        action = {
            "not_collected": "수집 미실행 → '소량 재수집' 또는 jobs/crawl_brand.py 실행",
            "no_result": "검색어/핸들 부족 가능 → search_keywords 확장",
            "needs_review_only": "데이터 있음(승인 전) → 소셜탭 '검토 필요 포함' 후 '이 브랜드 맞음' 승인 / 공식핸들 등록 시 자동승인",
            "rejected_only": "영상이 브랜드와 무관 판정 → 공식 채널/도메인 등록 또는 키워드 정확화",
            "ok": "정상", "unknown": "확인 필요",
        }[cause]
        out.append({
            "브랜드": bn, "광고": ad, "소셜승인": ap, "검토필요": rv, "제외": rj,
            "Meta": f"{plat.get('meta',{}).get('saved',0)}/{plat.get('meta',{}).get('found',0)}" if 'meta' in plat else "-",
            "Google": f"{plat.get('google',{}).get('saved',0)}/{plat.get('google',{}).get('found',0)}" if 'google' in plat else "-",
            "YouTube": f"{plat.get('youtube',{}).get('saved',0)}/{plat.get('youtube',{}).get('found',0)}" if 'youtube' in plat else "-",
            "키워드": "O" if has_kw else "부족", "공식정보": "O" if has_official else "없음",
            "원인": cause, "조치": action,
        })
    conn.close()
    out.sort(key=lambda x: (x["원인"] != "ok", -(x["광고"] + x["소셜승인"])))
    return out


def insight_summary() -> dict:
    conn = get_conn()
    r = conn.execute(
        "SELECT COUNT(*) total, "
        "SUM(CASE WHEN media_type='video' THEN 1 ELSE 0 END) videos, "
        "SUM(CASE WHEN status='live' THEN 1 ELSE 0 END) live, "
        "SUM(is_bookmarked) bm FROM ad_library_ads").fetchone()
    conn.close()
    return {"total": r["total"] or 0, "videos": r["videos"] or 0,
            "live": r["live"] or 0, "bm": r["bm"] or 0}


def _fix_google_urls(conn: sqlite3.Connection) -> int:
    """기존 google 행의 상대경로/localhost transparency URL 을 절대 URL 로 교정."""
    from services.urls import normalize_google_transparency_url as N
    rows = conn.execute(
        "SELECT id, original_ad_url, transparency_url FROM ad_library_ads "
        "WHERE platform='google'").fetchall()
    fixed = 0
    for r in rows:
        cur = (r["transparency_url"] or "") or (r["original_ad_url"] or "")
        good = N(cur)
        if good and good != (r["transparency_url"] or ""):
            conn.execute("UPDATE ad_library_ads SET transparency_url=?, original_ad_url=? WHERE id=?",
                         (good, good, r["id"]))
            fixed += 1
    if fixed:
        conn.commit()
    return fixed


# ── 적재 ─────────────────────────────────────────────────
def _quick_score(ad: dict) -> int:
    s = 40
    if ad.get("media_type") == "video":
        s += 25
    if ad.get("status") == "live":
        s += 20
    if ad.get("thumbnail_url"):
        s += 10
    if len((ad.get("ad_copy") or ad.get("ad_text") or "")) > 40:
        s += 10
    return max(0, min(100, s))


def ingest_ad_library(ads: list[dict]) -> int:
    conn = get_conn()
    n = 0
    for a in ads:
        aid = a.get("id") or a.get("platform_ad_id")
        if not aid:
            continue
        tags = a.get("tags") or (a.get("hook_tags") or []) + (a.get("format_tags") or [])
        prev = conn.execute("SELECT * FROM ad_library_ads WHERE id=?", (aid,)).fetchone()
        prev = dict(prev) if prev else None
        # ── Meta 영상 URL 추적: video_url 은 만료 임시값으로 취급 ──
        _now_ts = _now()
        _vu = a.get("video_url") or ""
        _is_video = (a.get("media_type") or a.get("ad_format") or "") == "video"
        _has_vu = _vu.startswith("http")
        if _is_video and _has_vu:
            _vstatus = "ok"                       # 방금 받은 신선한 URL(만료는 렌더 시 동적판정)
            _vu_updated = _now_ts
        elif _is_video:
            _vstatus = "private_or_deleted"       # 영상 광고인데 URL 못 가져옴 → 비공개/삭제 가능
            _vu_updated = (prev or {}).get("video_url_updated_at") or ""
        else:
            _vstatus = ""                         # 이미지 등 비영상
            _vu_updated = (prev or {}).get("video_url_updated_at") or ""
        row = {
            "id": aid, "brand_name": a.get("brand_name") or a.get("advertiser_name") or "(미상)",
            "ad_title": a.get("ad_title") or a.get("headline") or "",
            "ad_copy": a.get("ad_copy") or a.get("ad_text") or "",
            "platform": a.get("platform") or "", "media_type": a.get("media_type") or "unknown",
            "ad_format": a.get("ad_format") or a.get("media_type") or "unknown",
            "thumbnail_url": a.get("thumbnail_url") or "", "video_url": a.get("video_url") or "",
            "landing_url": a.get("landing_url") or "", "original_ad_url": a.get("original_ad_url") or "",
            "transparency_url": a.get("transparency_url") or "", "media_url": a.get("media_url") or "",
            "preview_url": a.get("preview_url") or "",
            "status": a.get("status") or "live", "started_at": a.get("started_at") or a.get("first_seen") or "",
            "collected_at": str(a.get("collected_at") or _now())[:19],
            "score": (prev["score"] if prev and prev["score"] else _quick_score(a)),
            "category": (a.get("format_tags") or ["기타"])[0] if a.get("format_tags") else "기타",
            "tags": json.dumps(tags, ensure_ascii=False),
            "is_bookmarked": prev["is_bookmarked"] if prev else 0,
            "memo": prev["memo"] if prev else "",
            "created_at": a.get("created_at") or _now(), "updated_at": _now(),
            "scrape_status": a.get("scrape_status") or "ok",
            "error_message": a.get("error_message") or "",
            "platforms": a.get("platforms") or "",
            "local_thumbnail_path": a.get("local_thumbnail_path") or "",
            # 재수집 시 생성된 스크립트는 보존
            "script_text": (prev or {}).get("script_text") or "",
            "script_source": (prev or {}).get("script_source") or "",
            "script_status": (prev or {}).get("script_status") or "pending",
            "script_error_message": (prev or {}).get("script_error_message") or "",
            "script_created_at": (prev or {}).get("script_created_at") or "",
            "script_updated_at": (prev or {}).get("script_updated_at") or "",
            "cta": a.get("cta") or (prev or {}).get("cta") or "",
            "ad_variant_count": max(int(a.get("ad_variant_count") or 1),
                                    int((prev or {}).get("ad_variant_count") or 1)),
            "yt_views": int(a.get("yt_views") or (prev or {}).get("yt_views") or 0),
            "yt_likes": int(a.get("yt_likes") or (prev or {}).get("yt_likes") or 0),
            "yt_comments": int(a.get("yt_comments") or (prev or {}).get("yt_comments") or 0),
            "detail_status": a.get("detail_status") or (prev or {}).get("detail_status") or "",
            "video_url_updated_at": _vu_updated,
            "last_crawled_at": _now_ts,        # 이번 크롤로 row 갱신됨(ad_id upsert)
            "video_status": _vstatus,
            "page_id": a.get("page_id") or (prev or {}).get("page_id") or "",
            "last_seen_at": _now_ts,           # 이번 크롤에서 라이브러리에 보였음
            "advertiser_name": a.get("advertiser_name") or (prev or {}).get("advertiser_name") or "",
            # 매칭 결과는 별도 매칭단계가 세팅 → 재수집 시 보존(수동지정 우선)
            "brand_status": (prev or {}).get("brand_status") or "",
            "match_method": (prev or {}).get("match_method") or "",
            "match_confidence": (prev or {}).get("match_confidence") or "",
            "manual_override": (prev or {}).get("manual_override") or 0,
        }
        conn.execute(f"INSERT OR REPLACE INTO ad_library_ads({','.join(AD_COLS)}) "
                     f"VALUES({','.join(['?']*len(AD_COLS))})", tuple(row[c] for c in AD_COLS))
        n += 1
    conn.commit()
    conn.close()
    return n


def mark_video_expired(ad_id: str) -> None:
    """앱에서 st.video 재생 실패가 감지된 광고를 expired_url 로 기록(다음 크롤 우선 갱신 대상)."""
    conn = get_conn()
    conn.execute("UPDATE ad_library_ads SET video_status='expired_url' "
                 "WHERE id=? AND platform='meta' AND COALESCE(video_status,'')<>'private_or_deleted'",
                 (ad_id,))
    conn.commit()
    conn.close()


def expired_video_brands() -> list[str]:
    """만료/재생불가 영상이 있는 브랜드 — 많은 순. 5시 크롤이 우선 갱신하도록 정렬용."""
    from services.urls import meta_video_state
    conn = get_conn()
    rows = conn.execute("SELECT brand_name, video_url, video_status, media_type, ad_format "
                        "FROM ad_library_ads WHERE platform='meta' AND media_type='video'").fetchall()
    conn.close()
    cnt: dict = {}
    for r in rows:
        d = dict(r)
        if d.get("video_status") == "expired_url" or meta_video_state(d) == "expired_url":
            cnt[d["brand_name"]] = cnt.get(d["brand_name"], 0) + 1
    return [b for b, _ in sorted(cnt.items(), key=lambda x: -x[1])]


def finalize_meta_video_status(run_start: str, brands: Optional[list] = None) -> dict:
    """크롤 직후 호출: meta 영상 row 의 video_status 를 확정.
       - 이번 실행에서 신선한 URL로 갱신됨 → ok
       - 이번 실행에서 다시 안 잡힘(last_crawled_at < run_start) + 만료/URL없음 → private_or_deleted
       - 크롤됐는데도 URL 없음/만료 → unavailable / expired_url
       brands 지정 시 해당 브랜드만(부분 크롤에서 다른 브랜드를 오판하지 않도록).
    """
    from services.urls import meta_video_state
    conn = get_conn()
    q = "SELECT * FROM ad_library_ads WHERE platform='meta' AND media_type='video'"
    params: list = []
    if brands:
        q += " AND brand_name IN (%s)" % ",".join("?" * len(brands))
        params = list(brands)
    rows = conn.execute(q, params).fetchall()
    counts: dict = {}
    for r in rows:
        d = dict(r)
        state = meta_video_state(d)              # ok / expired_url / unavailable
        refreshed = (d.get("last_crawled_at") or "") >= run_start
        if state == "ok":
            final = "ok"
        elif not refreshed:
            final = "private_or_deleted"          # 재크롤에서 다시 안 보임 → 비공개/삭제 추정
        else:
            final = state or "unavailable"        # 크롤됐는데도 만료/없음
        conn.execute("UPDATE ad_library_ads SET video_status=? WHERE id=?", (final, d["id"]))
        counts[final] = counts.get(final, 0) + 1
    conn.commit()
    conn.close()
    return counts


def backfill_video_status() -> dict:
    """모든 meta 영상 row 의 video_status 를 현재 video_url 만료여부로 채움(1회/유지보수용).
       - http URL + 미만료 → ok / + 만료 → expired_url / URL 없음 → unavailable
       - 크롤로 확정된 private_or_deleted·not_found 는 보존(되돌리지 않음).
       앱이 빈 status 를 만나 검은 플레이어를 띄우는 일을 방지."""
    from services.urls import fbcdn_url_expired
    conn = get_conn()
    rows = conn.execute("SELECT id, video_url, video_status FROM ad_library_ads "
                        "WHERE platform='meta' AND media_type='video'").fetchall()
    counts: dict = {}
    for r in rows:
        cur = (r["video_status"] or "").strip()
        if cur in ("private_or_deleted", "not_found"):
            counts[cur] = counts.get(cur, 0) + 1
            continue
        vu = (r["video_url"] or "").strip()
        if vu.startswith("http"):
            state = "expired_url" if fbcdn_url_expired(vu) else "ok"
        else:
            state = "unavailable"
        conn.execute("UPDATE ad_library_ads SET video_status=? WHERE id=?", (state, r["id"]))
        counts[state] = counts.get(state, 0) + 1
    conn.commit()
    conn.close()
    return counts


def regenerate_demo_db() -> None:
    """배포용 clean 데모 DB 생성. WAL 체크포인트 후 복사(미체크포인트 최신 커밋 누락 방지)
       → users 제거 → VACUUM. 잡에서 git push 전에 호출."""
    import shutil
    # WAL 의 미반영 커밋을 본 .db 파일로 합치고(TRUNCATE) 복사 — 안 하면 demo.db 가 옛 데이터
    conn = get_conn()
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        conn.close()
    sample = ROOT / "sample_data"
    sample.mkdir(parents=True, exist_ok=True)
    demo = sample / "demo.db"
    shutil.copy(DB_PATH, demo)
    con = sqlite3.connect(demo)
    con.isolation_level = None
    con.execute("DELETE FROM users")
    # 빌드 시각 스탬프 — 클라우드에서 '최신 demo.db 를 보는지' 화면에서 대조 가능
    con.execute("CREATE TABLE IF NOT EXISTS db_build (built_at TEXT)")
    con.execute("DELETE FROM db_build")
    con.execute("INSERT INTO db_build(built_at) VALUES(?)",
                (datetime.now().strftime("%Y-%m-%d %H:%M"),))
    con.execute("VACUUM")
    con.close()


def get_db_build() -> str:
    """현재 DB(클라우드는 demo.db 시드본)의 빌드 시각. 없으면 ''."""
    conn = get_conn()
    try:
        r = conn.execute("SELECT built_at FROM db_build LIMIT 1").fetchone()
        return r[0] if r else ""
    except Exception:  # noqa: BLE001
        return ""
    finally:
        conn.close()


def get_brand(display_name: str) -> Optional[dict]:
    conn = get_conn()
    r = conn.execute("SELECT * FROM brands WHERE display_name=?", (display_name,)).fetchone()
    conn.close()
    return dict(r) if r else None


def all_brand_collection_status() -> list[dict]:
    """전 브랜드 Meta 수집 상태를 1패스로 계산(관리 화면용)."""
    conn = get_conn()
    brands = conn.execute(
        "SELECT display_name, meta_page_id, page_id_status, meta_reported_count FROM brands "
        "WHERE COALESCE(is_active,1)=1").fetchall()
    rows = conn.execute(
        "SELECT brand_name, media_type, video_status, last_seen_at, collected_at "
        "FROM ad_library_ads WHERE platform='meta' AND COALESCE(is_excluded,0)=0").fetchall()
    conn.close()
    agg: dict = {}
    for r in rows:
        a = agg.setdefault(r["brand_name"], {"total": 0, "vid": 0, "gone": 0, "last": ""})
        a["total"] += 1
        if r["media_type"] == "video":
            a["vid"] += 1
            if (r["video_status"] or "") in ("expired_url", "private_or_deleted", "not_found"):
                a["gone"] += 1
        ls = str(r["last_seen_at"] or r["collected_at"] or "")[:16]
        if ls > a["last"]:
            a["last"] = ls
    out = []
    for b in brands:
        name = b["display_name"]
        a = agg.get(name, {"total": 0, "vid": 0, "gone": 0, "last": ""})
        has_pid = bool((b["meta_page_id"] or "").strip())
        ratio = (a["gone"] / a["vid"]) if a["vid"] else 0.0
        reported = int(b["meta_reported_count"] or 0)
        rate = (a["total"] / reported) if reported else None
        if reported and rate is not None and rate < 0.7:
            label = "누락 의심"
        elif a["total"] < 20 and not has_pid:
            label = "page_id 확인 필요"
        elif a["total"] < 20:
            label = "확인 필요"
        elif ratio >= 0.6:
            label = "만료 많음"
        elif has_pid:
            label = "정상"
        else:
            label = "얕은 수집"
        out.append({"brand": name, "method": "page_id" if has_pid else "keyword",
                    "page_id": b["meta_page_id"] or "", "count": a["total"], "video": a["vid"],
                    "gone": a["gone"], "last": a["last"], "status": label,
                    "reported": reported, "rate": (round(rate * 100) if rate is not None else None)})
    # 인덱스·요일 그룹 부여
    ig = brand_index_groups()
    for o in out:
        gi = ig.get(o["brand"], {})
        o["index"] = gi.get("index", 0)
        o["group"] = gi.get("group", "-")
    order = {"누락 의심": 0, "page_id 확인 필요": 1, "확인 필요": 2, "얕은 수집": 3, "만료 많음": 4, "정상": 5}
    out.sort(key=lambda x: (order.get(x["status"], 9), -x["count"]))
    return out


def set_brand_page_id(display_name: str, page_id: str, status: str = "confirmed") -> None:
    """브랜드의 Meta page_id 설정(수동 입력 or 자동 추출). status: candidate/confirmed."""
    conn = get_conn()
    conn.execute("UPDATE brands SET meta_page_id=?, page_id_status=?, updated_at=? WHERE display_name=?",
                 (str(page_id).strip(), status, _now(), display_name))
    conn.commit()
    conn.close()


_WEEKDAYS = ["월", "화", "수", "목", "금", "토", "일"]


def recompute_google_matches() -> dict:
    """전 구글 광고에 브랜드 매칭 적용. advertiser_name 비면 현재 brand 의 법인명으로 백필.
       수동지정(manual_override=1)은 보존. 반환 상태별 카운트."""
    import services.google_match as GM
    conn = get_conn()
    reg = GM.build_registry(conn)
    rules = GM.load_rules(conn)
    # 법인명(google_advertiser_name) 매핑
    legal = {r["display_name"]: (r["google_advertiser_name"] or "")
             for r in conn.execute("SELECT display_name, google_advertiser_name FROM brands").fetchall()}
    rows = conn.execute("SELECT * FROM ad_library_ads WHERE platform='google'").fetchall()
    counts: dict = {}
    for r in rows:
        d = dict(r)
        if d.get("manual_override"):
            counts["manual"] = counts.get("manual", 0) + 1
            continue
        if not (d.get("advertiser_name") or "").strip():
            d["advertiser_name"] = legal.get(d.get("brand_name"), "")
        m = GM.match_ad(d, reg, rules)
        # 확정/추정이면 실제 브랜드로 재태깅, 미확정/미매칭은 brand_name 유지(상태로만 분리)
        new_brand = m["brand"] if m["status"] in ("confirmed", "estimated") and m["brand"] else d.get("brand_name")
        conn.execute(
            "UPDATE ad_library_ads SET advertiser_name=?, brand_name=?, brand_status=?, "
            "match_method=?, match_confidence=?, match_reason=? WHERE id=?",
            (d["advertiser_name"], new_brand, m["status"], m["method"], m["confidence"],
             m.get("reason", ""), d["id"]))
        counts[m["status"]] = counts.get(m["status"], 0) + 1
    conn.commit()
    conn.close()
    return counts


def google_review_ads(limit: int = 300) -> list[dict]:
    """브랜드 미확정(company_only)·미매칭 구글 광고 — 리뷰함."""
    conn = get_conn()
    rows = conn.execute(
        f"SELECT {_SUMMARY_COLS}, a.advertiser_name, a.brand_status, a.match_method, "
        f"a.match_reason, a.transparency_url, a.original_ad_url "
        f"{_JOIN} WHERE a.platform='google' AND a.brand_status IN ('company_only','unmatched') "
        f"AND COALESCE(a.is_excluded,0)=0 GROUP BY a.id ORDER BY a.advertiser_name, a.collected_at DESC "
        f"LIMIT ?", [limit]).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def google_excluded_ads(limit: int = 120) -> list[dict]:
    """레퍼런스 제외 처리된 구글 광고 — 제외 탭."""
    conn = get_conn()
    rows = conn.execute(
        f"SELECT {_SUMMARY_COLS}, a.advertiser_name, a.brand_status, a.match_reason, "
        f"a.transparency_url, a.original_ad_url "
        f"{_JOIN} WHERE a.platform='google' AND "
        f"(a.brand_status='reference_excluded' OR COALESCE(a.is_excluded,0)=1) "
        f"GROUP BY a.id ORDER BY a.collected_at DESC LIMIT ?", [limit]).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def restore_google_excluded(ad_id: str) -> None:
    """제외 취소 → 다시 미확정으로 되돌림."""
    conn = get_conn()
    conn.execute("UPDATE ad_library_ads SET is_excluded=0, brand_status='company_only', "
                 "manual_override=0, match_reason='제외 취소' WHERE id=?", (ad_id,))
    conn.commit()
    conn.close()


def google_status_counts() -> dict:
    conn = get_conn()
    rows = conn.execute("SELECT COALESCE(NULLIF(brand_status,''),'(미분류)') s, COUNT(*) c "
                        "FROM ad_library_ads WHERE platform='google' AND COALESCE(is_excluded,0)=0 "
                        "GROUP BY s").fetchall()
    conn.close()
    return {r[0]: r[1] for r in rows}


def assign_google_brand(ad_id: str, brand: str = "", exclude: bool = False,
                        unsure: bool = False, learn: bool = True) -> None:
    """리뷰함 수동 처리: 브랜드 지정 / 브랜드 미확정(보류) / 레퍼런스 제외. manual_override 저장."""
    import services.google_match as GM
    conn = get_conn()
    if exclude:   # 레퍼런스 제외 → 제외 탭 + 학습(같은 광고주 광고는 다음부터 자동 제외)
        row = conn.execute("SELECT transparency_url, original_ad_url FROM ad_library_ads WHERE id=?",
                           (ad_id,)).fetchone()
        conn.execute("UPDATE ad_library_ads SET is_excluded=1, brand_status='reference_excluded', "
                     "manual_override=1, match_method='manual', match_reason='레퍼런스 제외' WHERE id=?",
                     (ad_id,))
        if learn and row:
            arid = GM.advertiser_id(dict(row))
            if arid:   # 이 광고주(AR ID) = 레퍼런스 제외 규칙 등록 + 같은 AR ID 일괄 제외
                conn.execute("INSERT OR REPLACE INTO brand_match_rules(pattern_type,pattern,brand_name,created_at)"
                             " VALUES('exclude_advertiser_id',?,'(제외)',?)", (arid, _now()))
                conn.execute(
                    "UPDATE ad_library_ads SET is_excluded=1, brand_status='reference_excluded', "
                    "match_reason='레퍼런스 제외(학습: 같은 광고주)' WHERE platform='google' "
                    "AND COALESCE(brand_status,'')<>'reference_excluded' "
                    "AND (transparency_url LIKE ? OR original_ad_url LIKE ?)",
                    (f"%advertiser/{arid}%", f"%advertiser/{arid}%"))
        conn.commit(); conn.close()
        return
    if unsure:   # 브랜드 미확정(보류) — 확인했지만 브랜드 모름, 리뷰함 유지(재계산에 안 덮임)
        conn.execute("UPDATE ad_library_ads SET brand_status='company_only', manual_override=1, "
                     "match_method='manual', match_reason='수동 확인: 브랜드 미확정' WHERE id=?", (ad_id,))
        conn.commit(); conn.close()
        return
    import services.google_match as GM
    row = conn.execute("SELECT landing_url, transparency_url, original_ad_url FROM ad_library_ads "
                       "WHERE id=?", (ad_id,)).fetchone()
    conn.execute("UPDATE ad_library_ads SET brand_name=?, brand_status='confirmed', "
                 "match_method='manual', match_confidence='high', match_reason='수동 지정', "
                 "manual_override=1, is_excluded=0 WHERE id=?", (brand, ad_id))
    if learn and row:
        d = dict(row)
        # 학습 1: 광고주 AR ID → 같은 AR ID 광고 전부 자동확정(핵심)
        arid = GM.advertiser_id(d)
        if arid:
            conn.execute("INSERT OR REPLACE INTO brand_match_rules(pattern_type,pattern,brand_name,created_at) "
                         "VALUES('advertiser_id',?,?,?)", (arid, brand, _now()))
            # 같은 AR ID 의 다른 광고도 즉시 확정(수동지정 제외)
            conn.execute(
                "UPDATE ad_library_ads SET brand_name=?, brand_status='confirmed', "
                "match_method='learned_advertiser_id', match_confidence='high', "
                "match_reason=? WHERE platform='google' AND COALESCE(manual_override,0)=0 "
                "AND (transparency_url LIKE ? OR original_ad_url LIKE ?)",
                (brand, f"AR ID 학습: {arid}", f"%advertiser/{arid}%", f"%advertiser/{arid}%"))
        # 학습 2: 랜딩 도메인(있으면)
        dom = (d.get("landing_url") or "").lower().replace("https://", "").replace("http://", "").split("/")[0]
        if dom:
            conn.execute("INSERT OR REPLACE INTO brand_match_rules(pattern_type,pattern,brand_name,created_at) "
                         "VALUES('domain',?,?,?)", (dom, brand, _now()))
    conn.commit()
    conn.close()


def recent_brand_logs(display_name: str, limit: int = 8) -> list[dict]:
    """브랜드 최근 수집 로그(관리화면 표시용)."""
    conn = get_conn()
    bid = (conn.execute("SELECT id FROM brands WHERE display_name=?", (display_name,)).fetchone() or [0])[0]
    rows = conn.execute(
        "SELECT platform, keyword AS method, status, found_count, saved_count AS new_count, "
        "skipped_count AS updated_count, started_at FROM brand_collection_logs "
        "WHERE brand_id=? ORDER BY id DESC LIMIT ?", (bid, limit)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def brand_index_groups() -> dict:
    """전 브랜드에 안정적 인덱스(1..N, brands.id 순) + 요일 그룹(월~일 균등분배) 부여.
       브랜드 추가/삭제 시 자동 재분배. 반환 {display_name: {index, weekday(0~6), group}}."""
    conn = get_conn()
    names = [r[0] for r in conn.execute(
        "SELECT display_name FROM brands WHERE COALESCE(is_active,1)=1 "
        "ORDER BY COALESCE(sort_order, id), id").fetchall()]
    conn.close()
    n = len(names) or 1
    out = {}
    for p, b in enumerate(names):
        wd = min(6, (p * 7) // n)        # 0=월 .. 6=일, 연속 균등 분배
        out[b] = {"index": p + 1, "weekday": wd, "group": _WEEKDAYS[wd]}
    return out


def swap_brand_order(a: str, b: str) -> bool:
    """두 브랜드의 정렬 순서(sort_order) 교체 — 사이드바/요일그룹 순서 자리바꿈."""
    conn = get_conn()
    ra = conn.execute("SELECT sort_order, id FROM brands WHERE display_name=?", (a,)).fetchone()
    rb = conn.execute("SELECT sort_order, id FROM brands WHERE display_name=?", (b,)).fetchone()
    if not ra or not rb:
        conn.close()
        return False
    sa = ra[0] if ra[0] is not None else ra[1]
    sb = rb[0] if rb[0] is not None else rb[1]
    conn.execute("UPDATE brands SET sort_order=? WHERE display_name=?", (sb, a))
    conn.execute("UPDATE brands SET sort_order=? WHERE display_name=?", (sa, b))
    conn.commit()
    conn.close()
    return True


def brands_for_weekday(wd: int) -> list:
    """해당 요일(0=월..6=일)에 수집할 브랜드 목록(인덱스 순)."""
    g = brand_index_groups()
    return [b for b, info in sorted(g.items(), key=lambda x: x[1]["index"]) if info["weekday"] == wd]


def set_brand_reported(display_name: str, reported: int) -> None:
    """라이브러리 표기 결과수 저장(최댓값 유지) — 수집률 비교용."""
    if not reported:
        return
    conn = get_conn()
    conn.execute("UPDATE brands SET meta_reported_count=MAX(COALESCE(meta_reported_count,0),?) "
                 "WHERE display_name=?", (int(reported), display_name))
    conn.commit()
    conn.close()


def existing_ad_ids(ids: list) -> set:
    """주어진 ad_id 중 이미 DB에 있는 것(신규/갱신 구분용)."""
    ids = [str(i) for i in ids if i]
    if not ids:
        return set()
    conn = get_conn()
    ph = ",".join("?" * len(ids))
    rows = conn.execute(f"SELECT id FROM ad_library_ads WHERE id IN ({ph})", ids).fetchall()
    conn.close()
    return {r[0] for r in rows}


def brand_collection_status(display_name: str) -> dict:
    """브랜드별 Meta 수집 상태 판정.
       method: page_id/keyword · count · expired/private 비율 → 상태 라벨."""
    b = get_brand(display_name) or {}
    has_pid = bool((b.get("meta_page_id") or "").strip())
    conn = get_conn()
    rows = conn.execute(
        "SELECT video_status, media_type, last_seen_at, collected_at FROM ad_library_ads "
        "WHERE brand_name=? AND platform='meta' AND COALESCE(is_excluded,0)=0", (display_name,)).fetchall()
    conn.close()
    total = len(rows)
    vids = [r for r in rows if r["media_type"] == "video"]
    gone = sum(1 for r in vids if (r["video_status"] or "") in ("expired_url", "private_or_deleted", "not_found"))
    gone_ratio = (gone / len(vids)) if vids else 0.0
    last = max((str(r["last_seen_at"] or r["collected_at"] or "") for r in rows), default="")[:16]
    reported = int(b.get("meta_reported_count") or 0)
    rate = (total / reported) if reported else None
    # 상태 판정(우선순위): 누락의심 > page_id확인 > 확인필요 > 만료많음 > 얕은수집 > 정상
    if reported and rate is not None and rate < 0.7:
        label = "누락 의심"
    elif total < 20 and not has_pid:
        label = "page_id 확인 필요"
    elif total < 20:
        label = "확인 필요"
    elif gone_ratio >= 0.6:
        label = "만료 많음"
    elif has_pid:
        label = "정상"
    else:
        label = "얕은 수집"
    return {"brand": display_name, "method": "page_id" if has_pid else "keyword",
            "page_id": b.get("meta_page_id") or "", "page_id_status": b.get("page_id_status") or "none",
            "count": total, "video": len(vids), "gone": gone, "reported": reported,
            "rate": (round(rate * 100) if rate is not None else None),
            "gone_ratio": round(gone_ratio, 2), "last": last, "status": label}


def ingest_social_videos(vids: list[dict], from_keyword_search: bool = True) -> int:
    """저장 전 브랜드 검증 점수 계산. 브랜드명/키워드가 전혀 없으면 저장 제외."""
    import services.brand_match as BM
    conn = get_conn()
    brand_cache: dict = {}
    n = 0
    for v in vids:
        vid = v.get("id") or v.get("video_id")
        if not vid:
            continue
        bn = v.get("brand_name") or "(미상)"
        if bn not in brand_cache:
            br = conn.execute("SELECT * FROM brands WHERE display_name=?", (bn,)).fetchone()
            brand_cache[bn] = dict(br) if br else {"display_name": bn,
                                                   "search_keywords": json.dumps([bn])}
        m = BM.score(v, brand_cache[bn], from_keyword_search=from_keyword_search)
        if not m["keep"]:
            continue   # 브랜드명/키워드/공식일치 전혀 없음 → 저장 안 함
        row = {
            "id": str(vid), "brand_name": bn,
            "platform": v.get("platform") or "tiktok",
            "video_id": v.get("video_id") or "", "embed_url": v.get("embed_url") or "",
            "title": v.get("title") or "", "channel_title": v.get("channel_title") or "",
            "video_url": v.get("video_url") or "",
            "thumbnail_url": v.get("thumbnail_url") or "", "caption": v.get("caption") or "",
            "views": int(v.get("views") or 0), "likes": int(v.get("likes") or 0),
            "comments": int(v.get("comments") or 0), "shares": int(v.get("shares") or 0),
            "posted_at": v.get("posted_at") or "", "source_url": v.get("source_url") or "",
            "collected_at": str(v.get("collected_at") or _now())[:19],
            "created_at": _now(), "updated_at": _now(),
            "brand_match_score": m["score"], "brand_match_reason": m["reason"],
            "review_status": m["status"],
        }
        conn.execute(f"INSERT OR REPLACE INTO social_videos({','.join(SOCIAL_COLS)}) "
                     f"VALUES({','.join(['?']*len(SOCIAL_COLS))})", tuple(row[c] for c in SOCIAL_COLS))
        n += 1
    conn.commit()
    conn.close()
    return n


def recompute_brand_match() -> dict:
    """기존 social_videos 전체 brand_match 재계산 + review_status 갱신."""
    import services.brand_match as BM
    conn = get_conn()
    socials = [dict(r) for r in conn.execute("SELECT * FROM social_videos").fetchall()]
    brands = {r["display_name"]: dict(r) for r in conn.execute("SELECT * FROM brands").fetchall()}
    stat = {"approved": 0, "needs_review": 0, "rejected": 0}
    for v in socials:
        br = brands.get(v.get("brand_name")) or {"display_name": v.get("brand_name"),
                                                 "search_keywords": json.dumps([v.get("brand_name")])}
        m = BM.score(v, br, from_keyword_search=True)
        conn.execute("UPDATE social_videos SET brand_match_score=?, brand_match_reason=?, "
                     "review_status=?, updated_at=? WHERE id=?",
                     (m["score"], m["reason"], m["status"], _now(), v["id"]))
        stat[m["status"]] = stat.get(m["status"], 0) + 1
    conn.commit()
    conn.close()
    return stat


def update_review_status(social_id: str, status: str) -> None:
    conn = get_conn()
    conn.execute("UPDATE social_videos SET review_status=?, updated_at=? WHERE id=?",
                 (status, _now(), social_id))
    conn.commit()
    conn.close()


def move_social_brand(social_id: str, new_brand: str) -> None:
    conn = get_conn()
    conn.execute("UPDATE social_videos SET brand_name=?, updated_at=? WHERE id=?",
                 (new_brand, _now(), social_id))
    conn.commit()
    conn.close()


# ── 매칭 ─────────────────────────────────────────────────
def compute_matches(threshold: float = 30.0) -> int:
    """광고↔소셜 영상 매칭. 브랜드명만 같은 건 '같은 광고'가 아니므로 제외하고,
    카피/랜딩 토큰이 영상 캡션과 실제로 일치(창작물 수준 확인)될 때만 연결한다.
    → 확인 안 되는 '원본 영상 보기' 링크가 붙지 않는다."""
    import services.matching as M
    conn = get_conn()
    ads = [dict(r) for r in conn.execute("SELECT * FROM ad_library_ads").fetchall()]
    socials = [dict(r) for r in conn.execute("SELECT * FROM social_videos").fetchall()]
    conn.execute("DELETE FROM ad_social_matches")
    n = 0
    by_brand: dict = {}
    for s in socials:
        by_brand.setdefault(s.get("brand_name"), []).append(s)
    for ad in ads:
        # 구글 투명성센터 광고는 카피·랜딩이 없어 영상 단위 확인 불가 → 유튜브 매칭 안 함(트렌드만).
        if (ad.get("platform") or "") == "google":
            continue
        for s in by_brand.get(ad.get("brand_name"), []):
            r = M.match(ad, s)
            # 창작물 수준 확인: 랜딩 상품토큰이 캡션에 있거나(url_sim≈1) 카피가 상당히 유사할 때만.
            # 브랜드명만 일치(copy/url 0)하는 매칭은 버린다 → 엉뚱한 유튜브 연결 방지.
            confirmed = r["brand_match"] and (r["url_sim"] >= 0.6
                                              or r["copy_sim"] >= 0.5
                                              or r["media_sim"] >= 0.5)
            if confirmed and r["match_score"] >= threshold:
                conn.execute(
                    "INSERT OR REPLACE INTO ad_social_matches"
                    "(ad_id,social_id,match_score,brand_match,copy_sim,url_sim,media_sim,created_at)"
                    " VALUES(?,?,?,?,?,?,?,?)",
                    (ad["id"], s["id"], r["match_score"], r["brand_match"],
                     r["copy_sim"], r["url_sim"], r["media_sim"], _now()))
                n += 1
    conn.commit()
    conn.close()
    return n


# ── 조회 ─────────────────────────────────────────────────
def add_snapshot(social_video_id: str, views, likes, comments, shares) -> None:
    """일자별 지표 스냅샷(같은 날짜면 갱신)."""
    from datetime import date
    conn = get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO social_video_snapshots"
        "(social_video_id,snapshot_date,views,likes,comments,shares,created_at) VALUES(?,?,?,?,?,?,?)",
        (social_video_id, date.today().isoformat(), int(views or 0), int(likes or 0),
         int(comments or 0), int(shares or 0), _now()))
    conn.commit()
    conn.close()


def get_snapshots(social_video_id: str, days: int = 7) -> list[dict]:
    conn = get_conn()
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM social_video_snapshots WHERE social_video_id=? "
        "ORDER BY snapshot_date DESC LIMIT ?", (social_video_id, days)).fetchall()]
    conn.close()
    return list(reversed(rows))


def link_ad_social(ad_id: str, social_id: str, score: float = 100.0) -> None:
    """수동으로 광고↔소셜 영상 연결."""
    conn = get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO ad_social_matches"
        "(ad_id,social_id,match_score,brand_match,copy_sim,url_sim,media_sim,created_at)"
        " VALUES(?,?,?,?,?,?,?,?)",
        (ad_id, social_id, score, 1, 0.0, 0.0, 0.0, _now()))
    conn.commit()
    conn.close()


def update_script(social_id: str, text: str, status: str) -> None:
    conn = get_conn()
    conn.execute("UPDATE social_videos SET script_text=?, script_status=?, updated_at=? WHERE id=?",
                 (text, status, _now(), social_id))
    conn.commit()
    conn.close()


def get_social(social_id: str) -> Optional[dict]:
    conn = get_conn()
    r = conn.execute("SELECT * FROM social_videos WHERE id=?", (social_id,)).fetchone()
    conn.close()
    return dict(r) if r else None


def social_count() -> int:
    conn = get_conn()
    n = conn.execute("SELECT COUNT(*) FROM social_videos").fetchone()[0]
    conn.close()
    return n


def add_ad_snapshot(ad_id: str, views, likes, comments) -> None:
    """광고(유튜브 연결)의 일자별 조회수 스냅샷 — 조회수 추이 그래프용(같은 날짜면 갱신)."""
    from datetime import date
    if not ad_id:
        return
    conn = get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO ad_view_snapshots"
        "(ad_id,snapshot_date,views,likes,comments,created_at) VALUES(?,?,?,?,?,?)",
        (ad_id, date.today().isoformat(), int(views or 0), int(likes or 0),
         int(comments or 0), _now()))
    conn.commit()
    conn.close()


def get_ad_snapshots(ad_id: str, days: int = 30) -> list[dict]:
    conn = get_conn()
    rows = [dict(r) for r in conn.execute(
        "SELECT snapshot_date, views, likes, comments FROM ad_view_snapshots "
        "WHERE ad_id=? ORDER BY snapshot_date DESC LIMIT ?", (ad_id, days)).fetchall()]
    conn.close()
    return list(reversed(rows))


def get_script_cache(cache_key: str) -> Optional[dict]:
    """영상 해시/URL 기준 스크립트 캐시 조회 → 같은 영상 재호출 방지."""
    if not cache_key:
        return None
    conn = get_conn()
    r = conn.execute("SELECT segments_json, source FROM video_script_cache WHERE cache_key=?",
                     (cache_key,)).fetchone()
    conn.close()
    return {"text": r[0], "source": r[1]} if r and r[0] else None


def put_script_cache(cache_key: str, segments_json: str, source: str) -> None:
    if not cache_key or not segments_json:
        return
    conn = get_conn()
    conn.execute("INSERT OR REPLACE INTO video_script_cache(cache_key,segments_json,source,created_at)"
                 " VALUES(?,?,?,?)", (cache_key, segments_json, source, _now()))
    conn.commit()
    conn.close()


def ingest_youtube_candidates(rows: list[dict]) -> int:
    """YouTube 광고 매칭 후보 적재(video_id 기준 upsert)."""
    import json as _json
    conn = get_conn()
    n = 0
    for r in rows:
        vid = r.get("video_id")
        if not vid:
            continue
        mb = r.get("matched_by")
        mb = _json.dumps(mb, ensure_ascii=False) if isinstance(mb, (list, dict)) else (mb or "[]")
        status = r.get("match_status") or r.get("classification")
        conn.execute(
            "INSERT OR REPLACE INTO youtube_ad_candidates"
            "(video_id,brand_name,query,advertiser_legal_name,source_account_name,"
            "title,description,channel_title,duration_sec,published_at,has_caption,"
            "thumbnail_url,source_url,views,likes,comments,matching_score,"
            "matching_confidence,match_status,matched_by,classification,signals,"
            "matched_ad_id,collected_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (vid, r.get("brand_name"), r.get("query"), r.get("advertiser_legal_name"),
             r.get("source_account_name") or r.get("channel_title"),
             r.get("title"), r.get("description"), r.get("channel_title"),
             int(r.get("duration_sec") or 0), r.get("published_at"),
             int(r.get("has_caption") or 0), r.get("thumbnail_url"), r.get("source_url"),
             int(r.get("views") or 0), int(r.get("likes") or 0), int(r.get("comments") or 0),
             float(r.get("matching_score") or 0), r.get("matching_confidence"), status, mb,
             status, _json.dumps(r.get("signals") or {}, ensure_ascii=False),
             r.get("matched_ad_id") or "", _now()))
        n += 1
    conn.commit()
    conn.close()
    return n


def get_youtube_candidates(brand_name: str = "", classification: str = "") -> list[dict]:
    """YouTube 광고 매칭 후보 조회(브랜드/상태 필터). 점수 내림차순.
    classification 인자는 match_status 값(youtube_ad_matched/.../not_matched)."""
    w, p = ["1=1"], []
    if brand_name and brand_name != "전체":
        w.append("brand_name=?"); p.append(brand_name)
    if classification:
        w.append("COALESCE(match_status,classification)=?"); p.append(classification)
    conn = get_conn()
    rows = [dict(r) for r in conn.execute(
        f"SELECT * FROM youtube_ad_candidates WHERE {' AND '.join(w)} "
        "ORDER BY matching_score DESC, views DESC", p).fetchall()]
    conn.close()
    return rows


def add_youtube_seed(brand_name: str, url: str) -> dict:
    """사용자가 아는 YouTube 광고 URL을 seed로 등록(자동매칭 보정용).
    video_id 추출 → API로 메타데이터 수집 → 브랜드 연결 → match_status=manual_seed_matched."""
    import json as _json

    import services.youtube as YT
    vid = YT.extract_video_id(url)
    if not vid:
        return {"ok": False, "msg": "유효한 YouTube URL/영상 ID가 아닙니다."}
    if not YT.is_enabled():
        return {"ok": False, "msg": "YOUTUBE_API_KEY가 없어 메타데이터를 가져올 수 없습니다."}
    vids = YT.fetch_videos_detailed([vid])
    if not vids:
        return {"ok": False, "msg": "영상 정보를 가져오지 못했습니다(비공개/삭제/쿼터)."}
    v = vids[0]
    br = get_brand(brand_name) or {}
    row = {**v, "brand_name": brand_name, "query": "(seed)",
           "advertiser_legal_name": (br.get("google_advertiser_name") or ""),
           "source_account_name": v.get("channel_title", ""),
           "matching_score": 100.0, "matching_confidence": "high",
           "match_status": "manual_seed_matched",
           "matched_by": _json.dumps(["수동 시드"], ensure_ascii=False),
           "classification": "manual_seed_matched",
           "signals": _json.dumps({"seed": True}, ensure_ascii=False)}
    ingest_youtube_candidates([row])
    return {"ok": True, "msg": f"시드 등록: {v.get('title','')[:30]} · 채널 {v.get('channel_title','')}",
            "title": v.get("title", ""), "channel": v.get("channel_title", "")}


def brand_seed_channels(brand_name: str) -> list:
    """브랜드의 seed 영상 채널명 목록 — 이후 매칭에서 '광고 채널' 강신호로 사용."""
    if not brand_name:
        return []
    conn = get_conn()
    rows = conn.execute(
        "SELECT DISTINCT channel_title FROM youtube_ad_candidates "
        "WHERE brand_name=? AND match_status='manual_seed_matched' AND channel_title<>''",
        (brand_name,)).fetchall()
    conn.close()
    return [r[0] for r in rows]


def youtube_candidate_counts() -> dict:
    """상태별 후보 개수(match_status 기준)."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT COALESCE(match_status,classification) s, COUNT(*) n "
        "FROM youtube_ad_candidates GROUP BY s"
    ).fetchall()
    conn.close()
    return {r[0]: r[1] for r in rows}


def brand_youtube_summary(brand_name: str) -> dict:
    """브랜드 단위 유튜브 존재 여부 — '이 브랜드가 유튜브에도 광고/영상을 돌리는지'.
    (특정 광고↔영상 1:1 매칭이 아니라 브랜드 차원의 참고용)"""
    out = {"count": 0, "max_views": 0, "top": None, "videos": []}
    if not brand_name:
        return out
    conn = get_conn()
    rows = [dict(r) for r in conn.execute(
        "SELECT id, title, source_url, views, likes, thumbnail_url, final_grade, posted_at "
        "FROM social_videos WHERE brand_name=? AND platform='youtube' "
        "ORDER BY views DESC", (brand_name,)).fetchall()]
    conn.close()
    out["count"] = len(rows)
    out["videos"] = rows[:6]
    if rows:
        out["max_views"] = int(rows[0].get("views") or 0)
        out["top"] = rows[0]
    return out


def regrade() -> int:
    """social_videos 전체 재등급(절대→내부분위수). ingest 후 호출."""
    import services.grading as G
    conn = get_conn()
    socials = [dict(r) for r in conn.execute("SELECT * FROM social_videos").fetchall()]
    if not socials:
        conn.close()
        return 0
    G.grade_all(socials, now_iso=_now())
    for v in socials:
        conn.execute(
            "UPDATE social_videos SET absolute_grade=?, internal_grade=?, final_grade=?, "
            "engagement_rate=?, engagement_level=?, engagement_score=?, internal_percentile=?, "
            "grading_basis=?, graded_at=? WHERE id=?",
            (v.get("absolute_grade"), v.get("internal_grade"), v.get("final_grade"),
             v.get("engagement_rate"), v.get("engagement_level"), v.get("engagement_score"),
             v.get("internal_percentile"), v.get("grading_basis"), v.get("graded_at"), v["id"]))
    conn.commit()
    conn.close()
    return len(socials)


def load_ad_library() -> list[dict]:
    """각 광고에 최고 매칭 소셜 영상의 반응지표·등급을 social_* 로 붙여 반환."""
    conn = get_conn()
    rows = conn.execute("""
        SELECT a.*, m.match_score AS match_score,
               s.views AS social_views, s.likes AS social_likes,
               s.comments AS social_comments, s.shares AS social_shares,
               s.video_url AS social_video_url, s.source_url AS social_source_url,
               s.platform AS social_platform, s.engagement_rate AS social_engagement_rate,
               s.engagement_level AS social_engagement_level,
               s.absolute_grade AS social_absolute_grade, s.internal_grade AS social_internal_grade,
               s.final_grade AS social_final_grade, s.engagement_score AS social_engagement_score,
               s.internal_percentile AS social_internal_percentile,
               s.grading_basis AS social_grading_basis
        FROM ad_library_ads a
        LEFT JOIN ad_social_matches m ON m.ad_id=a.id AND m.match_score=(
            SELECT MAX(match_score) FROM ad_social_matches WHERE ad_id=a.id)
        LEFT JOIN social_videos s ON s.id=m.social_id
    """).fetchall()
    conn.close()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["tags"] = json.loads(d.get("tags") or "[]")
        except Exception:  # noqa: BLE001
            d["tags"] = []
        out.append(d)
    return out


def load_social_videos() -> list[dict]:
    conn = get_conn()
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM social_videos ORDER BY views DESC").fetchall()]
    conn.close()
    return rows


def verify_user(username: str, password: str) -> bool:
    conn = get_conn()
    row = conn.execute("SELECT password_hash FROM users WHERE username=?", (username,)).fetchone()
    conn.close()
    return bool(row) and row["password_hash"] == hash_pw(password)


def update_bookmark(ad_id: str, value: bool, username: str = "") -> None:
    conn = get_conn()
    conn.execute("UPDATE ad_library_ads SET is_bookmarked=?, updated_at=? WHERE id=?",
                 (1 if value else 0, _now(), ad_id))
    conn.commit()
    conn.close()
    # Supabase에도 반영(팀 공유·영구 보존). 미설정이면 무동작.
    try:
        import services.bookmark_store as BS
        BS.add(ad_id, username) if value else BS.remove(ad_id)
    except Exception:  # noqa: BLE001
        pass


def exclude_ad(ad_id: str, value: bool = True) -> None:
    """잘못 수집된 광고를 archive 목록에서 숨김(모든 탭). 되돌리려면 value=False."""
    conn = get_conn()
    conn.execute("UPDATE ad_library_ads SET is_excluded=?, updated_at=? WHERE id=?",
                 (1 if value else 0, _now(), ad_id))
    conn.commit()
    conn.close()


def restore_bookmarks_from_store() -> int:
    """Supabase 북마크를 로컬 DB에 복원(Cloud 재배포 후 유실 방지). 미설정/실패 시 로컬 유지."""
    try:
        import services.bookmark_store as BS
        ids = BS.load_all()
        if ids is None:          # 미설정·네트워크 실패 → 로컬 북마크 보존(덮어쓰지 않음)
            return 0
        conn = get_conn()
        conn.execute("UPDATE ad_library_ads SET is_bookmarked=0")
        n = 0
        for aid in ids:
            n += conn.execute("UPDATE ad_library_ads SET is_bookmarked=1 WHERE id=?", (aid,)).rowcount
        conn.commit()
        conn.close()
        return n
    except Exception:  # noqa: BLE001
        return 0


def set_yt_embeddable(ad_id: str, value) -> None:
    """YouTube 임베드 가능 여부 캐시(1/0). 상세 진입 시 1회 조회해 저장."""
    if value is None:
        return
    conn = get_conn()
    conn.execute("UPDATE ad_library_ads SET yt_embeddable=? WHERE id=?",
                 (1 if value else 0, ad_id))
    conn.commit()
    conn.close()


def set_fatigue_status(ad_id: str, status: str) -> None:
    """소재 피로도 상태 캐시(일별 스냅샷 잡이 계산해 저장 → 카드/브랜드에서 싸게 읽음)."""
    conn = get_conn()
    conn.execute("UPDATE ad_library_ads SET fatigue_status=? WHERE id=?", (status, ad_id))
    conn.commit()
    conn.close()


def get_brand_trend_summary(brand: str) -> dict:
    """브랜드 단위 추이 요약: 총 조회수 · 운영중 광고수 · 피로도/성장 광고수 · 일별 총조회수."""
    conn = get_conn()
    tv = conn.execute("SELECT COALESCE(SUM(yt_views),0) FROM ad_library_ads WHERE brand_name=?",
                      (brand,)).fetchone()[0]
    live = conn.execute("SELECT COUNT(*) FROM ad_library_ads WHERE brand_name=? AND status='live' "
                        "AND COALESCE(video_url,'')<>''", (brand,)).fetchone()[0]
    fat = conn.execute("SELECT COUNT(*) FROM ad_library_ads WHERE brand_name=? AND fatigue_status=?",
                       (brand, "피로도 의심")).fetchone()[0]
    grow = conn.execute("SELECT COUNT(*) FROM ad_library_ads WHERE brand_name=? AND fatigue_status=?",
                        (brand, "성장 중")).fetchone()[0]
    rows = conn.execute("SELECT s.snapshot_date, SUM(s.views) FROM ad_view_snapshots s "
                        "JOIN ad_library_ads a ON a.id=s.ad_id WHERE a.brand_name=? "
                        "GROUP BY s.snapshot_date ORDER BY s.snapshot_date", (brand,)).fetchall()
    conn.close()
    return {"total_views": tv, "live": live, "fatigue": fat, "growing": grow,
            "daily": [{"snapshot_date": r[0], "views": r[1] or 0} for r in rows]}


def update_memo(ad_id: str, memo: str) -> None:
    conn = get_conn()
    conn.execute("UPDATE ad_library_ads SET memo=?, updated_at=? WHERE id=?", (memo, _now(), ad_id))
    conn.commit()
    conn.close()


def update_ad_script(ad_id: str, text: str, source: str, status: str, error: str = "") -> None:
    conn = get_conn()
    row = conn.execute("SELECT script_created_at, brand_name, platform, video_url "
                       "FROM ad_library_ads WHERE id=?", (ad_id,)).fetchone()
    created = (row["script_created_at"] if row and row["script_created_at"] else _now())
    conn.execute(
        "UPDATE ad_library_ads SET script_text=?, script_source=?, script_status=?, "
        "script_error_message=?, script_created_at=?, script_updated_at=? WHERE id=?",
        (text, source, status, error, created, _now(), ad_id))
    conn.commit()
    conn.close()
    # 완성된 스크립트는 Supabase에 영구 백업(reboot 후에도 복원)
    if status == "completed" and (text or "").strip():
        try:
            import services.script_store as SS
            SS.save_script(ad_id, text, source, status,
                           (row["brand_name"] if row else ""),
                           (row["platform"] if row else ""),
                           (row["video_url"] if row else ""))
        except Exception:  # noqa: BLE001
            pass


_SCRIPTS_RESTORED = False


def restore_scripts_from_store() -> int:
    """앱 시작 시 Supabase의 스크립트를 로컬 DB에 복원(프로세스당 1회). Cloud reboot 영구 보존."""
    global _SCRIPTS_RESTORED
    if _SCRIPTS_RESTORED:
        return 0
    _SCRIPTS_RESTORED = True
    try:
        import services.script_store as SS
        if not SS.enabled():
            return 0
        rows = SS.load_all()
    except Exception:  # noqa: BLE001
        return 0
    if not rows:
        return 0
    conn = get_conn()
    n = 0
    for r in rows:
        aid = r.get("ad_id")
        txt = r.get("script_text")
        if not aid or not (txt or "").strip():
            continue
        cur = conn.execute("UPDATE ad_library_ads SET script_text=?, script_source=?, "
                           "script_status=?, script_updated_at=? WHERE id=?",
                           (txt, r.get("script_source") or "stored",
                            r.get("script_status") or "completed", _now(), aid))
        n += cur.rowcount
    conn.commit()
    conn.close()
    return n


def filter_ads(ads: list[dict], f: dict) -> list[dict]:
    from datetime import date
    today = date.today()
    days = f.get("period_days")
    search = (f.get("search") or "").strip().lower()

    def keep(a: dict) -> bool:
        # 기본: 검색/텍스트·미상 포맷 및 미디어 없는(placeholder) 광고 숨김 (개발자모드 제외)
        if not f.get("show_hidden"):
            if a.get("ad_format") in ("search_text", "unknown"):
                return False
            if not (a.get("thumbnail_url") or a.get("video_url")):
                return False
        if f.get("brand") and f["brand"] != "전체" and a.get("brand_name") != f["brand"]:
            return False
        if f.get("platforms") and a.get("platform") not in f["platforms"]:
            return False
        if f.get("media") and a.get("media_type") not in f["media"]:
            return False
        st_ = f.get("status")
        if st_ == "라이브" and a.get("status") != "live":
            return False
        if st_ == "종료" and a.get("status") not in ("ended", "inactive"):
            return False
        if st_ == "OFF" and a.get("status") == "live":
            return False
        if f.get("categories") and not set(f["categories"]) & set(a.get("tags") or []):
            return False
        if f.get("only_bookmark") and not a.get("is_bookmarked"):
            return False
        if f.get("grade") and f["grade"] != "전체":
            import services.grading as G
            if not G.passes_grade_filter(a.get("social_final_grade"), f["grade"]):
                return False
        if search:
            hay = " ".join(str(a.get(k) or "") for k in ("brand_name", "ad_title", "ad_copy")).lower()
            if search not in hay:
                return False
        if days:
            d = str(a.get("collected_at") or "")[:10]
            try:
                if (today - datetime.strptime(d, "%Y-%m-%d").date()).days > days:
                    return False
            except ValueError:
                return False
        return True

    import services.grading as G

    def viral_key(a):
        """터진순: 등급 매칭된 광고 우선 → final_grade → engagement_score → social views → 수집일.
        소셜 없는 광고는 뒤로(0그룹) score/collected 순."""
        g = a.get("social_final_grade")
        if g:
            return (1, G.GRADE_RANK.get(g, 0), a.get("social_engagement_score") or 0,
                    int(a.get("social_views") or 0), str(a.get("collected_at") or ""))
        return (0, 0, 0, int(a.get("score") or 0) / 100.0, str(a.get("collected_at") or ""))

    keyf = {
        "🔥 터진순(추천)": viral_key,
        "조회수 높은순(소셜)": lambda a: int(a.get("social_views") or 0),
        "최근 수집순": lambda a: str(a.get("collected_at") or ""),
        "점수 높은순": lambda a: int(a.get("score") or 0),
        "저장 많은순": lambda a: (int(a.get("is_bookmarked") or 0), int(a.get("score") or 0)),
    }.get(f.get("sort"), lambda a: int(a.get("score") or 0))
    return sorted((a for a in ads if keep(a)), key=keyf, reverse=True)


def stats(ads: list[dict]) -> dict:
    return {"total": len(ads),
            "last_update": max((str(a.get("updated_at") or a.get("created_at") or "")
                                for a in ads), default="")[:16].replace("T", " ")}


# ── repurely AI 분석 리포트 영구 저장(상세보기에서 다시 꺼내보기) ──
def _ensure_repurely_ai_table(conn) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS repurely_ai_reports(
        key TEXT PRIMARY KEY, platform TEXT, creative_name TEXT,
        report_json TEXT, script_text TEXT, created_at TEXT, updated_at TEXT)""")


def save_repurely_report(key: str, platform: str, creative_name: str,
                         report: dict, script: str = "") -> None:
    """AI 분석 리포트(dict)를 key(=platform::소재명) 기준으로 저장/갱신. created_at은 보존."""
    import json
    from datetime import datetime, timezone
    conn = get_conn()
    _ensure_repurely_ai_table(conn)
    now = datetime.now(timezone.utc).isoformat()
    old = conn.execute("SELECT created_at FROM repurely_ai_reports WHERE key=?", (key,)).fetchone()
    created = old["created_at"] if old else now
    conn.execute("""INSERT OR REPLACE INTO repurely_ai_reports
        (key, platform, creative_name, report_json, script_text, created_at, updated_at)
        VALUES(?,?,?,?,?,?,?)""",
                 (key, platform, creative_name, json.dumps(report, ensure_ascii=False),
                  script or "", created, now))
    conn.commit()
    conn.close()


def get_repurely_report(key: str) -> dict | None:
    """저장된 AI 리포트 반환 {report, script, updated_at}. 없으면 None."""
    import json
    conn = get_conn()
    _ensure_repurely_ai_table(conn)
    row = conn.execute(
        "SELECT report_json, script_text, updated_at FROM repurely_ai_reports WHERE key=?",
        (key,)).fetchone()
    conn.close()
    if not row:
        return None
    try:
        return {"report": json.loads(row["report_json"]), "script": row["script_text"] or "",
                "updated_at": row["updated_at"]}
    except Exception:  # noqa: BLE001
        return None


# ── repurely(Insight) 소재 북마크 — key=platform::소재명 ──
def _ensure_repurely_bm_table(conn) -> None:
    conn.execute("CREATE TABLE IF NOT EXISTS repurely_bookmarks(key TEXT PRIMARY KEY, created_at TEXT)")


def toggle_repurely_bookmark(key: str, value: bool) -> None:
    conn = get_conn()
    _ensure_repurely_bm_table(conn)
    if value:
        conn.execute("INSERT OR IGNORE INTO repurely_bookmarks(key, created_at) VALUES(?,?)", (key, _now()))
    else:
        conn.execute("DELETE FROM repurely_bookmarks WHERE key=?", (key,))
    conn.commit()
    conn.close()


def get_repurely_bookmarks() -> set:
    conn = get_conn()
    _ensure_repurely_bm_table(conn)
    rows = conn.execute("SELECT key FROM repurely_bookmarks").fetchall()
    conn.close()
    return {r[0] for r in rows}


def _ensure_repurely_memo_table(conn) -> None:
    conn.execute("CREATE TABLE IF NOT EXISTS repurely_memos("
                 "key TEXT PRIMARY KEY, memo TEXT, images TEXT, updated_at TEXT)")
    cols = [r[1] for r in conn.execute("PRAGMA table_info(repurely_memos)").fetchall()]
    if "images" not in cols:  # 기존 테이블 마이그레이션
        conn.execute("ALTER TABLE repurely_memos ADD COLUMN images TEXT")


def save_repurely_memo(key: str, memo: str, images: Optional[list] = None) -> None:
    """repurely 소재 분석 메모+첨부이미지를 영구 저장(세션 휘발 방지)."""
    conn = get_conn()
    _ensure_repurely_memo_table(conn)
    conn.execute("INSERT OR REPLACE INTO repurely_memos(key, memo, images, updated_at) VALUES(?,?,?,?)",
                 (key, memo, json.dumps(images or [], ensure_ascii=False), _now()))
    conn.commit()
    conn.close()


def get_repurely_memos() -> dict:
    """{key: {"memo": str, "images": [경로...]}}"""
    conn = get_conn()
    _ensure_repurely_memo_table(conn)
    rows = conn.execute("SELECT key, memo, images FROM repurely_memos").fetchall()
    conn.close()
    out = {}
    for r in rows:
        try:
            imgs = json.loads(r[2]) if r[2] else []
        except Exception:  # noqa: BLE001
            imgs = []
        out[r[0]] = {"memo": r[1] or "", "images": imgs}
    return out


def _ensure_attach_table(conn) -> None:
    conn.execute("CREATE TABLE IF NOT EXISTS attach_images("
                 "key TEXT PRIMARY KEY, images TEXT, updated_at TEXT)")


def save_attach_images(key: str, images: list) -> None:
    """광고 카드(메타/구글) 등 임의 항목에 참고 이미지 첨부를 영구 저장."""
    conn = get_conn()
    _ensure_attach_table(conn)
    conn.execute("INSERT OR REPLACE INTO attach_images(key, images, updated_at) VALUES(?,?,?)",
                 (key, json.dumps(images or [], ensure_ascii=False), _now()))
    conn.commit()
    conn.close()


def get_attach_images(key: str) -> list:
    conn = get_conn()
    _ensure_attach_table(conn)
    r = conn.execute("SELECT images FROM attach_images WHERE key=?", (key,)).fetchone()
    conn.close()
    if not r or not r[0]:
        return []
    try:
        return json.loads(r[0])
    except Exception:  # noqa: BLE001
        return []
