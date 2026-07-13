"""
Creative Radar 수집기 전역 설정.
.env 를 읽어 환경변수/경로를 노출한다. 다른 모듈은 전부 config 를 통해 값을 읽는다.
"""
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

# Windows 콘솔(cp949)에서 한글/이모지 출력 시 UnicodeEncodeError 방지
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")


def _deep_find(mapping, key: str):
    """st.secrets에서 key를 top-level 우선, 없으면 중첩 섹션까지 재귀 탐색.
    (secrets.toml에서 키가 [auth] 등 섹션 아래에 들어가도 찾을 수 있게)"""
    try:
        if key in mapping:
            return mapping[key]
    except Exception:  # noqa: BLE001
        pass
    try:
        for k in list(mapping.keys()):
            v = mapping[k]
            if hasattr(v, "keys"):
                found = _deep_find(v, key)
                if found is not None:
                    return found
    except Exception:  # noqa: BLE001
        pass
    return None


def secret(key: str, default: str = "") -> str:
    """1순위 st.secrets(Streamlit Cloud, 중첩 섹션 포함) → 2순위 .env/환경변수.
    토큰값은 로그에 찍지 않음."""
    try:
        import streamlit as st  # 비-streamlit 컨텍스트에선 예외 → env 폴백
        v = _deep_find(st.secrets, key)
        if v is not None and not hasattr(v, "keys"):
            return str(v).strip()
    except Exception:  # noqa: BLE001
        pass
    return os.getenv(key, default).strip()

DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

# 수집 윈도우(일). 기획서 기준 최근 90일.
COLLECT_WINDOW_DAYS = int(os.getenv("COLLECT_WINDOW_DAYS", "90"))

# 각 수집기를 실제 API로 돌릴지(true) mock 으로 돌릴지(false).
# 미설정/false 면 mock data 로 동작 → Supabase 없이도 즉시 실행 가능.
USE_REAL_META = os.getenv("USE_REAL_META", "false").lower() == "true"
USE_REAL_TIKTOK = os.getenv("USE_REAL_TIKTOK", "false").lower() == "true"

# 자동수집 job이 demo.db·썸네일을 Git에 add/commit/push 할지. 기본 False = 레포 비대 방지.
# (수집·regenerate_demo_db()·로컬 썸네일 생성은 이 플래그와 무관하게 항상 수행)
ENABLE_AUTO_GIT_PUSH = os.getenv("ENABLE_AUTO_GIT_PUSH", "false").lower() == "true"

# ── Supabase ─────────────────────────────────────────────
SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip()
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
# Supabase 미설정 시 data/local_db.json 에 저장(로컬 폴백 모드)
USE_SUPABASE = bool(SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY)
LOCAL_DB_PATH = DATA_DIR / "local_db.json"

# ── Meta Ad Library API ──────────────────────────────────
# 레퍼런스(남의 광고) 수집은 Marketing API 가 아니라 Ad Library API 를 쓴다.
# 토큰은 Meta 앱의 액세스 토큰을 그대로 사용 가능(권한/지역 제한 존재).
META_ACCESS_TOKEN = secret("META_ACCESS_TOKEN")
META_APP_ID = os.getenv("META_APP_ID", "").strip()
META_APP_SECRET = os.getenv("META_APP_SECRET", "").strip()
META_API_VERSION = os.getenv("META_API_VERSION", "v21.0").strip()
# 검색어/국가 — Ad Library 는 키워드+국가로 검색한다.
META_SEARCH_TERMS = [s.strip() for s in os.getenv("META_SEARCH_TERMS", "").split(",") if s.strip()]
META_COUNTRIES = [s.strip() for s in os.getenv("META_AD_COUNTRIES", "KR").split(",") if s.strip()]

# ── TikTok ───────────────────────────────────────────────
# 레퍼런스 수집은 Commercial Content API(지역 제한 큼)가 정석.
# 토큰이 있으면 Marketing API 광고그룹/소재로도 일부 대체 가능.
TIKTOK_ACCESS_TOKEN = os.getenv("TIKTOK_ACCESS_TOKEN", "").strip()
TIKTOK_APP_ID = os.getenv("TIKTOK_APP_ID", "").strip()
TIKTOK_APP_SECRET = os.getenv("TIKTOK_APP_SECRET", "").strip()
TIKTOK_ADVERTISER_ID = os.getenv("TIKTOK_ADVERTISER_ID", "").strip()
TIKTOK_API_BASE = os.getenv(
    "TIKTOK_API_BASE", "https://business-api.tiktok.com/open_api/v1.3"
).strip()

# ── Google / Naver (2~3차) ───────────────────────────────
SERPAPI_KEY = os.getenv("SERPAPI_KEY", "").strip()
APIFY_TOKEN = secret("APIFY_TOKEN")
NAVER_CLIENT_ID = os.getenv("NAVER_CLIENT_ID", "").strip()
NAVER_CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET", "").strip()

# ── YouTube Data API ─────────────────────────────────────
YOUTUBE_API_KEY = secret("YOUTUBE_API_KEY")
GEMINI_API_KEY = secret("GEMINI_API_KEY")
GEMINI_MODEL = secret("GEMINI_MODEL", "gemini-2.5-flash")

# ── Apify (옵션 수집기) ──────────────────────────────────
USE_APIFY = (secret("USE_APIFY", "false").lower() == "true")
# 수집 소스 선택: 직접 크롤링(scraper) vs Apify(apify) / 소셜: manual vs apify
GOOGLE_TRANSPARENCY_PROVIDER = (secret("GOOGLE_TRANSPARENCY_PROVIDER", "scraper").lower())
SOCIAL_VIDEO_PROVIDER = (secret("SOCIAL_VIDEO_PROVIDER", "manual").lower())
# Apify Actor 슬러그(필요시 .env 로 교체)
APIFY_TIKTOK_ACTOR = os.getenv("APIFY_TIKTOK_ACTOR", "clockworks~tiktok-scraper").strip()
APIFY_INSTAGRAM_ACTOR = os.getenv("APIFY_INSTAGRAM_ACTOR", "apify~instagram-scraper").strip()
APIFY_GOOGLE_ACTOR = os.getenv("APIFY_GOOGLE_ACTOR", "").strip()  # 비우면 Google Apify 비활성
