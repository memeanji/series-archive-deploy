"""
Series Archive — Ad Reference Library for Series Builder.
탭별 SQL 페이지 로딩 + 캐시로 가볍게. 수집기(Apify 등)는 화면 렌더 시 절대 실행 안 함.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import auth  # noqa: E402
import components as C  # noqa: E402
import database  # noqa: E402
import styles  # noqa: E402

TAB_KEY = {"전체": "all", "Meta": "meta", "Google": "google"}


# ── 캐시된 경량 조회 (ttl=300) ───────────────────────────
@st.cache_data(ttl=300)
def _brand_counts():
    return database.brand_counts()


@st.cache_data(ttl=300)
def _filter_options():
    return database.filter_options()


@st.cache_data(ttl=300)
def _social_count():
    return database.social_count()


@st.cache_data(ttl=300)
def _ads_page(tab, fkey, page, page_size, _f):
    return database.load_ads_page(tab, _f, page, page_size)


@st.cache_data(ttl=300)
def _ads_count(tab, fkey, _f):
    return database.count_ads(tab, _f)


@st.cache_data(ttl=300)
def _social(page=1):
    return database.load_social_videos()


@st.cache_data(ttl=120)
def _yt_candidates(brand, cls):
    return database.get_youtube_candidates(brand, cls)


@st.cache_data(ttl=120)
def _yt_counts():
    return database.youtube_candidate_counts()


@st.cache_resource(ttl=3600, show_spinner="repurely 데이터 불러오는 중…")
def _repurely_load_cached():
    """repurely 시트+Meta API 통합 — 서버 전체에서 1시간 공유 캐시.
    cache_resource라 _reload()의 cache_data.clear()/세션 리셋/새로고침에도 살아남음.
    fetched_at = load_all 이 '실제로' 실행된 시각(캐시 적중 동안 고정 → 진짜 갱신시각)."""
    import repurely.insights as RI
    from datetime import datetime
    return {"rows": RI.load_all(),
            "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M")}


def _repurely_rows(force: bool = False):
    if force:
        try:
            _repurely_load_cached.clear()
        except Exception:  # noqa: BLE001
            pass
    base = _repurely_load_cached()
    # 세션별 enrich 변형이 공유 캐시를 오염시키지 않게 복사 + 실제 fetch 시각 동반
    return [dict(r) for r in base["rows"]], base["fetched_at"]


@st.cache_data(ttl=60)
def _apify_status():
    # 가벼운 상태만: 토큰 존재 여부(네트워크 호출 없음)
    import config
    return {"enabled": bool(config.USE_APIFY and config.APIFY_TOKEN)}


@st.cache_resource(show_spinner=False)
def _restore_bookmarks_once():
    """컨테이너 부팅 시 1회 — Supabase 북마크를 demo.db에 복원.
       Cloud는 재배포/재시작마다 demo.db가 리셋돼 is_bookmarked가 날아가므로 여기서 되살림.
       Supabase 미설정/테이블 없음/실패면 0 반환(로컬 북마크 그대로 유지)."""
    return database.restore_bookmarks_from_store()


def main() -> None:
    st.set_page_config(page_title="Series Archive", page_icon="📚", layout="wide",
                       initial_sidebar_state="expanded")
    styles.inject_css()
    styles.block_hotkeys()
    database.init_db(seed_users=auth.get_secret_users())
    _restore_bookmarks_once()   # Cloud 재배포 후 북마크 복원(1회/부팅)

    if not auth.require_login():
        auth.login()
        return

    t0 = time.perf_counter()
    header = C.render_header()
    counts = _brand_counts()
    C.render_sidebar(counts, total=sum(r["ad"] for r in counts))

    tab = header["tab"]

    # ── 구글 브랜드 미확정 리뷰함(법인 공유 → AR ID 단위 수동 지정·학습) — Google 탭 ──
    if tab == "Google":
        with st.expander("🔎 구글 브랜드 미확정 리뷰함 (법인 공유 광고 · AR ID 학습 지정)", expanded=False):
            C.render_google_review()

    # ── 광고 ID 다중 검색(Meta Ad Library 공유 광고 빠른 조회) ──
    with st.expander("🔎 광고 ID로 찾기 (여러 개·링크 붙여넣기 가능)", expanded=False):
        id_raw = st.text_area(
            "광고 ID 또는 Meta Ad Library 링크", key="id_search_raw", height=80,
            label_visibility="collapsed",
            placeholder="예) 1234567890, 9876543210  또는  https://www.facebook.com/ads/library/?id=1234567890\n"
                        "(쉼표·공백·줄바꿈으로 여러 개, 링크 여러 개도 가능)")
        sc = st.columns([1, 1, 4])
        sc[0].button("검색", type="primary", use_container_width=True)   # 누르면 rerun→아래서 렌더
        if sc[1].button("초기화", use_container_width=True):
            st.session_state.pop("id_search_raw", None)
            st.rerun()
    if (id_raw or "").strip():
        C.render_id_search(id_raw)
        _footer(t0)
        return

    # ── 광고 탭(전체/Meta/Google) — SQL 페이지 로딩 ──
    tabkey = TAB_KEY.get(tab, "all")
    f = C.render_filters(_filter_options(), header, _social_count())
    page_size = st.session_state.get("sa_psize", 20)
    page = st.session_state.get("sa_page", 1)

    import json
    fkey = json.dumps(f, sort_keys=True, ensure_ascii=False, default=str)
    t_db = time.perf_counter()
    total = _ads_count(tabkey, fkey, f)
    total_pages = max(1, (total + page_size - 1) // page_size)
    page = min(page, total_pages)
    rows = _ads_page(tabkey, fkey, page, page_size, f)
    db_ms = (time.perf_counter() - t_db) * 1000

    last = max((str(a.get("collected_at") or "") for a in rows), default="")[:16].replace("T", " ")
    scope = f["brand"] if f["brand"] != "전체" else (tab if tab != "전체" else "전체 브랜드")
    info = st.columns([5, 1])
    info[0].markdown(
        f"<div class='sa-info'>광고 <b>{total}</b>건 · {scope} · 페이지 <b>{page}/{total_pages}</b> · "
        f"최근수집 <b>{last or '-'}</b> · <span style='color:{styles.SUB}'>조회수·등급은 매칭된 소셜 원본 기준</span></div>",
        unsafe_allow_html=True)
    _opts = [20, 40, 60]
    st.session_state.sa_psize = info[1].selectbox(
        "페이지당", _opts, index=_opts.index(page_size) if page_size in _opts else 0,
        label_visibility="collapsed")
    # Google: 미확정 광고가 섞일 수 있음을 안내(공유 법인 단위 수집 특성)
    if tab == "Google" and any((r.get("brand_status") or "") in ("company_only", "unmatched", "")
                               for r in rows):
        st.caption("⚪ 미확정 배지 광고는 공유 법인/광고주 단위로 수집되어 **다른 자매브랜드 광고가 섞일 수 있습니다**. "
                   "리뷰함에서 브랜드 지정/제외로 정리할 수 있어요.")

    if f["brand"] != "전체":   # 특정 브랜드 선택 시 추이 요약
        C.render_brand_trend_summary(f["brand"])

    t_render = time.perf_counter()
    C.render_ad_grid(rows, total, page, page_size)
    render_ms = (time.perf_counter() - t_render) * 1000

    _footer(t0, db_ms, render_ms)


def _footer(t0: float, db_ms: float = 0.0, render_ms: float = 0.0) -> None:
    with st.sidebar:
        st.markdown("<br>", unsafe_allow_html=True)
        if st.button("🚪 로그아웃", use_container_width=True):
            auth.logout()
        total_ms = (time.perf_counter() - t0) * 1000
        build = database.get_db_build()
        if build:
            st.caption(f"🗄 데이터 빌드: {build}")
        st.caption(f"⏱ DB {db_ms:.0f}ms · render {render_ms:.0f}ms · total {total_ms:.0f}ms")


if __name__ == "__main__":
    main()
