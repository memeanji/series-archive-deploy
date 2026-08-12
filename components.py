"""Series Archive UI 컴포넌트."""
from __future__ import annotations

import base64
import json
import re
import subprocess
import sys
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as stc

import database
import services.youtube as YT
import services.thumbnail_store as thumbnail_store
import styles as S
from services.urls import is_valid_external_url, normalize_google_transparency_url

ROOT = Path(__file__).resolve().parent

PLATFORM_ICON = {"tiktok": "🎵", "meta": "📘", "google": "🔍", "naver": "🟢"}
PLAT_ICON = {"Facebook": "📘", "Instagram": "📸", "Messenger": "💬",
             "Audience Network": "📡", "Threads": "🧵"}
PLATFORM_LABEL = {"meta": "Meta", "tiktok": "TikTok", "google": "Google", "naver": "Naver"}
PAGE_SIZE = 12


def _fmt(n) -> str:
    try:
        n = int(n or 0)
    except (TypeError, ValueError):
        return "-"
    if n <= 0:
        return "-"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


def _num(v, suffix="") -> str:
    if v in (None, "", 0):
        return "-"
    return f"{v}{suffix}"


def _full(n) -> str:
    """전체 숫자(개 단위, 천단위 콤마). 예: 1,234,567"""
    try:
        n = int(n or 0)
    except (TypeError, ValueError):
        return "-"
    return f"{n:,}" if n > 0 else "-"


def _kabbr(n) -> str:
    """한국식 축약 표기. 예: 2,882,363 → 288만 · 28,823 → 2.9만 · 120,000,000 → 1.2억"""
    try:
        n = int(n or 0)
    except (TypeError, ValueError):
        return "-"
    if n <= 0:
        return "-"
    if n >= 100_000_000:
        v = n / 100_000_000
        return (f"{v:.0f}" if v >= 100 else f"{v:.1f}".rstrip("0").rstrip(".")) + "억"
    if n >= 10_000:
        v = n / 10_000
        return (f"{v:.0f}" if v >= 100 else f"{v:.1f}".rstrip("0").rstrip(".")) + "만"
    if n >= 1_000:
        return f"{n / 1_000:.1f}".rstrip("0").rstrip(".") + "천"
    return str(n)


def _g(ad: dict, key: str, default=""):
    v = ad.get(key)
    return default if v in (None, "") else v


def _reload() -> None:
    st.cache_data.clear()
    st.rerun()


@st.cache_data(ttl=86400, show_spinner=False)
def _yt_transcript(video_id: str) -> str:
    return YT.fetch_transcript(video_id)


@st.cache_data(ttl=86400, show_spinner=False)
def _yt_embeddable(video_id: str):
    """status.embeddable 캐시 조회(영상별 1회). True=재생가능 / False=외부재생필요 / None=미확인."""
    return YT.embeddable(video_id)


def _yt_smart_player(ad: dict, vid: str) -> None:
    """YouTube IFrame Player API로 재생 시도 → 임베드 차단(onError 101/150 등) 감지 시
    같은 자리에서 fallback(썸네일+안내+외부링크)으로 자동 전환. 우회 아님(정상 외부링크)."""
    import html as _h
    turl = normalize_google_transparency_url(ad.get("transparency_url") or ad.get("original_ad_url"))
    watch, thumb = YT.watch_url(vid), YT.thumb_url(vid)
    tbtn = (f"<a href='{_h.escape(turl)}' target='_blank' style='display:inline-block;"
            f"background:rgba(255,255,255,.16);color:#fff;border:1px solid rgba(255,255,255,.5);"
            f"font-size:12.5px;font-weight:700;padding:7px 14px;border-radius:8px;"
            f"text-decoration:none;margin:3px'>🔎 투명성센터에서 보기</a>"
            if is_valid_external_url(turl) else "")
    html = f"""
    <div id="wrap" style="position:relative;width:100%;height:455px;border-radius:10px;
         overflow:hidden;background:#0F172A;font-family:Pretendard,-apple-system,sans-serif">
      <div id="player" style="width:100%;height:100%"></div>
      <div id="fb" style="display:none;position:absolute;inset:0;flex-direction:column;
           align-items:center;justify-content:center;text-align:center;color:#fff;padding:18px;
           background-image:url('{thumb}');background-size:cover;background-position:center">
        <div style="position:absolute;inset:0;background:rgba(15,23,42,.74)"></div>
        <div style="position:relative;z-index:1">
          <div style="font-size:32px">🔒</div>
          <div style="font-size:13.5px;font-weight:700;margin:10px 0 16px;line-height:1.6">
            이 영상은 소유자 설정으로<br>앱 내 재생이 제한되어 있습니다.</div>
          <a href="{watch}" target="_blank" style="display:inline-block;background:#fff;color:#0F172A;
             font-size:12.5px;font-weight:700;padding:7px 14px;border-radius:8px;
             text-decoration:none;margin:3px">▶ YouTube에서 보기</a>
          {tbtn}
        </div>
      </div>
    </div>
    <script>
      var tag=document.createElement('script');tag.src="https://www.youtube.com/iframe_api";
      document.head.appendChild(tag);
      function _saFb(){{var p=document.getElementById('player');if(p)p.style.display='none';
        var f=document.getElementById('fb');if(f)f.style.display='flex';}}
      function onYouTubeIframeAPIReady(){{
        new YT.Player('player',{{width:'100%',height:'100%',videoId:'{vid}',
          playerVars:{{rel:0,modestbranding:1}},
          events:{{'onError':function(e){{
            if([101,150,100,2,5].indexOf(e.data)>=0){{_saFb();}}
          }}}}
        }});
      }}
    </script>
    """
    stc.html(html, height=470)


def _yt_fallback_ui(ad: dict, vid: str) -> None:
    """임베드 제한 영상 — 오류 화면 대신 썸네일 + 안내 + 외부 링크(YouTube·투명성센터)."""
    thumb = YT.thumb_url(vid)
    st.markdown(
        f"<div style='position:relative;border-radius:10px;overflow:hidden;background:#0F172A;"
        f"aspect-ratio:16/9'>"
        f"<img src='{thumb}' style='width:100%;height:100%;object-fit:cover;opacity:.5'/>"
        f"<div style='position:absolute;inset:0;display:flex;flex-direction:column;"
        f"align-items:center;justify-content:center;text-align:center;color:#fff;padding:18px'>"
        f"<div style='font-size:30px'>🔒</div>"
        f"<div style='font-size:13px;font-weight:700;margin-top:8px;line-height:1.55'>"
        f"이 영상은 소유자 설정으로<br>앱 내 재생이 제한되어 있습니다.</div></div></div>",
        unsafe_allow_html=True)
    bc = st.columns(2)
    bc[0].link_button("▶ YouTube에서 보기", YT.watch_url(vid), use_container_width=True)
    turl = normalize_google_transparency_url(ad.get("transparency_url") or ad.get("original_ad_url"))
    if is_valid_external_url(turl):
        bc[1].link_button("🔎 투명성센터에서 보기", turl, use_container_width=True)


@st.cache_data(ttl=3600, show_spinner=False)
def _file_data_uri(rel_path: str) -> str:
    """로컬 static 이미지 → data URI (렌더 순간 변환, 캐시). DB엔 저장 안 함."""
    p = Path(rel_path)
    if not p.is_absolute():
        p = ROOT / rel_path
    if not p.exists():
        return ""
    return "data:image/png;base64," + base64.b64encode(p.read_bytes()).decode()


def get_display_thumbnail(ad: dict) -> dict:
    """카드/상세 공통 썸네일 결정. 우선순위: (R2 공개URL) > thumbnail_path>thumbnail_url>preview_url>image_url>media_url.
    로컬 static 경로는 data URI로 변환, http(s)는 그대로. 반환: {src, source, method, exists}."""
    # R2 활성 시: 로컬 썸네일 파일명 → R2 공개 URL 우선(브라우저 직접 로드). 미설정/오류면 아래 로컬 폴백.
    try:
        if thumbnail_store.is_enabled():
            for key in ("local_thumbnail_path", "thumbnail_url"):
                v = (ad.get(key) or "").strip()
                if v and not v.startswith(("http://", "https://", "data:")):
                    url = thumbnail_store.public_url(v)
                    if url:
                        return {"src": url, "source": "r2", "method": "url", "exists": True}
    except Exception:  # noqa: BLE001  (R2 판단 실패 → 조용히 로컬 폴백)
        pass
    # Supabase Storage 로 이전된 행: local_thumbnail_path 가 'thumbnails/<파일>' 형태 → 공개 URL 로 직접.
    #   (이 경로는 로컬 디스크에 없으므로 아래 파일 폴백에서 헛되이 찾지 않도록 먼저 처리)
    try:
        v = (ad.get("local_thumbnail_path") or "").strip()
        if v.startswith("thumbnails/"):
            import services.supabase_read as _sr
            url = _sr.storage_url(v)
            if url:
                return {"src": url, "source": "supabase_storage", "method": "url", "exists": True}
    except Exception:  # noqa: BLE001  (판단 실패 → 아래 기존 폴백 그대로)
        pass
    # local_thumbnail_path(영구 로컬 파일) 최우선 → thumbnail_url(로컬경로 또는 만료 fbcdn) → 기타
    for key in ("local_thumbnail_path", "thumbnail_path", "thumbnail_url",
                "preview_url", "image_url", "media_url"):
        v = (ad.get(key) or "").strip()
        if not v:
            continue
        if v.startswith("http://") or v.startswith("https://"):
            return {"src": v, "source": key, "method": "url", "exists": True}
        if v.startswith("data:"):
            return {"src": v, "source": key, "method": "data_uri", "exists": True}
        rel = v[4:] if v.startswith("app/") else v   # 'app/static/..' → 'static/..'
        uri = _file_data_uri(rel)
        if uri:
            return {"src": uri, "source": key, "method": "local_data_uri", "exists": True}
        # 파일 없음 → 다음 후보
    return {"src": None, "source": None, "method": "none", "exists": False}


def _resolve_media(local: str, remote: str = "") -> str:
    """repurely 썸네일 해석: 영구화 로컬파일(data URI) 우선 → 없으면 fbcdn 원격 URL 폴백.
    클라우드는 로컬파일로, 로컬개발은 파일이 없을 때 fbcdn으로 뜬다. 둘 다 없으면 ''."""
    loc = (local or "").strip()
    if loc.startswith("http") or loc.startswith("data:"):
        return loc
    if loc:
        rel = loc[4:] if loc.startswith("app/") else loc   # 'app/static/..' → 'static/..'
        uri = _file_data_uri(rel)
        if uri:
            return uri
    rem = (remote or "").strip()
    if rem.startswith("http") or rem.startswith("data:"):
        return rem
    return ""


def _do_extract(social_id: str, video_id: str) -> None:
    """버튼 클릭 시에만 단건 추출. ① YouTube 자막 → ② 실패 시 Gemini 영상 전사."""
    database.update_script(social_id, "", "extracting")
    text = YT.fetch_transcript(video_id) if video_id else ""
    if not text and video_id:
        text = YT.gemini_transcript(video_id)   # 자막 없으면 Gemini 멀티모달
    database.update_script(social_id, text, "extracted" if text else "failed")
    _reload()


def render_script_section(social_id: str, video_id: str = "") -> None:
    """영상 하단 스크립트 — 자동추출 안 함. 저장된 값 표시 + 버튼으로만 추출/직접입력."""
    st.markdown(f"##### 📝 영상 스크립트 <span style='font-size:12px;color:{S.SUB}'>"
                f"· 버튼 클릭 시에만 추출</span>", unsafe_allow_html=True)
    if not social_id:
        st.caption("연결된 소셜 영상이 없습니다.")
        return
    sv = database.get_social(social_id) or {}
    status = sv.get("script_status") or "none"
    text = sv.get("script_text") or ""

    if text and status in ("extracted", "manual", "exists"):
        with st.expander("스크립트 보기", expanded=False):
            st.write(text)
        st.caption({"extracted": "YouTube 자막 자동 추출", "manual": "직접 입력",
                    "exists": "수집됨"}.get(status, ""))
        if st.button("🔁 재추출", key=f"re_{social_id}", disabled=not video_id):
            _do_extract(social_id, video_id)
        return
    if status == "extracting":
        st.info("추출 중입니다… 새로고침 후 확인하세요.")
        return

    st.caption("스크립트가 아직 없습니다." + (" (이전 추출 실패)" if status == "failed" else ""))
    c = st.columns(2)
    if c[0].button("🎬 스크립트 자동 추출", key=f"ex_{social_id}", disabled=not video_id,
                   help="① YouTube 자막 → ② 없으면 Gemini로 영상 전사 (영상 1건만 실행)"):
        _do_extract(social_id, video_id)
    with c[1].popover("✍️ 직접 입력"):
        man = st.text_area("스크립트", key=f"man_{social_id}", height=140,
                           label_visibility="collapsed", placeholder="스크립트를 직접 붙여넣기")
        if st.button("저장", key=f"mansave_{social_id}", type="primary"):
            database.update_script(social_id, man, "manual")
            _reload()
    if not video_id:
        st.caption("YouTube가 아닌 영상은 자동 추출 미지원 — 직접 입력만 가능(STT/Whisper 추후).")


# ════════════════════════════════════════════════════════════
def render_header(ads=None) -> dict:
    # 상단: 큰 탭(직관적) + 새로고침 + 우측 유저. 제목(Series Archive)은 사이드바로 이동.
    tc = st.columns([5, 1, 1], vertical_alignment="center")
    with tc[0]:
        tabs = ["Meta", "Google", "북마크"]
        tab = st.segmented_control("메뉴", tabs, default="Meta", key="nav_tab",
                                   label_visibility="collapsed") or "Meta"
    # 캐시 강제 초기화(5시 크롤·demo.db 갱신 후 옛 상태가 캐시로 남는 것 방지)
    if tc[1].button("🔄 새로고침", key="hdr_refresh", use_container_width=True,
                    help="최신 데이터로 캐시를 비우고 다시 불러옵니다"):
        st.cache_data.clear()
        st.cache_resource.clear()
        st.rerun()
    user = st.session_state.get("username", "guest")
    tc[2].markdown(f"<div style='text-align:right;font-size:12px;color:{S.SUB}'>"
                   f"👤 <b>{user}</b></div>", unsafe_allow_html=True)
    st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)
    return {"search": "", "tab": tab}


# ════════════════════════════════════════════════════════════
def _add_to_watchlist(brand: str) -> None:
    wl_path = ROOT / "data" / "watchlist.json"
    try:
        wl = json.loads(wl_path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        wl = {"brands": []}
    if brand not in wl.get("brands", []):
        wl.setdefault("brands", []).append(brand)
        wl_path.write_text(json.dumps(wl, ensure_ascii=False, indent=2), encoding="utf-8")


def _domain_ok(d: str) -> bool:
    d = (d or "").strip()
    if not d:
        return True
    d = d.replace("https://", "").replace("http://", "").split("/")[0]
    return bool(re.match(r"^[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$", d))


def _run_meta_collect(display: str) -> str:
    """선택 브랜드를 page_id(있으면) 기반으로 깊게 재수집. 마지막 결과줄 반환."""
    out = "완료"
    with st.spinner(f"'{display}' 광고주 전체 광고 재수집 중… (page_id 기반, 1~3분)"):
        try:
            r = subprocess.run([sys.executable, str(ROOT / "jobs" / "meta_collect.py"), display],
                               capture_output=True, text=True, timeout=900, cwd=str(ROOT))
            lines = [l for l in (r.stdout or r.stderr).strip().splitlines() if l.strip()]
            out = lines[-1] if lines else "완료"
        except subprocess.TimeoutExpired:
            out = "시간 초과"
        except Exception as e:  # noqa: BLE001
            out = f"실패: {e}"
    return out


def _find_page_id(arg: str) -> dict:
    """라이브러리 ID/링크 → {library_id, page_id, page_name} (subprocess)."""
    res = {"library_id": "", "page_id": "", "page_name": ""}
    try:
        r = subprocess.run([sys.executable, str(ROOT / "jobs" / "find_page_id.py"), arg],
                           capture_output=True, text=True, timeout=120, cwd=str(ROOT))
        for ln in (r.stdout or "").splitlines():
            if ln.startswith("LIBID:"):
                res["library_id"] = ln[6:].strip()
            elif ln.startswith("PAGEID:"):
                res["page_id"] = ln[7:].strip()
            elif ln.startswith("PAGENAME:"):
                res["page_name"] = ln[9:].strip()
    except Exception:  # noqa: BLE001
        pass
    return res


_STATUS_COLOR = {"정상": "#10B981", "얕은 수집": "#F59E0B", "확인 필요": "#EF4444",
                 "만료 많음": "#B45309", "page_id 확인 필요": "#6366F1"}


def render_brand_collection_admin() -> None:
    """브랜드별 Meta 수집 상태 + page_id 수동 입력/재수집."""
    stats = database.all_brand_collection_status()
    n_pid = sum(1 for s in stats if s["method"] == "page_id")
    st.caption(f"브랜드 {len(stats)}개 · page_id 기반 {n_pid} · 키워드 {len(stats) - n_pid} "
               f"(page_id가 있으면 키워드 한계 없이 광고주 전체를 수집)")

    # 상태 요약 테이블(상태 나쁜 순 정렬됨)
    try:
        import pandas as pd
        df = pd.DataFrame([{"#": s.get("index") or "-", "요일": s.get("group") or "-",
                            "브랜드": s["brand"], "방식": s["method"], "page_id": s["page_id"] or "-",
                            "앱수집": s["count"], "라이브러리": s.get("reported") or "-",
                            "수집률%": s.get("rate") if s.get("rate") is not None else "-",
                            "영상": s["video"], "사라짐": s["gone"],
                            "마지막수집": s["last"] or "-", "상태": s["status"]} for s in stats])
        st.dataframe(df, use_container_width=True, hide_index=True, height=320)
    except Exception:  # noqa: BLE001
        for s in stats[:40]:
            st.write(f"{s['brand']} · {s['method']} · {s['count']}개 · {s['status']}")

    st.markdown("---")
    names = [s["brand"] for s in stats]
    sel = st.selectbox("브랜드 선택", names, key="bca_sel")
    cur = database.get_brand(sel) or {}
    _pid_now = (cur.get("meta_page_id") or "").strip()
    _sst = next((s for s in stats if s["brand"] == sel), {})
    _gi = database.brand_index_groups().get(sel, {})
    _f = {"brand": sel, "media": [], "status": "전체", "category": "전체",
          "sort": "최근 수집순", "period_days": None}
    _merged = database.count_ads("meta", {**_f})   # 기본 묶기(영상 중복 대표) 기준 장수
    st.caption(f"#{_gi.get('index','-')} · **{_gi.get('group','-')}요일** 수집 그룹 · "
               f"page_id **{_pid_now or '없음'}**({cur.get('page_id_status') or 'none'}) · "
               f"광고ID별 **{_sst.get('count','-')}장** / 묶음 **{_merged}장** · 상태 **{_sst.get('status','-')}**")

    # ── 기본: 광고 링크/라이브러리 ID 로 광고주 찾기 ──
    raw = st.text_input("Meta 광고 라이브러리 ID 또는 공유 링크", key=f"bca_lib_{sel}",
                        placeholder="예: https://www.facebook.com/ads/library/?id=1234567890  또는  1234567890")
    fc = st.columns(2)
    if fc[0].button("🔎 광고 링크/ID로 광고주 찾기", key=f"bca_find_{sel}",
                    type="primary", use_container_width=True, disabled=not raw.strip()):
        with st.spinner("광고 원본에서 광고주(page_id)를 찾는 중… (~30초)"):
            r = _find_page_id(raw.strip())
        if r.get("page_id"):
            database.set_brand_page_id(sel, r["page_id"], "confirmed")
        st.session_state[f"bca_found_{sel}"] = r   # 결과 표시용
        st.rerun()

    found = st.session_state.get(f"bca_found_{sel}")
    if found:
        ok = bool(found.get("page_id"))
        st.markdown(
            f"- 입력 라이브러리 ID: **{found.get('library_id') or '-'}**\n"
            f"- 추출 page_id: **{found.get('page_id') or '실패'}**\n"
            f"- 광고주(page_name): **{found.get('page_name') or '-'}**\n"
            f"- 브랜드 매칭: **{sel}**\n"
            f"- page_id 저장: **{'✅ 저장됨' if ok else '❌ 못 찾음'}**")
        if not ok:
            st.warning("광고주 page_id를 못 찾았어요. 다른 광고의 ID/링크로 다시 시도하거나, "
                       "아래 고급 설정에서 page_id를 직접 입력하세요.")
        if fc[1].button("📥 광고주 전체 광고 재수집", key=f"bca_re_{sel}",
                        type="primary", use_container_width=True, disabled=not ok):
            res = _run_meta_collect(sel)
            st.session_state[f"bca_recol_{sel}"] = res
            st.rerun()
    recol = st.session_state.get(f"bca_recol_{sel}")
    if recol:
        st.success(f"재수집 결과 — {recol}")

    # page_id 이미 있으면 바로 재수집 버튼 노출
    if _pid_now and not found:
        if st.button("📥 광고주 전체 광고 재수집", key=f"bca_re2_{sel}", type="primary"):
            res = _run_meta_collect(sel)
            st.session_state[f"bca_recol_{sel}"] = res
            st.rerun()

    # ── 고급 설정: page_id 직접 입력 ──
    with st.expander("고급 설정: page_id 직접 입력", expanded=False):
        pid = st.text_input("Meta page_id (광고주 페이지 ID)", value=_pid_now,
                            key=f"bca_pid_{sel}", placeholder="예: 100xxxxxxxxxxx")
        if st.button("💾 page_id 저장", key=f"bca_save_{sel}"):
            database.set_brand_page_id(sel, pid, "confirmed" if pid.strip() else "none")
            st.success("저장됨")
            _reload()

    # ── 수집 로그 ──
    with st.expander(f"📜 '{sel}' 수집 로그", expanded=False):
        logs = database.recent_brand_logs(sel, 8)
        if not logs:
            st.caption("수집 로그가 없습니다.")
        for lg in logs:
            st.caption(f"{str(lg.get('started_at') or '')[:16]} · {lg.get('method') or '-'} · "
                       f"{lg.get('status')} · 발견 {lg.get('found_count',0)} · "
                       f"신규 {lg.get('new_count',0)} · 갱신 {lg.get('updated_count',0)}")


def _grv_thumb(ad) -> None:
    th = get_display_thumbnail(ad)
    if th["src"]:
        st.markdown(f"<img src='{th['src']}' loading='lazy' style='width:100%;border-radius:8px;"
                    f"background:#0F172A'/>", unsafe_allow_html=True)
    else:
        st.markdown("<div class='sa-thumb sa-thumb-empty' style='aspect-ratio:1'>"
                    "<div class='sa-ph'>🔍</div></div>", unsafe_allow_html=True)
    tu = ad.get("transparency_url") or ad.get("original_ad_url")
    if tu:
        st.markdown(f"<a href='{tu}' target='_blank' style='font-size:11px'>투명성센터 ↗</a>",
                    unsafe_allow_html=True)


def render_google_review() -> None:
    """구글 광고 상태 관리 — 4상태(확정/추정/미확정/제외). 미확정은 수동 지정, 제외는 별도 목록."""
    cnt = database.google_status_counts()
    review_n = cnt.get("company_only", 0) + cnt.get("unmatched", 0)
    excl_n = cnt.get("reference_excluded", 0)
    st.caption(f"🟢 브랜드 확정 {cnt.get('confirmed',0)} · 🟡 추정 {cnt.get('estimated',0)} · "
               f"⚪ 미확정 {review_n} · 🚫 레퍼런스 제외 {excl_n}")
    if st.button("🔁 브랜드 매칭 재계산", key="grv_recompute",
                 help="브랜드명/alias/도메인/제품키워드/법인 순으로 재매칭(수동 지정·제외는 보존)"):
        with st.spinner("구글 광고 브랜드 매칭 재계산 중…"):
            res = database.recompute_google_matches()
        st.success(f"재계산 완료 — {res}")
        _reload()

    view = st.segmented_control("보기", ["⚪ 미확정 리뷰", "🚫 제외 목록"],
                                default="⚪ 미확정 리뷰", key="grv_view") or "⚪ 미확정 리뷰"

    # 무거운 스크린샷(data URI) 로딩 방지 — 버튼 눌러야 소량 불러오기
    if not st.session_state.get("_grv_show"):
        if st.button("광고 불러오기(12개씩)", key="grv_load"):
            st.session_state["_grv_show"] = True
            st.session_state["_grv_page"] = 0
            st.rerun()
        st.caption("※ 구글 스크린샷이 무거워 자동 로딩을 끕니다. 버튼을 눌러 확인하세요.")
        return
    page = st.session_state.get("_grv_page", 0)
    SIZE = 12
    excluded_view = view.startswith("🚫")
    fetch = database.google_excluded_ads if excluded_view else database.google_review_ads
    rows_all = fetch(SIZE * (page + 1) + 1)
    rows = rows_all[page * SIZE:(page + 1) * SIZE]
    if not rows:
        st.info("제외된 광고가 없습니다." if excluded_view
                else "미확정 구글 광고가 없습니다. (먼저 '브랜드 매칭 재계산'을 눌러보세요.)")
        return
    if excluded_view:
        st.caption(f"레퍼런스 제외 {page*SIZE+1}~{page*SIZE+len(rows)}번째 — 잘못 제외했으면 되돌릴 수 있어요.")
    else:
        st.caption(f"미확정 {page*SIZE+1}~{page*SIZE+len(rows)}번째 — "
                   f"브랜드 지정 1건이면 **같은 광고주(AR ID) 전부 자동 확정**됩니다.")
    nav = st.columns([1, 1, 4])
    if nav[0].button("◀ 이전", disabled=page <= 0, key="grv_prev"):
        st.session_state["_grv_page"] = page - 1; st.rerun()
    if nav[1].button("다음 ▶", disabled=len(rows_all) <= (page + 1) * SIZE, key="grv_next"):
        st.session_state["_grv_page"] = page + 1; st.rerun()
    allb = sorted(database.brand_index_groups().keys())
    for i in range(0, len(rows), 4):
        cols = st.columns(4)
        for col, ad in zip(cols, rows[i:i + 4]):
            with col:
                _grv_thumb(ad)
                st.caption(f"광고주: {ad.get('advertiser_name') or '-'}")
                if excluded_view:
                    if st.button("↩ 제외 취소", key=f"grv_rs_{ad['id']}", use_container_width=True):
                        database.restore_google_excluded(ad["id"]); st.rerun()
                    continue
                st.caption(f"{ad.get('match_reason') or ''}")
                sel = st.selectbox("브랜드", allb, key=f"grv_b_{ad['id']}",
                                   index=(allb.index(ad["brand_name"]) if ad.get("brand_name") in allb else 0),
                                   label_visibility="collapsed")
                # 캐시 비우지 않고 가볍게 rerun(리뷰 목록은 비캐시 쿼리라 즉시 반영)
                if st.button("✅ 이 브랜드로 지정", key=f"grv_set_{ad['id']}",
                             type="primary", use_container_width=True):
                    database.assign_google_brand(ad["id"], brand=sel); st.rerun()
                bc = st.columns(2)
                if bc[0].button("브랜드 미확정", key=f"grv_uk_{ad['id']}", use_container_width=True):
                    database.assign_google_brand(ad["id"], unsure=True); st.rerun()
                if bc[1].button("레퍼런스 제외", key=f"grv_ex_{ad['id']}", use_container_width=True):
                    database.assign_google_brand(ad["id"], exclude=True); st.rerun()


def _collect_ids(ids: list) -> list:
    """미수집 광고 ID 를 Meta Ad Library 단건 크롤로 즉시 수집. ID별 결과 리스트 반환."""
    import json as _json
    results: list = []
    with st.spinner(f"광고 ID {len(ids)}개 수집 중… (Meta Ad Library, 최대 1~2분)"):
        try:
            r = subprocess.run([sys.executable, str(ROOT / "jobs" / "collect_by_id.py"), *map(str, ids)],
                               capture_output=True, text=True, timeout=600, cwd=str(ROOT))
            for ln in (r.stdout or "").splitlines():
                if ln.startswith("RESULT_JSON:"):
                    results = _json.loads(ln[len("RESULT_JSON:"):])
        except subprocess.TimeoutExpired:
            st.error("시간 초과 — 잠시 후 다시 시도")
        except Exception as e:  # noqa: BLE001
            st.error(f"수집 실패: {e}")
    return results


def _run_collect(display: str) -> None:
    with st.spinner(f"'{display}' 수집 중… (메타+구글+YouTube, 1~2분)"):
        try:
            r = subprocess.run([sys.executable, str(ROOT / "jobs" / "crawl_brand.py"), display],
                               capture_output=True, text=True, timeout=600, cwd=str(ROOT))
            tail = (r.stdout or r.stderr).strip().splitlines()[-1:] or ["완료"]
            st.success(f"'{display}' 수집 완료 — {tail[0]}")
        except subprocess.TimeoutExpired:
            st.error("시간 초과")
        except Exception as e:  # noqa: BLE001
            st.error(f"수집 실패: {e}")


def _suggest_advertisers(brand: str) -> list:
    """구글 투명성센터 자동완성에서 법인명 후보를 subprocess 로 가져온다."""
    import json as _json
    try:
        r = subprocess.run([sys.executable, str(ROOT / "jobs" / "google_advertisers.py"), brand],
                           capture_output=True, text=True, timeout=120, cwd=str(ROOT))
        for ln in (r.stdout or "").splitlines():
            if ln.startswith("ADVJSON:"):
                return _json.loads(ln[len("ADVJSON:"):])
    except Exception:  # noqa: BLE001
        pass
    return []


def render_add_brand() -> None:
    """브랜드 추가 위저드: 입력 → 후보 찾기 → 선택 → 저장 → 수집(버튼 클릭 시에만)."""
    with st.sidebar.expander("➕ 브랜드 추가", expanded=False):
        # 자동완성으로 받은 법인명 제안을 위젯 생성 전에 반영(위젯 생성 후 수정 불가)
        if "ab_gadv_pending" in st.session_state:
            st.session_state["ab_gadv"] = st.session_state.pop("ab_gadv_pending")

        name = st.text_input("브랜드명", key="ab_name", placeholder="예: 리바엔").strip()
        gadv = st.text_input("구글 광고주명(법인명)", key="ab_gadv",
                             placeholder="브랜드명만 넣고 '후보 찾기'를 누르면 자동으로 채워집니다",
                             help="구글 투명성센터는 '주식회사 OOO' 법인명으로 검색해야 잘 잡힙니다. "
                                  "비워두면 후보 찾기가 자동완성에서 법인명을 가져와 채웁니다.").strip()
        domain = st.text_input("공식몰 도메인(선택)", key="ab_domain", placeholder="rivan.co.kr").strip()
        kw_raw = st.text_input("검색 키워드(쉼표 구분)", key="ab_kw", placeholder="리바엔, RIVAN, 리바엔 공식몰")
        cat = st.text_input("카테고리(선택)", key="ab_cat").strip()
        keywords = [k.strip() for k in (kw_raw or "").split(",") if k.strip()]

        # Step 2: 후보 찾기
        if st.button("🔎 후보 찾기", key="ab_find", use_container_width=True):
            if not name:
                st.warning("브랜드명을 입력하세요.")
            elif not _domain_ok(domain):
                st.warning("도메인 형식이 올바르지 않습니다.")
            else:
                if database.brand_exists(name):
                    st.info("이미 등록된 브랜드 — 저장 시 법인명/도메인/키워드가 갱신됩니다.")
                st.session_state.ab_cands = database.find_brand_candidates(name, domain, keywords)
                st.session_state.ab_searched = True
                # 법인명이 비어 있으면 구글 자동완성에서 후보를 가져와 채운다
                if not gadv:
                    with st.spinner("구글 투명성센터에서 법인명 자동 검색 중… (~20초)"):
                        advs = _suggest_advertisers(name)
                    st.session_state.ab_adv_cands = advs
                    if advs:
                        st.session_state.ab_gadv_pending = advs[0]
                st.rerun()

        # Step 3: 후보 확인 + Step 4: 저장
        if st.session_state.get("ab_searched"):
            cands = st.session_state.get("ab_cands", [])
            picked = []
            if cands:
                st.caption(f"후보 {len(cands)}개 — 맞는 것 선택")
                for i, c in enumerate(cands[:10]):
                    if st.checkbox(f"**{c['name']}** · {'/'.join(c['sources'])} · "
                                   f"{c['ad_count']}건 · {','.join(c['reasons'])}", key=f"ab_c_{i}"):
                        picked.append(c["name"])
                    if c["thumbs"]:
                        tc = st.columns(3)
                        for col, th in zip(tc, c["thumbs"][:3]):
                            t = get_display_thumbnail({"thumbnail_url": th})
                            if t["src"]:
                                col.markdown(f"<img src='{t['src']}' style='width:100%;border-radius:4px'/>",
                                             unsafe_allow_html=True)
            else:
                st.info("후보 없음. 그래도 수동 추가하시겠습니까?")
                if not keywords:
                    st.caption("⚠️ 수동 추가는 검색 키워드 최소 1개 필요")

            # 구글 자동완성 법인명 후보 — 클릭하면 위 '구글 광고주명' 칸에 적용
            advs = st.session_state.get("ab_adv_cands", [])
            if advs:
                cur_g = st.session_state.get("ab_gadv", "")
                st.caption("🏢 구글 법인명 후보(자동완성) — 클릭해 적용")
                for j, a in enumerate(advs[:6]):
                    mark = "✓ " if a == cur_g else ""
                    if st.button(f"{mark}{a}", key=f"ab_adv_{j}", use_container_width=True):
                        st.session_state.ab_gadv_pending = a
                        st.rerun()
            elif st.session_state.get("ab_searched") and not gadv:
                st.caption("자동완성에 법인명이 없어요 — 직접 입력하거나 메타만 수집됩니다.")

            can_save = bool(name) and (bool(cands) or bool(keywords))
            if st.button("💾 브랜드 저장", key="ab_save", type="primary",
                         disabled=not can_save, use_container_width=True):
                kws = list(dict.fromkeys([name] + keywords + picked))
                database.add_brand(name, kws, domain, cat,
                                   extra={"google_advertiser_name": gadv or (picked[0] if picked else "")})
                st.session_state.ab_saved = name
                st.session_state.sa_brand = name
                st.cache_data.clear()   # 캐시 비워서 사이드바에 즉시 반영
                st.rerun()

        # Step 5: 수집 실행(버튼 클릭 시에만)
        saved = st.session_state.get("ab_saved")
        if saved:
            stt = database.latest_brand_status(saved)
            st.caption(f"상태: {stt['status'] if stt else '대기'}")
            if st.button(f"📥 '{saved}' 지금 수집", key="ab_collect", use_container_width=True):
                _run_collect(saved)
                for k in ("ab_saved", "ab_searched", "ab_cands", "ab_adv_cands"):
                    st.session_state.pop(k, None)
                st.cache_data.clear()
                st.rerun()


def render_sidebar(counts: list, total: int) -> str:
    """counts: [{name, ad, approved, needs, rejected, live}, ...] (캐시). 상위 20개 + 검색."""
    sb = st.sidebar
    # 앱 타이틀(사이드바 최상단 sticky 헤더)
    sb.markdown(f"<div class='sa-sb-head'>"
                f"<div class='sa-logo'>Series Archive</div>"
                f"<div class='sa-sub'>Ad Reference Library</div></div>", unsafe_allow_html=True)
    render_add_brand()
    sb.markdown("<div style='height:.4rem'></div>", unsafe_allow_html=True)
    q = sb.text_input("브랜드 검색", placeholder="브랜드 검색", label_visibility="collapsed").strip().lower()

    sel = st.session_state.get("sa_brand", "전체")

    def _select(b: str) -> None:
        st.session_state.sa_brand = b
        st.session_state.sa_page = 1
        if b != "전체":   # 최근 본 브랜드 갱신(최신 우선, 최대 6)
            rec = [x for x in st.session_state.get("recent_brands", []) if x != b]
            st.session_state.recent_brands = ([b] + rec)[:6]
        st.rerun()

    # 구분선 → 그 아래에 '전체 브랜드'
    sb.markdown(f"<hr style='margin:.2rem 0 1.2rem;border-color:{S.BORDER}'>", unsafe_allow_html=True)
    if sb.button(f"전체 브랜드  ·  {total}", key="b_all",
                 type=("primary" if sel == "전체" else "secondary")):
        _select("전체")

    # ── 최근 본 브랜드 ──
    valid = {r["name"] for r in counts}
    recent = [b for b in st.session_state.get("recent_brands", []) if b in valid]
    if recent and not q:
        sb.markdown(f"<div style='font-size:11px;font-weight:700;color:{S.OFF_GRAY};"
                    f"margin:.5rem 0 .2rem 2px'>최근 본 브랜드</div>", unsafe_allow_html=True)
        for b in recent[:5]:
            if sb.button(b, key=f"rc_{b}", type=("primary" if b == sel else "secondary")):
                _select(b)
        sb.markdown(f"<hr style='margin:.5rem 0 .6rem;border-color:{S.BORDER}'>", unsafe_allow_html=True)

    # 브랜드 인덱스/요일 그룹(요일별 분할 수집 기준) — 사이드바 번호 표기에 사용
    _ig = database.brand_index_groups()

    # ── 브랜드 목록(N. 브랜드 + Ⓜ 메타 · Ⓖ 구글 광고수) ──
    def _brand_btn(r, container=sb):
        b = r["name"]
        m, g = r.get("meta", 0), r.get("google", 0)
        seg = []
        if m:
            seg.append(f"Ⓜ {m}")
        if g:
            seg.append(f"Ⓖ {g}")
        cnt = "   ".join(seg)
        gi = _ig.get(b, {})
        idx = gi.get("index", 0)
        num = f"{idx}." if idx else "·"
        tip = (f"#{idx} · {gi.get('group','-')}요일 수집 · 메타 {m} · 구글 {g} · "
               f"소셜 승인 {r['approved']} (검토 {r['needs']} · 제외 {r['rejected']})")
        if container.button(f"{num} {b}   {cnt}", key=f"b_{b}", help=tip,
                            type=("primary" if b == sel else "secondary")):
            _select(b)

    # 인덱스 순으로 정렬(번호가 순차적으로 보이게)
    counts = sorted(counts, key=lambda r: _ig.get(r["name"], {}).get("index", 9999))

    def _norm(s):
        return (s or "").lower().replace(" ", "")

    if q:
        nq = _norm(q)
        hit = [r for r in counts if nq in _norm(r["name"])]
        for r in hit:
            _brand_btn(r)
        # 연관 브랜드: 검색어 앞 2글자/끝 2글자가 겹치는 느슨한 매칭
        related = [r for r in counts if r not in hit and len(nq) >= 2
                   and (nq[:2] in _norm(r["name"]) or _norm(r["name"])[:2] in nq)]
        if related:
            sb.markdown(f"<div style='font-size:11px;font-weight:700;color:{S.OFF_GRAY};"
                        f"margin:.6rem 0 .2rem 2px'>🔗 연관 브랜드</div>", unsafe_allow_html=True)
            for r in related[:8]:
                _brand_btn(r)
        if not hit and not related:
            sb.caption("일치하는 브랜드가 없습니다.")
    else:
        for r in counts[:15]:
            _brand_btn(r)
        if len(counts) > 15:   # 나머지는 펼쳐서 전체 확인
            _exp = sb.expander(f"📋 전체 브랜드 {len(counts)}개 보기")
            for r in counts[15:]:
                _brand_btn(r, _exp)
    return sel


# ════════════════════════════════════════════════════════════
def render_filters(opts: dict, header: dict, social_count: int = 0) -> dict:
    has_social = social_count > 0
    grade = "전체"   # 등급 기능 제거
    # ── 필터 toolbar(얇은 카드): 소재 유형 · 정렬 · 기간 · 초기화 + 고급필터 ──
    with st.container(border=True):
        c = st.columns([1.1, 1.7, 1.2, 0.55], vertical_alignment="bottom")
        media = c[0].multiselect("소재 유형", ["video", "image"],
                                 default=st.session_state.get("f_media", []),
                                 format_func=lambda x: {"video": "🎬 영상", "image": "🖼 이미지"}.get(x, x),
                                 key="f_media")
        sort = c[1].selectbox("정렬",
                              ["조회수 높은순", "최근 수집순", "오래된순", "게재기간 긴순",
                               "게재기간 짧은순", "저장 많은순"], index=0, key="f_sort")
        period = c[2].selectbox("기간(게재 시작)", ["전체", "7일", "30일", "90일"], key="f_period")
        if c[3].button("초기화", use_container_width=True, help="필터 초기화"):
            for k in ("f_media", "f_status", "f_sort", "f_period", "f_devhidden", "f_unavail"):
                st.session_state.pop(k, None)
            st.rerun()
        with st.expander("⚙ 고급 필터"):
            status = st.selectbox("상태", ["전체", "라이브", "종료", "OFF"], key="f_status")
            show_hidden = st.checkbox("검색형·미디어 없는 광고도 표시", value=False, key="f_devhidden",
                                      help="구글 텍스트/검색광고·썸네일 없는 광고는 기본 숨김(개발·디버그용).")
            only_unavail = st.checkbox("상세 확인 불가 광고만 보기", value=False, key="f_unavail",
                                       help="상세에서 '광고 라이브러리에 없습니다'가 뜨는 광고만 모아 봅니다.")
            show_all = st.checkbox("모든 광고 개별 보기", value=False, key="f_showall",
                                   help="기본은 같은 영상(YouTube video_id·Meta 동일 썸네일)을 대표 1장으로 묶어 표시. "
                                        "켜면 라이브러리처럼 광고 1건당 카드 1장.")
            include_estimated = st.checkbox("🟡 브랜드 추정 광고도 포함(구글)", value=False, key="f_estimated",
                                            help="구글: 기본은 '브랜드 확정'만. 켜면 '추정' 광고도 함께 표시.")

    # 탭 → 매체/북마크 매핑
    tab = header["tab"]
    platforms = {"Meta": ["meta"], "Google": ["google"]}.get(tab)
    only_bm = tab == "북마크"

    # ── 적용된 필터 요약 칩(콘텐츠 상단) — 예: Google · 전체 상태 · 최근 수집순 · 전체 기간 ──
    brand = st.session_state.get("sa_brand", "전체")
    chips = [f"<b>{tab}</b>"]
    if brand != "전체":
        chips.append("🏷 " + brand)
    chips += ["🎬 영상" if m == "video" else "🖼 이미지" for m in media]
    chips.append(("● " + status) if status != "전체" else "전체 상태")
    chips.append(sort)
    chips.append(period if period != "전체" else "전체 기간")
    st.markdown(" ".join(f"<span class='sa-chip'>{c}</span>" for c in chips),
                unsafe_allow_html=True)

    return {
        "search": header["search"],
        "brand": st.session_state.get("sa_brand", "전체"),
        "platforms": platforms,
        "media": media,
        "status": status,
        "sort": sort,
        "grade": grade,
        "period_days": {"7일": 7, "30일": 30, "90일": 90}.get(period),
        "only_bookmark": only_bm,
        "show_hidden": show_hidden,
        "only_unavailable": only_unavail,
        "show_all_individual": show_all,   # 기본 False = 영상 중복 묶기
        "include_estimated": include_estimated,
    }


# ════════════════════════════════════════════════════════════
def render_ad_card(ad: dict, idx: int) -> None:
    aid = ad.get("id")
    plat = ad.get("platform", "")
    is_video = ad.get("media_type") == "video"
    th = get_display_thumbnail(ad)
    thumb = th["src"]

    # ── 썸네일(우선순위 1) ──
    bg = ""
    if ad.get("detail_status") == "unavailable":
        inner = ("<div class='sa-ph'><span class='i'>⚠️</span>상세 확인 불가"
                 "<div style='font-size:10px;opacity:.7'>광고 라이브러리에 없음</div></div>")
        thumb_cls = "sa-thumb sa-thumb-empty"
    elif thumb:
        inner = f"<img src='{thumb}' loading='lazy' decoding='async'/>"
        if plat == "google":
            thumb_cls = "sa-thumb sa-thumb-contain"      # 구글: 원본 비율 유지(자르지 않음)
        elif plat == "meta":
            thumb_cls = "sa-thumb sa-thumb-meta"          # 메타: 4:3 세로형, cover·center
        elif thumb.startswith("data:"):
            thumb_cls = "sa-thumb sa-thumb-fill"          # data URI: 채움(crop)
        else:
            thumb_cls = "sa-thumb"                         # 기타: 블러배경+소재 크게
            bg = f"background-image:url('{thumb}')"
    elif ad.get("scrape_status") == "failed":
        why = (ad.get("error_message") or "수집 실패")[:24]
        inner = (f"<div class='sa-ph'><span class='i'>⚠️</span>수집 실패"
                 f"<div style='font-size:10px;opacity:.7'>{why}</div></div>")
        thumb_cls = "sa-thumb sa-thumb-empty"
    else:
        inner = (f"<div class='sa-ph'><span class='i'>{'🎬' if is_video else '🖼'}</span>"
                 f"미리보기 없음</div>")
        thumb_cls = "sa-thumb sa-thumb-empty"

    nab = max(int(ad.get("variant_count") or 1), int(ad.get("dup_rows") or 1))
    ab_badge = (f"<span class='sa-badge' style='background:#F59E0B;top:auto;bottom:7px;right:7px;left:auto'"
                f" title='이 크리에이티브·문구를 쓰는 광고 {nab}개 (A/B 테스트)'>A/B {nab}</span>"
                if plat == "meta" and nab >= 2 else "")
    play = "<div class='sa-play'>▶</div>" if is_video and thumb else ""
    media_label = "Google" if plat == "google" else ("▶ 영상" if is_video else "🖼 이미지")
    media_badge = f"<div class='sa-media'>{media_label}</div>"
    # 임베드 제한 영상 표시(YouTube 소유자가 외부 재생 차단) — 카드 상단 좌측
    yt_blocked = (ad.get("yt_embeddable") == 0) and ("youtu" in (ad.get("video_url") or ""))
    play_badge = ("<div class='sa-badge' style='background:rgba(180,83,9,.92);top:7px;left:7px'>"
                  "🔒 외부재생</div>" if yt_blocked else "")
    # Meta 영상 재생불가 상태 배지 — 크롤 단계에서 저장된 video_status 를 '읽기만'(렌더 시 계산 안 함)
    if plat == "meta" and is_video and not yt_blocked:
        _mst = (ad.get("video_status") or "").strip()
        if _mst == "expired_url":
            play_badge = ("<div class='sa-badge' style='background:rgba(202,138,4,.95);top:7px;left:7px'>"
                          "⚠ URL 만료</div>")
        elif _mst in ("private_or_deleted", "unavailable"):
            play_badge = ("<div class='sa-badge' style='background:rgba(100,116,139,.95);top:7px;left:7px'>"
                          "재생 불가</div>")
    # Google 브랜드 매칭 상태 배지(B안: 미확정/추정도 노출하되 명확히 구분)
    if plat == "google":
        _bs = (ad.get("brand_status") or "").strip()
        if _bs in ("company_only", "unmatched", ""):
            play_badge = ("<div class='sa-badge' style='background:rgba(100,116,139,.95);top:7px;left:7px'>"
                          "⚪ 미확정</div>")
        elif _bs == "estimated":
            play_badge = ("<div class='sa-badge' style='background:rgba(202,138,4,.95);top:7px;left:7px'>"
                          "🟡 추정</div>")
    dot = S.status_color(ad.get("status"))

    # ── 핵심 지표(우선순위 2) ──
    yv, yl, yc = (int(ad.get("yt_views") or 0), int(ad.get("yt_likes") or 0),
                  int(ad.get("yt_comments") or 0))
    if yv or yl or yc:
        # 조회수=축약(288만), 좋아요·댓글=정확한 수+개
        _p = []
        if yv:
            _p.append(f"👁 {_kabbr(yv)}")
        if yl:
            _p.append(f"❤ {_full(yl)}개")
        if yc:
            _p.append(f"💬 {_full(yc)}개")
        metric = (f"<span class='v' title='연결된 유튜브 원본 공개지표 · 광고 성과 아님'>"
                  + " · ".join(_p) + "</span>")
    elif plat == "google":
        metric = f"<span class='sa-date'>조회수 정보 없음</span>"
    else:
        metric = f"<span class='sa-date'>게재 {str(_g(ad,'started_at','-'))[:10]}</span>"
    live = ad.get("status") == "live"
    status_html = (f"<span class='sa-live' style='color:{S.LIVE}'>● 라이브</span>" if live
                   else f"<span class='sa-live' style='color:{S.END_RED}'>● {ad.get('status') or '종료'}</span>")
    media_chip = f"<span class='sa-mchip'>{'🎬 영상' if is_video else ('🔍 Google' if plat=='google' else '🖼 이미지')}</span>"
    plat_badge = f"<span class='sa-pbadge'>{PLATFORM_LABEL.get(plat, plat or '-')}</span>"
    # 소재 피로도 배지(일별 잡이 계산한 fatigue_status — 데이터 쌓이면 표시)
    import services.trend as _TR
    fstat = ad.get("fatigue_status")
    fat_chip = ""
    if fstat and fstat not in ("데이터 부족", "종료"):
        _fc, _fe = _TR.FATIGUE_STYLE.get(fstat, ("#94A3B8", ""))
        fat_chip = (f"<span style='font-size:10px;font-weight:700;color:{_fc};background:{_fc}1A;"
                    f"padding:1px 6px;border-radius:5px'>{_fe} {fstat}</span>")

    with st.container(border=True):
        st.markdown(
            f"<div class='{thumb_cls}' style=\"{bg}\">{inner}"
            f"<div class='sa-dot' style='background:{dot}'></div>{play}{media_badge}{ab_badge}{play_badge}</div>"
            f"<div class='sa-brand'>{_g(ad,'brand_name','-')}</div>"
            f"<div class='sa-meta'><span>{metric}</span>{fat_chip}</div>"
            f"<div class='sa-meta'><span>{media_chip} {plat_badge}</span>{status_html}</div>"
            f"<div style='height:16px'></div>",
            unsafe_allow_html=True)
        b = st.columns([3, 1, 1])
        if b[0].button("상세 보기", key=f"open_{aid}_{idx}", use_container_width=True):
            full = database.get_ad_full(aid)   # 상세 클릭 시에만 1건 전체 로드
            if full:
                render_ad_detail(full)
        marked = bool(ad.get("is_bookmarked"))
        if b[1].button("★" if marked else "☆", key=f"bm_{aid}_{idx}",
                       use_container_width=True, type=("primary" if marked else "secondary"),
                       help="북마크 해제" if marked else "북마크 저장"):
            database.update_bookmark(aid, not marked, st.session_state.get('username',''))
            _reload()
        if b[2].button("🚫", key=f"exc_{aid}_{idx}", use_container_width=True,
                       help="잘못 수집된 광고 — archive에서 제외(숨김)"):
            database.exclude_ad(aid, True)
            _reload()
        if st.session_state.get("f_devhidden"):
            st.caption(f"🔧 id={aid} · {plat} · src={th['source']} · "
                       f"{th['method']} · exists={th['exists']}")


# ════════════════════════════════════════════════════════════
def _render_source_buttons(ad: dict) -> None:
    """원본/투명성센터/랜딩 — 작은 링크형 pill. 유효한 외부 절대 URL일 때만."""
    plat = ad.get("platform")
    links = []
    # 영상 광고면 항상 YouTube 원본 링크 제공(임베드 차단돼도 원본 확인 가능한 안전장치)
    _vu = ad.get("video_url") or ""
    if "youtu" in _vu:
        _vid = YT.extract_video_id(_vu)
        if _vid:
            links.append(("▶ YouTube", YT.watch_url(_vid)))
    if plat == "google":
        turl = normalize_google_transparency_url(ad.get("transparency_url") or ad.get("original_ad_url"))
        if is_valid_external_url(turl):
            links.append(("🔎 투명성센터", turl))
    elif is_valid_external_url(ad.get("original_ad_url")):
        links.append(("🔗 원본 광고", ad["original_ad_url"]))
    elif plat == "meta" and ad.get("id"):
        # 원본 URL 없어도(만료/비공개) Ad Library 검색링크는 항상 유지
        links.append(("🔗 원본 광고", f"https://www.facebook.com/ads/library/?id={ad['id']}"))
    if is_valid_external_url(ad.get("landing_url")):
        links.append(("🛒 랜딩", ad["landing_url"]))
    # 원본 광고가 접근 불가일 수 있는 상태면 버튼 옆에 경고 표시(버튼은 유지)
    _vs = (ad.get("video_status") or "").strip()
    inaccessible = plat == "meta" and _vs in ("private_or_deleted", "not_found", "unavailable")
    if links:
        pills = "".join(
            f"<a href='{u}' target='_blank' style='font-size:12px;color:{S.SUB};text-decoration:none;"
            f"border:1px solid {S.BORDER};border-radius:8px;padding:4px 11px;background:{S.CARD};"
            f"white-space:nowrap;transition:all .15s'>{lbl} ↗</a>" for lbl, u in links)
        warn = ("<span style='font-size:11px;color:#B45309;align-self:center'>⚠ 접근 불가일 수 있음</span>"
                if inaccessible else "")
        st.markdown(f"<div style='display:flex;gap:7px;flex-wrap:wrap;align-items:center;"
                    f"margin-top:18px'>{pills}{warn}</div>", unsafe_allow_html=True)
    else:
        st.markdown(f"<div style='font-size:11.5px;color:{S.OFF_GRAY};margin-top:18px'>"
                    f"원본·랜딩 URL 정보 없음</div>", unsafe_allow_html=True)


def _rel_korean(iso_ts: str) -> str:
    """ISO 시각 → '방금/N분 전/N시간 전/N일 전'. 비거나 파싱 실패 시 ''."""
    if not iso_ts:
        return ""
    from datetime import datetime as _dt, timezone as _tz
    try:
        t = _dt.fromisoformat(str(iso_ts))
        if t.tzinfo is None:
            t = t.replace(tzinfo=_tz.utc)
        secs = (_dt.now(_tz.utc) - t).total_seconds()
    except Exception:  # noqa: BLE001
        return ""
    if secs < 120:
        return "방금"
    if secs < 3600:
        return f"{int(secs // 60)}분 전"
    if secs < 86400:
        return f"{int(secs // 3600)}시간 전"
    return f"{int(secs // 86400)}일 전"


def _greybox(text: str) -> str:
    import html as _h
    return (f"<div style='background:{S.BG};border:1px solid {S.BORDER};border-radius:10px;"
            f"padding:10px 12px;white-space:pre-wrap;font-size:13px;color:{S.TEXT};"
            f"line-height:1.5;max-height:340px;overflow:auto'>{_h.escape(text)}</div>")


_TS_RE = re.compile(r"^\s*(\d{1,2}:\d{2}(?::\d{2})?)\s*[-–~]?\s*"
                    r"(\d{1,2}:\d{2}(?::\d{2})?)?\s*(.*)$")


def _render_script_segments(text: str):
    """Gemini 구간 JSON([{start,end,script,visual_summary,on_screen_text}])이면 리치 타임라인으로,
    아니면 None(일반 텍스트 렌더로 폴백)."""
    import html as _h
    import json as _json
    t = (text or "").strip()
    if not (t.startswith("[") and t.endswith("]")):
        return None
    try:
        segs = _json.loads(t)
        if not isinstance(segs, list) or not segs or not isinstance(segs[0], dict):
            return None
    except Exception:  # noqa: BLE001
        return None
    # 향후 구간 태그(후킹/문제제기/제품효과/CTA) — seg에 'tag'가 있으면 chip으로 표시(forward-compatible)
    tag_color = {"후킹": "#F59E0B", "문제제기": "#EF4444", "제품효과": "#10B981",
                 "CTA": "#6366F1", "혜택": "#0EA5E9"}
    rows = []
    for s in segs:
        chip = f"{s.get('start','')}–{s.get('end','')}".strip("–")
        script = _h.escape(s.get("script") or "")
        vis = _h.escape(s.get("visual_summary") or "")
        ost = _h.escape(s.get("on_screen_text") or "")
        tag = (s.get("tag") or "").strip()
        tag_badge = (f"<span style='background:{tag_color.get(tag, '#94A3B8')};color:#fff;"
                     f"font-size:10px;font-weight:700;padding:1px 7px;border-radius:5px;"
                     f"margin-left:6px'>{_h.escape(tag)}</span>" if tag else "")
        ost_badge = (f"<span style='background:{S.SOFT_MINT};color:#0F766E;font-size:10.5px;"
                     f"padding:1px 6px;border-radius:5px;margin-left:6px'>화면: {ost}</span>"
                     if ost else "")
        rows.append(
            f"<div style='display:flex;gap:11px;align-items:flex-start;padding:11px 0;"
            f"border-bottom:1px solid {S.BORDER}'>"
            f"<span style='flex:0 0 auto;background:{S.SOFT_MINT};color:#0F766E;font-weight:700;"
            f"font-size:11px;padding:3px 8px;border-radius:6px;font-variant-numeric:tabular-nums;"
            f"white-space:nowrap'>{_h.escape(chip)}</span>"
            f"<span style='flex:1'>"
            f"<span style='font-size:13.5px;color:{S.TEXT};line-height:1.75;font-weight:600'>"
            f"{script or '<span style=\"color:#94A3B8\">(대사 없음)</span>'}</span>{tag_badge}{ost_badge}"
            + (f"<div style='font-size:11.5px;color:{S.SUB};margin-top:4px;line-height:1.5'>🎬 {vis}</div>"
               if vis else "")
            + "</span></div>")
    return (f"<div style='background:{S.CARD};border:1px solid {S.BORDER};border-radius:12px;"
            f"padding:6px 14px;max-height:420px;overflow:auto'>{''.join(rows)}</div>")


def _script_plaintext(text: str) -> str:
    """구간 JSON이면 각 구간 대사(script)를 순서대로 이어붙인 통짜 스크립트, 아니면 원문 그대로."""
    import json as _json
    t = (text or "").strip()
    if not (t.startswith("[") and t.endswith("]")):
        return t
    try:
        segs = _json.loads(t)
        if not isinstance(segs, list) or not segs or not isinstance(segs[0], dict):
            return t
    except Exception:  # noqa: BLE001
        return t
    parts = [(s.get("script") or "").strip() for s in segs]
    parts = [p for p in parts if p]
    # 이어붙임·복붙용: 구간 구분 없이 대사만 자연스럽게 연결(줄바꿈 X → 흐르는 문장)
    return " ".join(parts) if parts else t


def _render_thumb_analysis(text: str) -> str:
    """이미지 소재 썸네일 분석 JSON({thumbnail_text,visual_summary,main_subject,hook_type,ad_angle})을 카드로."""
    import html as _h
    import json as _json
    try:
        o = _json.loads(text or "{}")
    except Exception:  # noqa: BLE001
        return _greybox(text or "")
    rows = [("화면 문구", o.get("thumbnail_text")), ("장면 요약", o.get("visual_summary")),
            ("핵심 피사체", o.get("main_subject")), ("후킹 유형", o.get("hook_type")),
            ("소구 포인트", o.get("ad_angle"))]
    inner = "".join(
        f"<div style='display:flex;gap:8px;padding:5px 0;border-bottom:1px solid {S.BORDER}'>"
        f"<span style='flex:0 0 84px;color:{S.SUB};font-size:11.5px;font-weight:700'>{k}</span>"
        f"<span style='flex:1;color:{S.TEXT};font-size:13px'>{_h.escape(str(v or '-'))}</span></div>"
        for k, v in rows)
    return (f"<div style='background:{S.BG};border:1px solid {S.BORDER};border-radius:10px;"
            f"padding:10px 12px'>{inner}</div>")


def _render_script_body(text: str) -> str:
    """Gemini 구간 JSON이면 리치 타임라인, 아니면 'MM:SS–MM:SS 대사' 줄 타임라인."""
    rich = _render_script_segments(text)
    if rich is not None:
        return rich
    import html as _h
    rows = []
    for raw in (text or "").splitlines():
        ln = raw.rstrip()
        if not ln.strip():
            continue
        if ln.strip().startswith("[") and ln.strip().endswith("]"):
            rows.append(f"<div style='font-weight:800;color:{S.TEXT};font-size:12px;"
                        f"margin:10px 0 4px'>{_h.escape(ln.strip()[1:-1])}</div>")
            continue
        m = _TS_RE.match(ln)
        if m and m.group(1) and m.group(3) is not None and m.group(3) != "":
            t0, t1, body = m.group(1), m.group(2), m.group(3)
            chip = t0 + (f"–{t1}" if t1 else "")
            rows.append(
                f"<div style='display:flex;gap:8px;align-items:flex-start;padding:5px 0;"
                f"border-bottom:1px solid {S.BORDER}'>"
                f"<span style='flex:0 0 auto;background:{S.MINT}1A;color:#0F766E;"
                f"font-weight:700;font-size:11px;padding:2px 7px;border-radius:6px;"
                f"font-variant-numeric:tabular-nums;white-space:nowrap'>{_h.escape(chip)}</span>"
                f"<span style='font-size:13px;color:{S.TEXT};line-height:1.5'>{_h.escape(body)}</span>"
                f"</div>")
        else:
            rows.append(f"<div style='font-size:13px;color:{S.TEXT};line-height:1.5;"
                        f"padding:3px 0'>{_h.escape(ln)}</div>")
    return (f"<div style='background:{S.BG};border:1px solid {S.BORDER};border-radius:10px;"
            f"padding:10px 12px;max-height:360px;overflow:auto'>{''.join(rows)}</div>")


def _render_sheets_cuts(ad: dict, aid: str, script_text: str) -> None:
    """구간별 자막 → 구글시트 스토리보드 탭 자동 생성: 버튼 → 미리보기 → 확인 시 템플릿 복사+입력."""
    import datetime as _dt
    import services.sheets_cuts as SC
    if not (script_text or "").strip():
        return   # 스크립트 자체가 없으면 아무것도 안 함(상위에서 AI 생성 안내)
    cuts = SC.normalize_cuts(script_text)
    # 디버그 로그(ad당 세션 1회) — 어느 포맷에서 컷/스크립트를 읽었는지
    try:
        _seen = st.session_state.setdefault("_script_dbg", set())
        if aid not in _seen:
            _seen.add(aid)
            nd = SC.normalize_script_data(ad)
            import os
            os.makedirs("logs", exist_ok=True)
            with open("logs/script_debug.log", "a", encoding="utf-8") as _f:
                _f.write(f"{aid}\t{ad.get('brand_name') or '-'}\tsrc={nd['source']}\t"
                         f"parse={nd['parse']}\tsegs={len(nd['segments'])}\t"
                         f"full_len={len(nd['full_script'])}\n")
    except Exception:  # noqa: BLE001
        pass
    # 안내 문구는 버튼 클릭 시에만 — 항상 노출하지 않음
    pkey = f"_sheets_prev_{aid}"
    label = f"📤 구글시트 스토리보드로 보내기 ({len(cuts)}컷)" if cuts else "📤 구글시트 스토리보드로 보내기"
    if st.button(label, key=f"sheetbtn_{aid}"):
        st.session_state[pkey] = True
    if not st.session_state.get(pkey):
        return
    if not cuts:   # 클릭했는데 컷이 없을 때만 안내(전체 스크립트만 있는 경우)
        st.info("구간별 자막(컷)으로 나뉘지 않아 시트 컷 입력이 어려워요. 전체 스크립트만 있는 형태예요. "
                "'재생성'하면 구간별로 다시 만들어질 수 있어요.")
        if st.button("닫기", key=f"sheetclose_{aid}"):
            st.session_state[pkey] = False
            st.rerun()
        return
    mmdd = _dt.datetime.now().strftime("%m%d")
    default_name = f"{mmdd} {ad.get('brand_name') or ''}".strip()
    name = st.text_input("새 탭 이름", value=default_name, key=f"sheetname_{aid}",
                         help="템플릿을 복사해 이 이름의 새 탭을 만듭니다 (예: 0617 올레놀샷)")
    st.caption("미리보기 — 1행 C열~ 대사, 3행 C열~ 편집가이드 (A열·B2 보존, 템플릿 복사)")
    for i, c in enumerate(cuts, 1):
        st.caption(f"컷{i}: {c['caption'] or '(빈칸)'}  ·  🎬 {c.get('visual','') or '-'}")
    cc = st.columns(2)
    if cc[0].button("✅ 스토리보드 탭 생성", key=f"sheetgo_{aid}", type="primary", use_container_width=True):
        with st.spinner("구글시트 템플릿 복사·입력 중…"):
            res = SC.write_storyboard(name or default_name, cuts)
        if res.get("success"):
            st.success(f"{res['message']} — 탭 '{res['tab']}' · {res['cut_count']}개 컷 입력")
        else:
            st.error(res.get("message") or "생성 실패")
        st.session_state[pkey] = False
    if cc[1].button("취소", key=f"sheetcancel_{aid}", use_container_width=True):
        st.session_state[pkey] = False
        st.rerun()


def _render_video_script(ad: dict) -> None:
    """모달을 닫지 않고(=튕김 없음) 세션 상태에 결과를 캐시해 즉시 갱신."""
    import services.script_gen as SG
    aid = ad.get("id")
    if ad.get("detail_status") == "unavailable":
        st.markdown("##### 영상 스크립트")
        st.caption("⚠️ 이 광고는 광고 라이브러리 상세에 표시되지 않아(노출 미발생 등) "
                   "영상/스크립트 수집 대상이 아닙니다.")
        return
    src_ko = {"youtube_transcript": "YouTube 자막", "gemini_video": "Gemini 영상분석",
              "gemini_estimated": "Gemini 추정(카피 기반)", "manual": "직접 입력"}
    ovr = st.session_state.setdefault("_script_result", {})

    def _gen(label: str):
        # rerun 중복 호출 방지 — 같은 ad 가 생성 중이면 재호출 금지
        running = st.session_state.setdefault("_gemini_running", set())
        if aid in running:
            st.caption("⏳ 이미 생성 중입니다…")
            return
        running.add(aid)
        try:
            database.update_ad_script(aid, ad.get("script_text") or "", ad.get("script_source") or "",
                                      "running", "")   # generation_status=running
            with st.spinner(label):
                r = SG.generate(ad)
            database.update_ad_script(aid, r["text"], r["source"], r["status"], r["error"])
            ovr[aid] = r   # 세션 캐시 → 리런 없이 바로 표시
        finally:
            running.discard(aid)

    # 현재 상태(세션 캐시 우선)
    cur = ovr.get(aid) or {"text": ad.get("script_text") or "",
                           "status": ad.get("script_status") or "pending",
                           "source": ad.get("script_source") or "",
                           "error": ad.get("script_error_message") or ""}

    has_video = bool((ad.get("video_url") or ad.get("media_url") or "").startswith("http"))

    # 상세 진입 시 자동 생성 — 무료(YouTube 자막)만. Gemini 는 버튼 클릭 시에만(키 절약)
    if cur["status"] == "pending" and aid not in ovr:
        done = st.session_state.setdefault("_autogen_done", set())
        if aid not in done:
            done.add(aid)
            with st.spinner("자막 확인 중…"):
                r = SG.transcript_only(ad)
            if r:
                database.update_ad_script(aid, r["text"], r["source"], r["status"], r["error"])
                ovr[aid] = r
            elif has_video:
                ovr[aid] = {"text": "", "status": "needs_ai", "source": "", "error": ""}
            else:
                ovr[aid] = {"text": "", "status": "thumbnail_only", "source": "", "error": ""}
            cur = ovr[aid]

    status = cur["status"]
    head = st.columns([3, 1, 1])

    # ── 이미지 소재: 썸네일 분석(후킹/소구/화면문구) ──
    if not has_video:
        head[0].markdown("##### 소재 분석 (이미지)")
        thumb_done = bool(cur["text"]) and status == "thumbnail_only"
        run = head[1].button("재분석" if thumb_done else "썸네일 분석", key=f"thb_{aid}",
                             use_container_width=True,
                             help="Gemini Vision으로 썸네일의 후킹/소구/화면문구를 추출합니다.")
        if run:
            with st.spinner("Gemini가 썸네일을 분석 중…"):
                r = SG.analyze_thumbnail(ad)
            database.update_ad_script(aid, r["text"], r["source"], r["status"], r["error"])
            ovr[aid] = r
            cur = r
            thumb_done = bool(cur["text"]) and cur["status"] == "thumbnail_only"
        if thumb_done:
            st.caption("🖼 이미지 소재 · Gemini 분석 결과는 추정이며 실제와 다를 수 있습니다.")
            st.markdown(_render_thumb_analysis(cur["text"]), unsafe_allow_html=True)
        elif cur.get("error"):
            st.caption(f"⚠️ {cur['error']}")
        else:
            st.caption("이미지 소재입니다. **썸네일 분석**을 누르면 후킹·소구·화면문구를 추출합니다.")
        return

    # ── 영상 소재: 3초 구간 스크립트 ──
    head[0].markdown("##### 🎬 영상 스크립트")
    completed = bool(cur["text"]) and status == "completed"
    err = cur.get("error")
    if completed:
        regen = head[1].button("재생성", key=f"rgen_{aid}", use_container_width=True)
        edit = head[2].toggle("편집", key=f"edit_{aid}")
    else:
        # 실패/미생성 — 메시지 바로 아래에 [생성/다시 생성] · [직접 입력] 배치
        if status == "video_too_long":
            st.caption(f"⏱ {err}")
        elif err:
            st.warning(f"스크립트 생성 실패 · {err}", icon="⚠️")
        else:
            st.caption("Gemini가 이 영상을 3초 구간별 대본(대사·화면문구·장면)으로 분석합니다.")
        fb = st.columns(2)
        regen = fb[0].button("🔁 다시 생성" if err else "✨ AI 생성", key=f"rgen2_{aid}",
                             use_container_width=True, type="primary")
        with fb[1].popover("✍️ 직접 입력", use_container_width=True):
            man = st.text_area("스크립트", key=f"scman_{aid}", height=160,
                               label_visibility="collapsed", placeholder="스크립트를 직접 붙여넣기")
            if st.button("저장", key=f"scmansave_{aid}", type="primary"):
                database.update_ad_script(aid, man, "manual", "completed", "")
                ovr[aid] = {"text": man, "status": "completed", "source": "manual", "error": ""}
                cur = ovr[aid]
                completed = True
        edit = False
    if regen:
        _gen("Gemini가 영상을 분석해 구간별 대본 작성 중… (최대 2분)")
        cur = ovr[aid]
        status = cur["status"]
        completed = bool(cur["text"]) and status == "completed"
        edit = False

    if completed and not edit:
        if cur["source"] == "gemini_video":
            st.caption("🤖 Gemini가 영상을 분석한 추정 대본 — 실제 대사와 다를 수 있습니다.")
        else:
            st.caption(f"출처: {src_ko.get(cur['source'], cur['source'] or '-')}")
        # 📄 전체 스크립트(이어붙임·복붙용) — 접었다 펼침(기본 펼침), 구간/타임코드 없이 흐르는 문장
        with st.expander("📄 전체 스크립트 (이어붙임 · 복붙용)", expanded=True):
            st.text_area("전체 스크립트", value=_script_plaintext(cur["text"]), height=260,
                         label_visibility="collapsed", key=f"scfull_{aid}")
        with st.expander("📝 구간별 자막 (타임코드별) 보기", expanded=False):
            st.markdown(_render_script_body(cur["text"]), unsafe_allow_html=True)
        _render_sheets_cuts(ad, aid, cur["text"])
    elif completed and edit:
        new = st.text_area("스크립트 편집", value=cur["text"], height=260, key=f"scredit_{aid}",
                           label_visibility="collapsed")
        if st.button("💾 스크립트 저장", key=f"scsave_{aid}", type="primary"):
            database.update_ad_script(aid, new, "manual", "completed", "")
            ovr[aid] = {"text": new, "status": "completed", "source": "manual", "error": ""}


def _render_trend_section(ad: dict, aid: str) -> None:
    """조회수 추이 — 핵심 숫자 카드 + 일별 조회수 증가량 차트(크게)."""
    import services.trend as TR
    import pandas as _pd
    snaps = database.get_ad_snapshots(aid, days=120)
    st.markdown("<hr style='margin:30px 0 16px;border:none;border-top:1px solid #E5E7EB'>",
                unsafe_allow_html=True)
    st.markdown("<div style='font-size:14px;font-weight:800;color:#1E293B;margin-bottom:8px'>"
                "📈 조회수 추이</div>", unsafe_allow_html=True)
    # 복원분(과거 git 기록에서 되살린 조회수)이 섞여 있으면 몇 건인지 알려준다 — 데이터 자체는 동일하게 사용
    try:
        _srcs = database.snapshot_source_counts(aid)
        _rec = int(_srcs.get("git_yt_views", 0)) + int(_srcs.get("git_restore", 0))
        if _rec:
            st.caption(f"총 {sum(_srcs.values())}개 지점 중 {_rec}개는 과거 기록에서 복원한 값입니다.")
    except Exception:  # noqa: BLE001
        pass
    if len(snaps) < 2:
        st.markdown(f"<div style='font-size:12px;color:#94A3B8;line-height:1.5;"
                    f"padding:11px 13px;background:{S.BG};border:1px solid {S.BORDER};border-radius:10px'>"
                    f"{'오늘치(1일) 수집됨 · 내일 한 번 더 쌓이면 추이가 표시돼요.' if snaps else '아직 추이 데이터가 없습니다.'}"
                    f"</div>", unsafe_allow_html=True)
        return
    period = st.selectbox("기간", ["최근 7일", "최근 14일", "최근 30일", "전체"],
                          key=f"tp_{aid}", label_visibility="collapsed")
    dmap = {"최근 7일": 7, "최근 14일": 14, "최근 30일": 30, "전체": 120}
    rows = TR.with_deltas(snaps)[-dmap[period]:]
    df = _pd.DataFrame(rows).rename(columns={"snapshot_date": "날짜"}).set_index("날짜")
    # 핵심 숫자 카드
    _total = int(df["views"].iloc[-1]) if len(df) else 0
    _recent = int(df["daily_view_delta"].iloc[-1]) if len(df) else 0
    _avg = int(df["daily_view_delta"].clip(lower=0).mean()) if len(df) else 0
    mc = st.columns(3)
    mc[0].metric("누적 조회수", _kabbr(_total))
    mc[1].metric("최근 일 증가", f"+{_kabbr(_recent)}")
    mc[2].metric("일 평균 증가", f"+{_kabbr(_avg)}")
    st.markdown("<div style='font-size:12px;color:#64748B;font-weight:700;margin:10px 0 2px'>"
                "일별 조회수 증가량</div>", unsafe_allow_html=True)
    st.bar_chart(df[["daily_view_delta"]].rename(columns={"daily_view_delta": "조회수"}),
                 height=240, color="#03C75A")


@st.dialog("광고 상세", width="large")
def render_ad_detail(ad: dict) -> None:
    import html as _h
    aid = ad.get("id")
    plat = ad.get("platform", "")
    marked = bool(ad.get("is_bookmarked"))

    # ── YouTube 임베드 가능 여부 사전 판단(영상별 1회 조회 후 DB 캐시) ──
    vurl = ad.get("video_url") or ""
    yt_vid = YT.extract_video_id(vurl) if "youtu" in vurl else None
    emb = ad.get("yt_embeddable")   # 1/0/None
    if yt_vid and emb is None and YT.is_enabled():
        emb = _yt_embeddable(yt_vid)
        database.set_yt_embeddable(aid, emb)
    blocked = yt_vid and (emb == 0 or emb is False)

    # ── 헤더: 브랜드명(가장 크게) + 우측 [보존 토글] + [북마크 별] (개념 구분) ──
    preserved = bool(ad.get("is_preserved"))
    hc = st.columns([7, 1, 1])
    hc[0].markdown(f"<div style='font-size:23px;font-weight:800;color:{S.PRIMARY};"
                   f"letter-spacing:-.3px;line-height:1.25;margin-top:2px'>"
                   f"{_h.escape(_g(ad,'brand_name','-'))}</div>", unsafe_allow_html=True)
    # 보존: retention(60일 자동정리)에서 이 광고를 제외. 북마크와 별개.
    if hc[1].button("🔒 보존" if preserved else "🔓 보존", key=f"preservetop_{aid}",
                    use_container_width=True,
                    type=("primary" if preserved else "secondary"),
                    help=("자동정리(60일) 보존 중 — 눌러서 해제" if preserved
                          else "자동정리(60일)에서 이 광고 삭제 제외로 표시")):
        database.update_preserved(aid, not preserved)
        _reload()
    # 북마크: 관심 광고 저장(별개 개념)
    if hc[2].button("★" if marked else "☆", key=f"bmtop_{aid}", use_container_width=True,
                    type=("primary" if marked else "secondary"),
                    help="북마크 해제" if marked else "북마크 저장"):
        database.update_bookmark(aid, not marked, st.session_state.get('username',''))
        _reload()

    left, right = st.columns([2, 3], gap="medium")
    # ── 좌: 영상/썸네일 + 버튼 ──
    with left:
        th = get_display_thumbnail(ad)
        if yt_vid and blocked:
            # API가 사전에 '임베드 제한' 확인한 경우 — 바로 fallback(플레이어 깜빡임 없이)
            _yt_fallback_ui(ad, yt_vid)
        elif yt_vid:
            # 그 외 — Player API로 재생 시도, 실제 차단(onError) 시 자동 fallback 전환
            _yt_smart_player(ad, yt_vid)
        elif plat == "meta" and (ad.get("media_type") == "video"):
            # Meta 영상: 저장된 video_status 만 '읽기'. **ok 일 때만** st.video → 만료/미상은
            # 검은 플레이어 대신 안내 박스(빈 값=미상도 ok로 보지 않음, 검은화면 방지).
            mstate = (ad.get("video_status") or "").strip()
            _rel = _rel_korean(ad.get("video_url_updated_at"))
            if mstate == "ok" and vurl.startswith("http"):
                st.video(vurl)
                if _rel:
                    st.caption(f"🔄 영상 URL 갱신: {_rel}")
            elif mstate in ("private_or_deleted", "not_found", "unavailable"):
                # 썸네일·광고문구·수집일·브랜드는 그대로 유지하고 영상만 안내로 대체
                if th["src"]:
                    st.markdown(f"<img src='{th['src']}' style='width:100%;min-height:240px;"
                                f"object-fit:contain;background:#0F172A;border-radius:10px'/>",
                                unsafe_allow_html=True)
                st.info("메타에서 원본 광고를 더 이상 확인할 수 없습니다. "
                        "비공개·삭제됐거나 게재가 종료된 광고일 수 있어요 "
                        "(아래 ‘원본 광고’ 접근이 안 될 수 있습니다).")
            else:  # expired_url 또는 빈 값(미상) → 만료 안내(다음 크롤 갱신 대상)
                if th["src"]:
                    st.markdown(f"<img src='{th['src']}' style='width:100%;min-height:240px;"
                                f"object-fit:contain;background:#0F172A;border-radius:10px'/>",
                                unsafe_allow_html=True)
                _ago = f" (마지막 갱신 {_rel})" if _rel else ""
                st.warning(f"⚠ 영상 URL 만료 또는 원본 광고 접근 불가{_ago}. 매일 05:00 자동 수집 때 "
                           f"우선 갱신됩니다. 지금은 아래 ‘원본 광고’에서 확인하세요.")
        elif plat != "meta" and vurl:
            st.video(vurl)
        elif th["src"]:
            # 구글 소재 스크린샷은 작을 수 있어 최대한 크게(업스케일) 표시
            st.markdown(f"<img src='{th['src']}' style='width:100%;min-height:240px;"
                        f"object-fit:contain;background:#0F172A;border-radius:10px'/>",
                        unsafe_allow_html=True)
        else:
            st.markdown(f"<div class='sa-thumb sa-thumb-empty' style='aspect-ratio:1/1'>"
                        f"<div class='sa-ph'><span class='i'>🖼</span>미리보기 없음</div></div>",
                        unsafe_allow_html=True)
        _render_source_buttons(ad)
        # 조회수 추이 분석(피로도 상태 + 기간선택 + 누적/일별증가량/좋아요)
        _render_trend_section(ad, aid)

    # ── 우: 핵심 광고정보(배지) → 지표 → 보조정보 → 카피 → 스크립트 ──
    with right:
        if ad.get("ad_title"):
            st.markdown(f"<div style='font-size:17px;font-weight:800;color:{S.TEXT};"
                        f"margin:4px 0 14px;line-height:1.35'>{_h.escape(ad['ad_title'])}</div>",
                        unsafe_allow_html=True)

        # ── 배지 행: 상태 · 플랫폼 · 게재위치 · CTA · A/B ──
        def _b(txt, bg, fg, bd):
            return (f"<span style='display:inline-flex;align-items:center;background:{bg};color:{fg};"
                    f"border:1px solid {bd};font-size:13px;font-weight:700;padding:4px 12px;"
                    f"border-radius:999px'>{txt}</span>")
        live = ad.get("status") == "live"
        badges = [_b("● 라이브", "#ECFDF5", S.LIVE, "#A7F3D0") if live
                  else _b("● " + (ad.get("status") or "종료"), "#FEF2F2", S.END_RED, "#FECACA"),
                  _b(PLATFORM_LABEL.get(plat, plat or "-"), "#F1F5F9", S.TEXT, S.BORDER)]
        for p in [x.strip() for x in (ad.get("platforms") or "").split(",") if x.strip()]:
            badges.append(_b(f"{PLAT_ICON.get(p,'')} {p}".strip(), "#F8FAFC", S.SUB, S.BORDER))
        if ad.get("cta"):
            badges.append(_b(f"🔘 {_h.escape(ad['cta'])}", S.SOFT_MINT, S.DEEP, "#A7F3D0"))
        nab = int(ad.get("ad_variant_count") or 1)
        if plat == "meta" and nab >= 2:
            badges.append(_b(f"A/B 광고 {nab}개", "#FEF3C7", "#B45309", "#FDE68A"))
        # 재생 상태 배지(YouTube 영상일 때): 재생 가능 / 외부 재생 필요 / 원본 확인 필요
        if yt_vid:
            if blocked:
                badges.append(_b("🔒 외부 재생 필요", "#FEF3C7", "#B45309", "#FDE68A"))
            elif emb in (1, True):
                badges.append(_b("▶ 재생 가능", S.SOFT_MINT, S.DEEP, "#A7F3D0"))
            else:
                badges.append(_b("↗ 원본 확인 필요", "#F1F5F9", S.SUB, S.BORDER))
        st.markdown("<div style='display:flex;flex-wrap:wrap;gap:7px;margin:2px 0 18px'>"
                    + "".join(badges) + "</div>", unsafe_allow_html=True)
        # 같은 소재(영상 중복)로 묶인 광고 수 + 판정 근거
        dupn = int(ad.get("dup_rows") or 1)
        if dupn >= 2:
            gk = str(ad.get("grp_key") or "")
            basis = ("YouTube video_id 동일" if gk.startswith("yt:")
                     else "동일 소재 후보(썸네일 SHA-256·카피·랜딩 일치)" if gk.startswith("meta:")
                     else "동일 그룹")
            st.caption(f"📎 같은 소재로 등록된 광고 **{dupn}개** · 판정근거: {basis} "
                       "— 목록엔 대표 1건만 표시(‘모든 광고 개별 보기’로 전건 확인).")

        # ── 지표(숫자 중심, 낮은 색 강조) — 구글/유튜브 공개 지표 ──
        # 현재 조회수는 **영상 단위 최신값**(video_view_state)을 우선 사용하고, 없으면 광고 행의 yt_*.
        # (추이 그래프는 아래에서 스냅샷을 그대로 사용 — 역할 분리)
        yv, yl, yc = (int(ad.get("yt_views") or 0), int(ad.get("yt_likes") or 0),
                      int(ad.get("yt_comments") or 0))
        _checked_at = ""
        try:
            import services.youtube as _YT
            _vid = _YT.extract_video_id(ad.get("video_url") or "")
            _st = database.get_video_view_state(_vid) if _vid else None
            if _st and int(_st.get("current_view_count") or 0) > 0:
                yv = int(_st["current_view_count"])
                yl = int(_st.get("current_like_count") or yl)
                yc = int(_st.get("current_comment_count") or yc)
                _checked_at = (_st.get("last_checked_at") or "")[:16].replace("T", " ")
        except Exception:  # noqa: BLE001  (상태 테이블 없거나 조회 실패 → 기존 값 그대로)
            pass
        if ad.get("video_url") and (yv or yl or yc):
            # 배경색 있는 카드(색강조) — 조회수/좋아요/댓글
            cards = [("👁", "조회수", _full(yv), "#03C75A"),
                     ("❤", "좋아요", _full(yl), "#EF4444"),
                     ("💬", "댓글", _full(yc), "#0EA5E9")]
            html = "<div style='display:flex;gap:8px;margin:2px 0 8px'>"
            for icon, lab, val, col in cards:
                html += (f"<div style='flex:1;background:#F8FFFB;border:1px solid {S.BORDER};"
                         f"border-radius:14px;padding:13px 8px;text-align:center'>"
                         f"<div style='font-size:11.5px;color:{S.SUB};font-weight:700'>{icon} {lab}</div>"
                         f"<div style='font-size:24px;font-weight:900;color:{col};"
                         f"line-height:1.3;font-variant-numeric:tabular-nums'>{val}</div></div>")
            st.markdown(html + "</div>", unsafe_allow_html=True)
            if _checked_at:
                st.caption(f"조회수 최종 확인 {_checked_at} · 아래 추이는 하루 1회 저장된 스냅샷 기준")
        # (메타·구글 반응지표 미제공 안내문구 삭제)

        # ── 보조 정보: 게재 시작 · 수집 · 광고 ID ──
        started = str(_g(ad, "started_at", ""))[:10]
        sub = [f"게재 시작 {started}" if started
               else "<span style='color:#94A3B8'>게시 시작일 확인 불가</span>",
               f"수집 {str(_g(ad,'collected_at','-'))[:10]}", f"ID {aid}"]
        st.markdown(f"<div style='font-size:13px;color:{S.SUB};margin:14px 0 0'>"
                    + "&nbsp;·&nbsp; ".join(sub) + "</div>", unsafe_allow_html=True)
        if is_valid_external_url(ad.get("landing_url")):
            _lu = ad["landing_url"]
            _disp = _lu if len(_lu) <= 110 else _lu[:110] + "…"
            st.markdown(f"<div style='font-size:13px;margin-top:11px;line-height:1.55;"
                        f"word-break:break-all'>🛒 "
                        f"<a href='{_lu}' target='_blank' style='color:{S.SUB}'>"
                        f"{_h.escape(_disp)}</a></div>", unsafe_allow_html=True)

        st.divider()
        # ── 광고 카피 — 아코디언(>) ──
        copy = (ad.get("ad_copy") or "").strip()
        if copy:
            with st.expander("광고 카피", expanded=False):
                st.code(copy, language=None)   # language=None → 해시태그(#) 기울임/하이라이트 방지
                tags = re.findall(r"#[^\s#]+", copy)
                if tags:
                    st.markdown("<div style='margin-top:6px'>"
                                + "".join(f"<span class='sa-chip'>{_h.escape(t)}</span>" for t in tags[:14])
                                + "</div>", unsafe_allow_html=True)
        else:
            st.markdown("##### 광고 카피")
            empty_who = "Google 투명성센터" if plat == "google" else "이 광고"
            st.markdown(f"<div style='background:{S.BG};border:1px dashed {S.BORDER};border-radius:10px;"
                        f"padding:16px;text-align:center;color:{S.SUB};font-size:12.5px;line-height:1.7'>"
                        f"📝 {empty_who}에서 제공된 광고 카피가 없습니다.<br>"
                        f"<span style='color:#94A3B8'>영상/스크립트를 기준으로 분석해보세요.</span></div>",
                        unsafe_allow_html=True)

        st.divider()
        _render_video_script(ad)

    # ── 내 분석 메모 + 하단 액션(메모 저장 · 원본 보기 · YouTube 연결) ──
    st.divider()
    st.markdown("##### 📝 내 분석 메모")
    memo = st.text_area("메모", value=ad.get("memo") or "", label_visibility="collapsed",
                        key=f"memo_{aid}", placeholder="이 소재의 후킹/카피/구성 포인트를 기록하세요")
    # 이미지 첨부(참고 캡처·레퍼런스) — DB 영구 저장
    _aimgs = database.get_attach_images(f"ad::{aid}")
    if _aimgs:
        from pathlib import Path as _P
        st.caption(f"📎 저장된 첨부 이미지 {len(_aimgs)}장")
        _aic = st.columns(2)
        for _j, _ip in enumerate(_aimgs):
            if _P(_ip).exists():
                _aic[_j % 2].image(_ip, use_container_width=True)
    _aup = st.file_uploader("🖼️ 이미지 첨부(참고 캡처·레퍼런스 등)",
                            type=["png", "jpg", "jpeg", "webp"],
                            accept_multiple_files=True, key=f"adimg_{aid}")
    if _aup:
        st.caption(f"⬆️ 올린 이미지 {len(_aup)}장 — 아래 **💾 메모·이미지 저장**을 눌러야 보관돼요")
        _pc = st.columns(3)
        for _j, _f in enumerate(_aup):
            _pc[_j % 3].image(_f, width=120)
    ac = st.columns([1, 1.5, 1.5])
    if ac[0].button("💾 메모·이미지 저장", use_container_width=True, key=f"sm_{aid}"):
        database.update_memo(aid, memo)
        _asaved = list(_aimgs)
        if _aup:
            import hashlib
            from pathlib import Path as _P
            _d = _P("static/memo_images"); _d.mkdir(parents=True, exist_ok=True)
            _safe = hashlib.md5(f"ad::{aid}".encode("utf-8")).hexdigest()[:10]
            for _k, _f in enumerate(_aup):
                _ext = (_f.name.rsplit(".", 1)[-1] if "." in _f.name else "png").lower()
                _p = _d / f"{_safe}_{len(_asaved) + _k}.{_ext}"
                _p.write_bytes(_f.getbuffer())
                _asaved.append(str(_p).replace("\\", "/"))
            database.save_attach_images(f"ad::{aid}", _asaved)
        st.toast("메모·이미지 저장됨")
        _reload()
    src_url = (normalize_google_transparency_url(ad.get("transparency_url") or ad.get("original_ad_url"))
               if plat == "google" else ad.get("original_ad_url"))
    if is_valid_external_url(src_url):
        ac[1].link_button("🔎 Google 투명성센터에서 보기" if plat == "google" else "🔗 원본 광고 보기",
                          src_url, use_container_width=True)
    with ac[2].popover("▶ YouTube 원본 연결", use_container_width=True):
        if not YT.is_enabled():
            st.caption("YOUTUBE_API_KEY 등록 시 사용 가능")
        else:
            yurl = st.text_input("YouTube URL", key=f"yt_{aid}", placeholder="watch?v=… / shorts/…")
            if st.button("연결", key=f"ytlink_{aid}", type="primary"):
                vid = YT.extract_video_id(yurl)
                data = YT.fetch_video(vid) if vid else None
                if not data:
                    st.error("유효한 URL/영상 아님")
                else:
                    data["brand_name"] = ad.get("brand_name")
                    database.ingest_social_videos([data])
                    database.add_snapshot(data["id"], data["views"], data["likes"],
                                          data["comments"], data["shares"])
                    database.link_ad_social(aid, data["id"], 100.0)
                    database.regrade()
                    st.success("연결 완료")
                    _reload()


# ════════════════════════════════════════════════════════════
_REP_PLAT = {"Naver GFA": ("#03C75A", "#ECFDF5"), "Meta": ("#1877F2", "#EFF6FF"),
             "TikTok": ("#111827", "#F1F5F9")}
_REP_WIN = {"위닝 소재": "#10B981", "위닝 후보": "#0EA5E9", "모니터링": "#F59E0B", "일반 소재": "#94A3B8"}


def _krw(n) -> str:
    return f"{int(n or 0):,}원"


def _rep_win_badge(label: str) -> str:
    c = _REP_WIN.get(label, "#94A3B8")
    return (f"<span style='display:inline-flex;align-items:center;line-height:1;"
            f"background:{c}1A;color:{c};border:1px solid {c}66;font-size:10.5px;"
            f"font-weight:700;padding:4px 9px;border-radius:999px'>{label}</span>")


def _repurely_auto_script(r: dict) -> str:
    """repurely 소재의 영상/썸네일을 AI가 자동 인식 → 스크립트(구간 JSON) 또는 썸네일 요약 텍스트.
    영상: video_id→mp4(자사계정)→Gemini 3초 구간 분석. 실패/이미지: 썸네일 Vision. 둘 다 실패 시 ''."""
    import services.script_gen as SG
    ad_like = {"brand_name": "repurely",
               "ad_copy": r.get("ad_copy") or r.get("campaign_name") or "", "cta": ""}
    plat = r.get("platform", "")
    vurl = ""
    if plat == "TikTok":
        vurl = (r.get("video_url") or "").strip()        # TikTok preview_url(mp4) 직접
    else:
        vid = (r.get("video_id") or "").strip()
        if vid:
            try:
                import repurely.meta_api as MAPI
                vurl = (MAPI.video_info(vid) or {}).get("source", "")
            except Exception:  # noqa: BLE001
                vurl = ""
    if vurl.startswith("http"):
        try:
            status, text, _s, _e = SG._gemini_video_file(vurl, ad_like)
            if status == "completed" and text:
                return text
        except Exception:  # noqa: BLE001
            pass
    # 영상 없음/실패 → 썸네일(이미지) 분석
    try:
        res = SG.analyze_thumbnail({**ad_like,
                                    "thumbnail_url": r.get("thumbnail_url") or "",
                                    "local_thumbnail_path": r.get("thumb_local") or ""})
        if res.get("text"):
            return res["text"]
    except Exception:  # noqa: BLE001
        pass
    return ""


def _render_ai_report(rep) -> None:
    """AI 분석 결과(dict)를 최종 판단 배지 + 평균 기준 + 섹션 카드로 렌더. 구버전 문자열도 호환."""
    import services.ai_insight as AI
    if isinstance(rep, str):          # 이전 버전(단일 문자열) 캐시 호환
        with st.container(border=True):
            st.markdown(rep)
        return
    import html as _h
    # ── 최종 판단 라벨(별도 줄, 아래 여백 충분히) ──
    lab = rep.get("verdict")
    col = rep.get("verdict_color", "#64748B")
    if lab:
        st.markdown(f"<div style='margin:2px 0 14px'>"
                    f"<span style='display:inline-block;background:{col}1A;color:{col};"
                    f"border:1.5px solid {col};font-size:14px;font-weight:800;padding:7px 18px;"
                    f"border-radius:999px;line-height:1.5'>최종 판단 · {_h.escape(str(lab))}</span></div>",
                    unsafe_allow_html=True)
    # ── 비교 기준 — 항목별 정보 박스(그리드, 자동 줄바꿈) ──
    am = rep.get("av_meta") or {}
    if am:
        _metrics = [("평균 ROAS", f"{am.get('roas',0):.0f}%"), ("평균 CTR", f"{am.get('ctr',0):.2f}%"),
                    ("평균 CPC", f"{int(am.get('cpc',0)):,}원"), ("평균 CPM", f"{int(am.get('cpm',0)):,}원"),
                    ("기준 방식", str(am.get("basis", "누적 합계")))]
        _cells = "".join(
            f"<div style='background:#fff;border:1px solid {S.BORDER};border-radius:8px;padding:8px 10px'>"
            f"<div style='font-size:10.5px;color:{S.SUB};font-weight:600;margin-bottom:2px'>{k}</div>"
            f"<div style='font-size:13.5px;color:{S.TEXT};font-weight:700;line-height:1.3;"
            f"word-break:break-all'>{_h.escape(v)}</div></div>" for k, v in _metrics)
        st.markdown(
            f"<div style='background:#F8FAFC;border:1px solid {S.BORDER};border-radius:10px;"
            f"padding:12px 13px;margin-bottom:16px'>"
            f"<div style='font-size:12px;font-weight:800;color:{S.PRIMARY};margin-bottom:6px'>📐 비교 기준</div>"
            f"<div style='font-size:12.5px;color:{S.TEXT};word-break:break-all;line-height:1.5;"
            f"margin-bottom:9px'>기준: {_h.escape(str(am.get('source','-')))}</div>"
            f"<div style='display:grid;grid-template-columns:repeat(3,1fr);gap:8px'>{_cells}</div></div>",
            unsafe_allow_html=True)
    # ── 섹션 카드(제목 줄간격·본문 자동 줄바꿈) ──
    for key, title in AI.FIELDS:
        val = (rep.get(key) or "").strip()
        if not val:
            continue
        with st.container(border=True):
            st.markdown(f"<div style='font-size:13.5px;font-weight:800;color:{S.PRIMARY};"
                        f"margin-bottom:6px;line-height:1.4'>{title}</div>", unsafe_allow_html=True)
            st.markdown(val)
    if isinstance(rep, dict) and rep.get("_source") != "gemini":
        st.caption("규칙 기반 분석 · Gemini 키 설정 시 더 깊은 리포트 제공")


@st.dialog("repurely 소재 상세", width="large")
def _repurely_detail(r: dict) -> None:
    import html as _h
    plat = r.get("platform", "")
    pc, pb = _REP_PLAT.get(plat, ("#6B7280", "#F1F5F9"))
    # ── 헤더: 브랜드 + 채널 설명을 한 줄로(겹침 방지, 줄간격 확보) ──
    st.markdown(
        f"<div style='display:flex;align-items:baseline;gap:10px;flex-wrap:wrap;margin:0 0 12px'>"
        f"<span style='font-size:22px;font-weight:800;color:{S.PRIMARY};line-height:1.4'>repurely</span>"
        f"<span style='font-size:13px;font-weight:600;color:{S.SUB};line-height:1.4'>"
        f"{plat} · 내부 소재 성과</span></div>", unsafe_allow_html=True)

    _bmkey = f"{plat}::{r.get('creative_name')}"
    _isbm = _bmkey in database.get_repurely_bookmarks()
    if st.button("⭐ 북마크됨 — 해제" if _isbm else "☆ 북마크 추가", key=f"repbm_{_bmkey}"):
        database.toggle_repurely_bookmark(_bmkey, not _isbm)
        st.rerun()

    left, right = st.columns([2, 3], gap="large")
    # ── 좌: 소재 영상(인앱 재생)/이미지 + 원본 링크 ──
    with left:
        # 영상은 9:16 유지하되 너무 커지지 않게 높이 제한(폭 자동·가운데)
        st.markdown("<style>[data-testid='stVideo'] video{max-height:480px !important;"
                    "width:auto !important;max-width:100% !important;margin:0 auto;display:block;"
                    "border-radius:12px;}</style>", unsafe_allow_html=True)
        _th = _resolve_media(r.get("thumb_local"), r.get("thumbnail_url"))
        _vid = r.get("video_id") or ""
        permalink = mp4 = ""
        # TikTok preview_url(mp4)·cover는 서명·만료 URL → 영구 로컬썸네일(thumb_local)이 있으면
        # 그걸 미리보기로 우선 사용(클라우드에서도 안정). 로컬썸 없을 때만 mp4 재생 시도.
        _tt_local_thumb = plat == "TikTok" and (r.get("thumb_local") or "").strip()
        if plat == "TikTok" and (r.get("video_url") or "").startswith("http") and not _tt_local_thumb:
            mp4 = r.get("video_url")            # TikTok preview_url(mp4) 직접 재생(로컬썸 없을 때)
        elif _vid and plat != "TikTok":   # Meta 자사 계정 영상 → 원본 mp4(source) 조회해 st.video 재생
            try:
                import repurely.meta_api as MAPI
                vi = MAPI.video_info(_vid)
                mp4 = vi.get("source", "")
                permalink = vi.get("permalink", "")
                _th = vi.get("thumbnail") or _th
            except Exception:  # noqa: BLE001
                pass
        if mp4:                # 원본 mp4 → Meta 탭과 동일한 네이티브 플레이어(원본 화질)
            st.video(mp4)
        elif permalink:        # mp4 미제공 시 → FB 임베드로 인앱 재생(세로 9:16 맞춰 잘림 없이)
            from urllib.parse import quote
            src = (f"https://www.facebook.com/plugins/video.php?href={quote(permalink, safe='')}"
                   f"&show_text=0&width=300&height=533")
            stc.html(f"<div style='display:flex;justify-content:center'>"
                     f"<iframe src='{src}' width='300' height='533' style='border:none;"
                     f"border-radius:10px;overflow:hidden' scrolling='no' frameborder='0' "
                     f"allow='autoplay; clipboard-write; encrypted-media; picture-in-picture; "
                     f"web-share' allowfullscreen></iframe></div>", height=545)
        elif _th and (_th.startswith("http") or _th.startswith("data:")):   # 이미지 소재 또는 영상 썸네일
            st.markdown(f"<div style='position:relative;border-radius:12px;overflow:hidden;"
                        f"aspect-ratio:9/16;max-width:400px;max-height:480px;margin:0 auto;"
                        f"background:#0F172A'>"
                        f"<img src='{_th}' loading='lazy' decoding='async' "
                        f"style='width:100%;height:100%;object-fit:contain'/></div>",
                        unsafe_allow_html=True)
            if _tt_local_thumb:
                st.caption("🎬 TikTok 소재 미리보기 · 영상 원본은 서명 URL이라 만료될 수 있어요")
        else:
            st.markdown(f"<div class='sa-thumb sa-thumb-empty' style='aspect-ratio:9/16'>"
                        f"<div class='sa-ph'><span class='i'>🎬</span>소재 미리보기 없음</div></div>",
                        unsafe_allow_html=True)
        pills = []
        if permalink:
            pills.append(f"<a href='{permalink}' target='_blank' style='font-size:12px;"
                         f"color:{S.SUB};border:1px solid {S.BORDER};border-radius:8px;padding:4px 11px;"
                         f"text-decoration:none'>▶ Facebook에서 보기 ↗</a>")
        if (r.get("landing") or "").startswith("http"):
            pills.append(f"<a href='{r['landing']}' target='_blank' style='font-size:12px;"
                         f"color:{S.SUB};border:1px solid {S.BORDER};border-radius:8px;padding:4px 11px;"
                         f"text-decoration:none'>🛒 랜딩 ↗</a>")
        if pills:
            st.markdown(f"<div style='display:flex;gap:7px;flex-wrap:wrap;margin-top:9px'>"
                        + "".join(pills) + "</div>", unsafe_allow_html=True)

    # ── 우: 배지 → 지표 → 보조정보 → 상태요약 ──
    with right:
        st.markdown(f"<div style='display:flex;gap:6px;flex-wrap:wrap;margin:2px 0 12px'>"
                    f"<span style='background:{pb};color:{pc};font-size:12px;font-weight:700;"
                    f"padding:3px 11px;border-radius:999px'>{plat}</span>"
                    f"<span style='background:#EEF2FF;color:#4F46E5;font-size:12px;font-weight:700;"
                    f"padding:3px 11px;border-radius:999px'>내부 데이터</span>"
                    f"{_rep_win_badge(r.get('winning_label','-'))}"
                    f"<span style='background:{'#FEF2F2' if r.get('is_off') else '#ECFDF5'};"
                    f"color:{'#EF4444' if r.get('is_off') else '#10B981'};font-size:12px;font-weight:700;"
                    f"padding:3px 11px;border-radius:999px'>"
                    f"{'🔴 OFF 후보' if r.get('is_off') else '🟢 운영중'}</span></div>", unsafe_allow_html=True)
        # ── 핵심 성과 카드(크게, 먼저) ──
        cards = [("광고비", _krw(r.get("spend")), "#03C75A"), ("매출", _krw(r.get("revenue")), "#EF4444"),
                 ("구매", f"{int(r.get('conversions',0))}건", "#0EA5E9"),
                 ("ROAS", f"{r.get('roas',0):.0f}%", "#10B981")]
        html = "<div style='display:grid;grid-template-columns:repeat(4,1fr);gap:9px;margin:2px 0 9px'>"
        for lab, val, col in cards:
            html += (f"<div style='background:#F8FFFB;border:1px solid {S.BORDER};border-radius:14px;"
                     f"padding:15px 6px;text-align:center'><div style='font-size:12px;color:{S.SUB};"
                     f"font-weight:700;margin-bottom:3px'>{lab}</div>"
                     f"<div style='font-size:23px;font-weight:900;color:{col};line-height:1.2'>{val}</div></div>")
        st.markdown(html + "</div>", unsafe_allow_html=True)
        st.markdown(f"<div style='font-size:13px;color:{S.SUB};margin:2px 0 14px'>"
                    f"CTR <b style='color:{S.TEXT}'>{r.get('ctr',0)}%</b> · "
                    f"CPC <b style='color:{S.TEXT}'>{int(r.get('cpc',0)):,}원</b> · "
                    f"CPM <b style='color:{S.TEXT}'>{int(r.get('cpm',0)):,}원</b></div>", unsafe_allow_html=True)
        # TikTok 매칭 영상지표(조회/완료율/시청시간/인게이지먼트)
        _tt = r.get("tt_metrics") or {}
        if _tt:
            _pv = _tt.get("video_play_actions", 0) or 0
            _comp = f"{(_tt.get('video_views_p100',0) or 0)/_pv*100:.0f}%" if _pv else "-"
            _items = [("👁 재생", _kabbr(_pv)), ("2초 조회", _kabbr(_tt.get("video_watched_2s", 0))),
                      ("6초 조회", _kabbr(_tt.get("video_watched_6s", 0))), ("완료율", _comp),
                      ("평균시청", f"{_tt.get('average_video_play',0):.0f}초"),
                      ("❤ 좋아요", int(_tt.get("likes", 0))), ("💬 댓글", int(_tt.get("comments", 0))),
                      ("↗ 공유", int(_tt.get("shares", 0)))]
            _chips = "".join(
                f"<div style='background:#F8FAFC;border:1px solid {S.BORDER};border-radius:11px;"
                f"padding:10px 6px;text-align:center'>"
                f"<div style='font-size:11.5px;color:{S.SUB};font-weight:600;margin-bottom:2px'>{lab}</div>"
                f"<div style='font-size:18px;font-weight:800;color:{S.TEXT};line-height:1.2'>{val}</div></div>"
                for lab, val in _items)
            st.markdown(f"<div style='margin:6px 0 16px'><div style='font-size:14px;font-weight:800;"
                        f"color:{S.PRIMARY};margin-bottom:8px'>📱 TikTok 영상 지표 (최근 30일)</div>"
                        f"<div style='display:grid;grid-template-columns:repeat(4,1fr);gap:8px'>{_chips}</div></div>",
                        unsafe_allow_html=True)
        # ── 소재 정보(캠페인/광고그룹/소재명/UTM) — 핵심 지표 아래 별도 섹션 ──
        meta = [("캠페인", r.get("campaign_name")), ("광고그룹", r.get("ad_group_name")),
                ("소재명", r.get("creative_name")), ("UTM", r.get("utm_value"))]
        st.markdown(
            f"<div style='background:#F8FAFC;border:1px solid {S.BORDER};border-radius:12px;"
            f"padding:16px 18px;margin:2px 0 12px'>"
            f"<div style='font-size:12px;font-weight:700;color:{S.SUB};margin-bottom:10px'>소재 정보</div>"
            + "".join(
                f"<div style='display:flex;gap:12px;padding:6px 0;font-size:14px;line-height:1.55'>"
                f"<span style='flex:0 0 74px;color:{S.TEXT};font-weight:800'>{k}</span>"
                f"<span style='flex:1;color:{S.TEXT};word-break:break-all'>{_h.escape(str(v or '-'))}</span></div>"
                for k, v in meta)
            + "</div>", unsafe_allow_html=True)
        # 자동 상태 요약
        st.markdown(f"<div style='background:#F8FFFB;border:1px solid {S.BORDER};border-radius:10px;"
                    f"padding:11px 13px;font-size:13px;color:{S.TEXT};line-height:1.6;margin-top:10px'>"
                    f"🤖 {_h.escape(r.get('status_text',''))}</div>", unsafe_allow_html=True)
        st.markdown(f"<div style='font-size:12px;color:{S.OFF_GRAY};line-height:1.7;"
                    f"margin:18px 0 6px'>날짜별 추이는 매일 스냅샷이 누적되면 표시됩니다. "
                    f"현재 시트 값은 누적 합계 기준입니다.</div>", unsafe_allow_html=True)

    # ── AI 상세 분석 리포트(전체 폭) ──
    st.divider()
    import services.ai_insight as AI
    ac = st.columns([5, 1.4], vertical_alignment="center")
    ac[0].markdown(f"<div style='font-size:15px;font-weight:800;color:{S.TEXT};line-height:1.5'>"
                   f"🤖 AI 상세 분석 리포트</div>"
                   f"<div style='font-size:11.5px;color:{S.SUB};margin-top:5px;line-height:1.5'>"
                   f"{'Gemini' if AI.enabled() else '규칙 기반'} · 후킹·스크립트·전환 구조까지 진단</div>",
                   unsafe_allow_html=True)
    skey = f"_repscript_{plat}_{r.get('creative_name','')}"
    with st.expander("🎬 영상 스크립트/대본 입력 (선택 — 넣으면 장면 흐름·이탈 구간까지 분석)"):
        st.text_area("자막·대사·장면 흐름", key=skey, height=130, label_visibility="collapsed",
                     placeholder="(0~3초) 첫 장면/자막 …\n(3~10초) 문제 제기 …\n(중반) 공감·해결책·제품 …\nCTA: …")
    aikey = f"_repai_{plat}_{r.get('creative_name','')}"
    dbkey = f"{plat}::{r.get('creative_name','')}"
    cache = st.session_state.setdefault("_repai_cache", {})
    # 저장된 이전 분석을 DB에서 로드(세션에 없을 때) — 새로고침/재접속해도 계속 보임
    if aikey not in cache:
        _saved = database.get_repurely_report(dbkey)
        if _saved:
            cache[aikey] = _saved["report"]
            st.session_state[aikey + "_at"] = _saved.get("updated_at", "")
    _btn_label = "🔄 AI 재분석" if cache.get(aikey) else "AI 분석 실행"
    if ac[1].button(_btn_label, key=f"aibtn_{aikey}", use_container_width=True, type="primary"):
        with st.spinner("영상 인식 + 분석 중… (자막·장면·후킹·전환)"):
            # 영상/썸네일 자동 인식 — 수동 입력이 있으면 그걸 우선, 없으면 AI가 자동 추출
            _script = (st.session_state.get(skey, "") or "").strip()
            if not _script:
                _script = _repurely_auto_script(r)
                st.session_state[aikey + "_script"] = _script   # 추출 원본 보관(표시용)
            _all = st.session_state.get("_rep_rows_all", [])
            _peers = [x for x in _all if x.get("platform") == plat
                      and x.get("winning_label") == "위닝 소재"
                      and x.get("creative_name") != r.get("creative_name")]
            # 비교 기준 = 대시보드 시트 첫 요약행(매체+랜딩별). 없으면 소재 평균 폴백
            _benches = (st.session_state.get("_rep_benchmarks", {}) or {}).get(plat) or {}
            _ldg = r.get("landing_type") or ""
            _bench = _benches.get(_ldg) if isinstance(_benches, dict) else None
            _av = _bench or st.session_state.get("_rep_av") or {}
            _av_meta = {
                "source": (f"대시보드 {_ldg} 전체 요약 지표" if _bench else f"repurely {plat} 소재 평균"),
                "basis": "오늘 누적",
                "roas": _av.get("roas", 0), "ctr": _av.get("ctr", 0),
                "cpc": _av.get("cpc", 0), "cpm": _av.get("cpm", 0)}
            _rep = AI.analyze(r, peers=_peers, av=_av, av_meta=_av_meta, script=_script)
            cache[aikey] = _rep
            database.save_repurely_report(dbkey, plat, r.get("creative_name", ""), _rep, _script)
            from datetime import datetime, timezone
            st.session_state[aikey + "_at"] = datetime.now(timezone.utc).isoformat()
    if cache.get(aikey):
        _at = st.session_state.get(aikey + "_at", "")
        if _at:
            st.caption(f"💾 저장된 분석 · {_at[:16].replace('T',' ')} · 재분석하면 최신 데이터로 갱신")
        _render_ai_report(cache[aikey])
    else:
        st.caption("‘AI 분석 실행’을 누르면 종합 진단·성과 해석·후킹·스크립트·강약점·피로도·"
                   "개선 카피·다음 테스트까지 리포트로 보여줍니다.")

    # ── 분석 메모 + 이미지 첨부(전체 폭) — DB 영구 저장(세션 휘발 방지) ──
    st.divider()
    st.markdown("##### 📝 분석 메모")
    mkey = f"repmemo_{plat}_{r.get('creative_name','')}"
    _memos = _rep_memos_cached()
    _cur = _memos.get(mkey) or {"memo": "", "images": []}
    st.text_area("메모", key=mkey, label_visibility="collapsed",
                 placeholder="이 소재의 후킹/카피/구성 포인트, 운영 판단을 기록하세요",
                 value=_cur.get("memo", ""))
    _imgs = _cur.get("images", [])
    if _imgs:
        from pathlib import Path as _P
        st.caption(f"📎 저장된 첨부 이미지 {len(_imgs)}장")
        _ic = st.columns(2)
        for _j, _ip in enumerate(_imgs):
            if _P(_ip).exists():
                _ic[_j % 2].image(_ip, use_container_width=True)
    _up = st.file_uploader("🖼️ 이미지 첨부(참고 캡처·레퍼런스 등)",
                           type=["png", "jpg", "jpeg", "webp"],
                           accept_multiple_files=True, key=f"upimg_{mkey}")
    if _up:
        st.caption(f"⬆️ 올린 이미지 {len(_up)}장 — 아래 **💾 메모·이미지 저장**을 눌러야 보관돼요")
        _pc = st.columns(3)
        for _j, _f in enumerate(_up):
            _pc[_j % 3].image(_f, width=120)
    if st.button("💾 메모·이미지 저장", key=f"repsave_{mkey}"):
        import database as _DB, hashlib
        from pathlib import Path as _P
        _saved = list(_imgs)
        if _up:
            _d = _P("static/memo_images"); _d.mkdir(parents=True, exist_ok=True)
            _safe = hashlib.md5(mkey.encode("utf-8")).hexdigest()[:10]
            for _k, _f in enumerate(_up):
                _ext = (_f.name.rsplit(".", 1)[-1] if "." in _f.name else "png").lower()
                _p = _d / f"{_safe}_{len(_saved) + _k}.{_ext}"
                _p.write_bytes(_f.getbuffer())
                _saved.append(str(_p).replace("\\", "/"))
        _DB.save_repurely_memo(mkey, st.session_state.get(mkey, ""), _saved)
        _rep_memos_cached.clear()
        st.toast("메모·이미지 저장됨")


def _repurely_card(r: dict, key: str) -> None:
    import html as _h
    plat = r.get("platform", "")
    pc, pb = _REP_PLAT.get(plat, ("#6B7280", "#F1F5F9"))
    status = ("🔴 OFF 후보" if r.get("is_off") else
              ("⚠️ 피로도 의심" if r.get("is_fatigue") else "🟢 운영중"))
    th = _resolve_media(r.get("thumb_local"), r.get("thumbnail_url"))
    is_meta = plat == "Meta"
    is_video = bool(r.get("video_id"))
    # Insight 카드 = Meta 탭과 같은 3:4 박스, 단 영상 썸네일은 contain으로 전체 표시
    # (cover로 꽉 채우면 좌우가 잘려 확대·흐림). sa-thumb-rep = 3:4 + contain + 어두운 여백.
    meta_cls = " sa-thumb-rep"
    if th:
        play = "<div class='sa-play'>▶</div>" if is_video else ""
        media_label = ("▶ 영상" if is_video else "🖼 이미지") if is_meta else plat
        thumb_html = (f"<div class='sa-thumb{meta_cls}'>"
                      f"<img src='{th}' loading='lazy' decoding='async'/>{play}"
                      f"<div class='sa-media'>{media_label}</div></div>")
    else:
        icon = "🎬" if is_video else "🖼"
        thumb_html = (f"<div class='sa-thumb{meta_cls} sa-thumb-empty'>"
                      f"<div class='sa-ph'><span class='i'>{icon}</span>미리보기 없음</div></div>")
    with st.container(border=True):
        st.markdown(thumb_html, unsafe_allow_html=True)
        st.markdown(
            f"<div style='display:flex;justify-content:space-between;align-items:center;gap:4px;"
            f"margin-top:9px'>"
            f"<span style='font-weight:800;color:{S.PRIMARY};font-size:13.5px'>repurely</span>"
            f"<span style='display:flex;gap:3px'>"
            f"<span style='background:#EEF2FF;color:#4F46E5;font-size:9.5px;font-weight:700;"
            f"padding:2px 6px;border-radius:6px'>내부 데이터</span>"
            f"<span style='background:{pb};color:{pc};font-size:10px;font-weight:700;"
            f"padding:2px 8px;border-radius:6px'>{plat}</span></span></div>"
            f"<div style='font-size:13.5px;font-weight:700;color:{S.TEXT};margin:7px 0 1px;"
            f"white-space:nowrap;overflow:hidden;text-overflow:ellipsis'>"
            f"{'⭐ ' if r.get('is_bm') else ''}{_h.escape(r.get('creative_name') or '-')}</div>"
            f"<div style='font-size:11px;color:{S.SUB};white-space:nowrap;overflow:hidden;"
            f"text-overflow:ellipsis'>{_h.escape(r.get('campaign_name') or '')}</div>"
            f"<div style='font-size:12px;color:{S.TEXT};margin-top:8px'>오늘 광고비 "
            f"<b>{_krw(r.get('spend'))}</b> · 매출 <b>{_krw(r.get('revenue'))}</b> · "
            f"ROAS <b style='color:#10B981'>{r.get('roas',0):.0f}%</b> · 구매 {int(r.get('conversions',0))}</div>"
            f"<div style='font-size:10px;color:{S.SUB};margin-top:6px;opacity:.8'>🕐 업데이트 "
            f"{str(r.get('collected_at') or '-')[:16]}</div>"
            f"<div style='height:9px'></div>", unsafe_allow_html=True)
        if st.button("상세 보기", key=f"repbtn_{key}", use_container_width=True):
            _repurely_detail(r)


@st.cache_resource(ttl=3600, show_spinner=False)
def _rep_daily_cached():
    import repurely.insights as RI
    return RI.daily_by_segment(21)   # 직전 완결 주 + 그 전 주(전주 대비)까지 커버


@st.cache_data(ttl=120, show_spinner=False)
def _rep_memos_cached():
    import database as _DB
    return _DB.get_repurely_memos()


def _render_kpi(k: dict) -> None:
    """상단 요약 KPI — 분류 개수 + 오늘/7일 전체 + 전일·전주 대비 ROAS."""
    def _chg(v):
        if v is None:
            return f"<span style='color:{S.SUB}'>–</span>"
        c = "#10B981" if v >= 0 else "#EF4444"
        return f"<span style='color:{c};font-weight:700'>{'▲' if v >= 0 else '▼'} {abs(v)}%</span>"
    cells = [
        ("🔥 금일 효율", f"{k['cnt_today']}개", S.PRIMARY),
        ("📈 주간 효율", f"{k['cnt_week']}개", "#0EA5E9"),
        ("⭐ 지속 효율", f"{k['cnt_sustained']}개", "#10B981"),
        ("오늘 광고비", _krw(k['today_spend']), S.TEXT),
        ("오늘 매출", _krw(k['today_revenue']), S.TEXT),
        ("오늘 ROAS", f"{k['today_roas']:.0f}%", "#10B981"),
        ("주간 광고비", _krw(k['week_spend']), S.TEXT),
        ("주간 매출", _krw(k['week_revenue']), S.TEXT),
        ("주간 ROAS", f"{k['week_roas']:.0f}%", "#10B981"),
    ]
    html = "<div style='display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin:2px 0 6px'>"
    for lab, val, col in cells:
        html += (f"<div style='background:#F8FFFB;border:1px solid {S.BORDER};border-radius:12px;"
                 f"padding:12px 8px;text-align:center'>"
                 f"<div style='font-size:11px;color:{S.SUB};font-weight:600;margin-bottom:3px'>{lab}</div>"
                 f"<div style='font-size:18px;font-weight:900;color:{col};line-height:1.2'>{val}</div></div>")
    html += "</div>"
    st.markdown(html, unsafe_allow_html=True)
    st.markdown(f"<div style='font-size:12.5px;color:{S.SUB};margin:0 0 12px'>"
                f"전일 대비 ROAS {_chg(k['roas_vs_yday'])} &nbsp;·&nbsp; "
                f"전주 대비 ROAS {_chg(k['roas_vs_pweek'])}<br>"
                f"<span style='font-size:11.5px'>주간 = {k.get('week_label','')} (지난 월~일 완결 주) · 전주 = {k.get('pweek_label','')}</span>"
                f"</div>", unsafe_allow_html=True)


def render_repurely_insights(rows: list[dict], last_sync: str = "") -> None:
    """repurely 내부 소재 분석 탭 — 매체별(Meta/TikTok/Naver GFA) 섹션별 소재 카드.
    last_sync: 데이터가 실제 fetch 된 시각('YYYY-MM-DD HH:MM'). 캐시 적중 중에도 진짜 갱신시각."""
    import repurely.insights as RI
    # 동기화 실패(빈 결과) 시 마지막 정상 데이터 유지
    if rows:
        st.session_state["_rep_last_good"] = rows
        stale = False
    else:
        rows = st.session_state.get("_rep_last_good", [])
        stale = bool(rows)
    if not rows:
        st.markdown(f"<div style='text-align:center;padding:4rem 1rem;color:{S.SUB}'>"
                    f"<div style='font-size:48px'>📊</div><div style='font-size:16px;font-weight:700;"
                    f"color:{S.TEXT};margin-top:.5rem'>repurely 성과 데이터를 불러오지 못했습니다</div>"
                    f"<div style='font-size:13px;margin-top:.3rem'>구글시트 공개 설정 또는 서비스계정 인증을 "
                    f"확인해주세요.</div></div>", unsafe_allow_html=True)
        return
    rows, av = RI.enrich(rows)
    _bms = database.get_repurely_bookmarks()
    for r in rows:
        r["is_bm"] = f"{r.get('platform')}::{r.get('creative_name')}" in _bms
    st.session_state["_rep_rows_all"] = rows   # AI 분석 위너 비교용
    st.session_state["_rep_av"] = av
    if "_rep_benchmarks" not in st.session_state:   # 매체별 시트 요약행(평균 기준값)
        st.session_state["_rep_benchmarks"] = RI.benchmarks()

    # ── 헤더 + 동기화 상태 + 새로고침 ──
    # 실제 fetch 시각(last_sync) 우선 — 캐시 적중 중에도 '진짜 마지막 갱신'을 보여줌.
    # 없으면(구버전 호출) 행의 collected_at 최댓값으로 폴백.
    from datetime import datetime as _dt, timedelta as _td
    last_sync = (last_sync or "").strip() or max((r.get("collected_at", "") for r in rows), default="-")
    nxt = "-"
    rel = ""
    try:
        _ts = _dt.strptime(last_sync, "%Y-%m-%d %H:%M")
        nxt = (_ts + _td(hours=1)).strftime("%H:%M")
        _now = _dt.now()
        _mins = int((_now - _ts).total_seconds() // 60)
        if _now.date() == _ts.date():
            rel = "오늘" if _mins >= 60 else ("방금" if _mins < 2 else f"{_mins}분 전")
            if _mins >= 60:
                rel = f"오늘 · {_mins // 60}시간 전"
        else:
            _days = (_now.date() - _ts.date()).days
            rel = f"⚠ {_days}일 전 (오래됨)"
    except Exception:  # noqa: BLE001
        nxt = "-"
    rel_html = (f" <span style='color:{S.PRIMARY};font-weight:700'>({rel})</span>" if rel else "")
    hc = st.columns([4, 1.3])
    hc[0].markdown(f"<div style='font-size:16px;font-weight:800;color:{S.TEXT};margin:.2rem 0 0'>"
                   f"🏢 repurely 내부 소재 성과</div>"
                   f"<div style='font-size:11.5px;color:{S.SUB};margin-top:3px;line-height:1.7'>"
                   f"📊 분석 기준 <b style='color:{S.TEXT}'>오늘 누적 데이터</b> · "
                   f"마지막 갱신 <b style='color:{S.TEXT}'>{last_sync}</b>{rel_html} · "
                   f"다음 갱신 ~{nxt} · <b>1시간 주기</b></div>", unsafe_allow_html=True)
    if hc[1].button("🔄 지금 갱신", use_container_width=True):
        st.session_state.pop("_rep_cache", None)
        st.session_state.pop("_rep_benchmarks", None)   # 평균 기준도 재로딩
        st.session_state.pop("_rep_last_good", None)
        st.cache_data.clear()
        st.cache_resource.clear()                       # repurely 1시간 캐시 강제 갱신
        st.rerun()
    if stale:
        st.warning("Meta 시트 동기화 실패 · 마지막 정상 데이터 표시 중", icon="⚠️")
    st.markdown("<div style='height:6px'></div>", unsafe_allow_html=True)

    # ── 매체 선택 + 랜딩 구분(블로그/직접랜딩) ──
    plats = ["Meta", "TikTok", "Naver GFA"]
    sel_p = st.segmented_control("매체", plats, default="Meta", key="rep_plat",
                                 label_visibility="collapsed") or "Meta"
    landings = [l for l in ("블로그", "직접랜딩")
                if any(r.get("platform") == sel_p and r.get("landing_type") == l for r in rows)]
    sel_l = None
    if landings:
        sel_l = st.segmented_control("랜딩 구분", landings, default=landings[0],
                                     key=f"rep_landing_{sel_p}",
                                     label_visibility="collapsed") or landings[0]
    only_bm = st.checkbox("⭐ 북마크한 소재만 보기", key="rep_only_bm")
    view = [r for r in rows if r.get("platform") == sel_p
            and (not sel_l or r.get("landing_type") == sel_l)
            and (not only_bm or r.get("is_bm"))]

    # ── 상단 요약 KPI(접기/펴기 · 선택 매체·랜딩 기준) ──
    with st.expander(f"📊 {sel_p}{' · ' + sel_l if sel_l else ''} 요약 KPI", expanded=False):
        try:
            import repurely.insights as _RI
            _render_kpi(_RI.kpi_from_daily_seg(_rep_daily_cached(), sel_p, sel_l, view))
        except Exception:  # noqa: BLE001
            st.caption("요약 데이터를 불러오는 중…")

    # ── 효율 3분류: 금일 / 주간 / 지속 (효율 좋은 소재만 — 기타/종료 섹션 제거) ──
    sections = [
        ("🔥 금일 효율 좋은 소재", [r for r in view if r.get("is_today_eff")]),
        ("📈 주간 효율 좋은 소재", [r for r in view if r.get("is_week_eff")]),
        ("⭐ 지속 효율 소재", [r for r in view if r.get("is_sustained")]),
    ]
    for title, items in sections:
        items = sorted(items, key=lambda x: -(x.get("roas", 0) or 0))   # 오늘 ROAS 내림차순

        st.markdown(f"<div style='font-size:15px;font-weight:800;color:{S.TEXT};margin:1.1rem 0 .5rem'>"
                    f"{title} <span style='color:{S.SUB};font-size:13px;font-weight:600'>"
                    f"{len(items)}개</span></div>", unsafe_allow_html=True)
        if not items:
            st.caption("해당 소재가 없습니다.")
            continue
        for i in range(0, len(items[:24]), 4):
            cc = st.columns(4)
            for col, r in zip(cc, items[i:i + 4]):
                with col:
                    _repurely_card(r, f"{title}_{i}_{r.get('platform')}_{r.get('creative_name','')}")


def render_empty_state(msg: str = "표시할 광고가 없습니다") -> None:
    st.markdown(f"""
    <div style='text-align:center; padding:5rem 1rem; color:{S.SUB}'>
      <div style='font-size:54px'>🗂️</div>
      <div style='font-size:17px; font-weight:700; color:{S.TEXT}; margin-top:.6rem'>{msg}</div>
      <div style='font-size:13px; margin-top:.3rem'>필터를 바꾸거나 새 브랜드를 수집해 보세요.</div>
    </div>
    """, unsafe_allow_html=True)


def render_brand_trend_summary(brand: str) -> None:
    """브랜드 단위 추이 요약: 총 조회수 · 운영중 · 피로도 의심 · 성장 중 + 일별 총 조회수."""
    s = database.get_brand_trend_summary(brand)
    with st.expander(f"📊 {brand} 추이 요약", expanded=False):
        m = st.columns(2)
        m[0].metric("브랜드 총 조회수", _kabbr(s["total_views"]) if s["total_views"] else "-")
        m[1].metric("운영 중 광고", f"{s['live']}개")
        if len(s["daily"]) >= 2:
            import pandas as _pd
            df = (_pd.DataFrame(s["daily"]).rename(columns={"snapshot_date": "날짜", "views": "총 조회수"})
                  .set_index("날짜"))
            st.markdown("<div style='font-size:11px;color:#64748B;font-weight:700'>일별 브랜드 총 조회수</div>",
                        unsafe_allow_html=True)
            st.line_chart(df, height=140)
        else:
            st.caption("일별 추이는 스냅샷이 2일치 이상 쌓이면 표시됩니다.")


def parse_ad_ids(raw: str) -> list:
    """입력 텍스트에서 광고 ID 추출. 지원: 쉼표/공백/줄바꿈 구분, Meta Ad Library URL(여러 개),
       숫자 라이브러리 ID, UUID. 순서 유지 + 중복 제거."""
    raw = raw or ""
    ids: list = []
    seen: set = set()

    def _add(v: str) -> None:
        v = (v or "").strip()
        if v and v not in seen:
            seen.add(v)
            ids.append(v)

    # 1) URL(여러 개 가능) 안의 id= 값 먼저 추출
    for m in re.findall(r"[?&]id=([0-9A-Za-z\-]+)", raw):
        _add(m)
    # 2) URL 제거 후 남은 토큰을 구분자(쉼표/공백/줄바꿈)로 분리
    text = re.sub(r"https?://\S+", " ", raw)
    for tok in re.split(r"[\s,]+", text):
        tok = tok.strip()
        if not tok:
            continue
        if tok.isdigit() and len(tok) >= 6:                       # 숫자 라이브러리 ID
            _add(tok)
        elif re.fullmatch(r"[0-9a-fA-F]{8}-[0-9a-fA-F-]{4,}", tok):  # UUID 형식
            _add(tok)
    return ids


def render_id_search(raw: str) -> None:
    """광고 ID 다중 검색 결과 — 매칭/미수집 요약 + 카드 그리드."""
    ids = parse_ad_ids(raw)
    if not ids:
        st.warning("인식된 광고 ID가 없어요. 숫자 ID나 Meta Ad Library 링크를 붙여넣어 주세요.")
        return
    rows = database.get_ads_by_ids(ids)
    found_ids = {str(r.get("id")) for r in rows}
    matched = [i for i in ids if i in found_ids]
    missing = [i for i in ids if i not in found_ids]

    st.markdown(
        f"<div class='sa-info'>🔎 ID 검색 · 입력 <b>{len(ids)}</b>개 · "
        f"<span style='color:{S.LIVE}'>검색됨 <b>{len(matched)}</b></span> / "
        f"<span style='color:{S.END_RED}'>미수집 <b>{len(missing)}</b></span></div>",
        unsafe_allow_html=True)
    if matched:
        st.caption("✅ 매칭된 ID: " + ", ".join(matched))
    if missing:
        st.caption("⚠️ 미수집 ID: " + ", ".join(missing))

    # 미수집 ID → 즉시 수집 버튼 + 원본 열기
    if missing:
        st.markdown("현재 DB에 없는 광고 ID입니다. Meta Ad Library 원본에서 바로 수집을 시도할 수 있습니다.")
        mc = st.columns([1.4, 1, 3])
        if mc[0].button(f"📥 미수집 광고 바로 수집하기 ({len(missing)})", key="idsearch_collect",
                        type="primary", use_container_width=True):
            res = _collect_ids(missing)
            st.session_state["idsearch_collect_res"] = res
            st.cache_data.clear()      # 새 광고가 검색결과에 바로 보이게
            st.rerun()
        first = missing[0]
        mc[1].markdown(
            f"<a href='https://www.facebook.com/ads/library/?id={first}' target='_blank' "
            f"style='display:inline-block;text-align:center;width:100%;font-size:13px;color:{S.SUB};"
            f"border:1px solid {S.BORDER};border-radius:8px;padding:7px 0;text-decoration:none'>"
            f"Meta 원본 열기 ↗</a>", unsafe_allow_html=True)
        links = "".join(
            f"<a href='https://www.facebook.com/ads/library/?id={i}' target='_blank' "
            f"style='font-size:11px;color:{S.OFF_GRAY};text-decoration:none'>{i}↗</a>" for i in missing)
        st.markdown(f"<div style='display:flex;gap:9px;flex-wrap:wrap;margin-top:5px'>{links}</div>",
                    unsafe_allow_html=True)

    # 직전 수집 결과 요약(부분 성공/실패 + 사유)
    cres = st.session_state.get("idsearch_collect_res")
    if cres:
        ok_new = [r for r in cres if r.get("is_new")]
        had = [r for r in cres if r.get("ok") and not r.get("is_new")]
        fail = [r for r in cres if not r.get("ok")]
        st.markdown(
            f"<div class='sa-info'>수집 결과 · 입력 <b>{len(cres)}</b> · 기존보유 <b>{len(had)}</b> · "
            f"<span style='color:{S.LIVE}'>신규성공 <b>{len(ok_new)}</b></span> · "
            f"<span style='color:{S.END_RED}'>실패 <b>{len(fail)}</b></span></div>",
            unsafe_allow_html=True)
        for r in fail:
            st.caption(f"❌ {r['id']} — {r.get('reason','실패')}")
        if any(r.get("page_id") for r in ok_new):
            pids = ", ".join(f"{r['id']}→page_id {r['page_id']}" for r in ok_new if r.get("page_id"))
            st.caption(f"🏢 광고주 page_id 추출: {pids} (브랜드 수집관리에서 매칭 가능)")
        st.session_state.pop("idsearch_collect_res", None)

    if not rows and not missing:
        st.info("입력한 ID가 모두 검색됐어요.")
        return

    for i in range(0, len(rows), 4):
        cols = st.columns(4)
        for col, ad in zip(cols, rows[i:i + 4]):
            with col:
                render_ad_card(ad, i)


def render_ad_grid(rows: list[dict], total: int, page: int, page_size: int) -> None:
    """rows 는 이미 잘린 현재 페이지(SQL LIMIT/OFFSET)."""
    if not rows:
        render_empty_state("조건에 맞는 광고가 없습니다")
        return
    total_pages = max(1, (total + page_size - 1) // page_size)
    for i in range(0, len(rows), 4):
        cols = st.columns(4)
        for col, ad in zip(cols, rows[i:i + 4]):
            with col:
                render_ad_card(ad, i)
    nav = st.columns([1, 1, 6, 1])
    if nav[0].button("◀ 이전", disabled=page <= 1, use_container_width=True):
        st.session_state.sa_page = page - 1
        st.rerun()
    nav[2].markdown(f"<div style='text-align:center;color:{S.SUB};font-size:13px;margin-top:6px'>"
                    f"페이지 {page} / {total_pages}</div>", unsafe_allow_html=True)
    if nav[1].button("다음 ▶", disabled=page >= total_pages, use_container_width=True):
        st.session_state.sa_page = page + 1
        st.rerun()


def render_add_youtube() -> None:
    """YouTube 영상 URL 수동 등록."""
    with st.expander("➕ YouTube 영상 등록"):
        if not YT.is_enabled():
            st.info("YOUTUBE_API_KEY를 등록하면 YouTube 조회수/좋아요/댓글 수집이 가능합니다.")
            return
        url = st.text_input("YouTube URL", key="yt_add_url",
                            placeholder="watch?v=... / shorts/... / youtu.be/...")
        brand = st.text_input("브랜드명(선택)", key="yt_add_brand", placeholder="예: 더스크랙")
        if st.button("등록", type="primary", key="yt_add_btn"):
            vid = YT.extract_video_id(url)
            if not vid:
                st.error("유효한 YouTube URL이 아닙니다.")
                return
            data = YT.fetch_video(vid)
            if not data:
                st.error("영상 정보를 가져오지 못했습니다.")
                return
            if brand.strip():
                data["brand_name"] = brand.strip()
            database.ingest_social_videos([data])          # 중복(video_id)이면 갱신
            database.add_snapshot(data["id"], data["views"], data["likes"],
                                  data["comments"], data["shares"])
            database.regrade()
            st.success(f"등록/갱신: {data['title'][:40]} ({_fmt(data['views'])}회)")
            _reload()


def _social_card(v: dict) -> None:
    plat = v.get("platform")
    thumb = v.get("thumbnail_url")
    grade = v.get("final_grade")
    inner = (f"<img src='{thumb}' loading='lazy' decoding='async'/>" if thumb
             else "<div class='sa-ph'>🎬</div>")
    badge = (f"<div class='sa-badge' style='background:{S.grade_color(grade)}'>{grade}급</div>"
             if grade and grade != "미분류" else "")
    rs = v.get("review_status") or "needs_review"
    rs_label = {"approved": "✅ 승인", "needs_review": "🔍 검토필요", "rejected": "🚫 제외"}.get(rs, rs)
    with st.container(border=True):
        st.markdown(
            f"<div class='sa-thumb'>{inner}{badge}<div class='sa-play'>▶</div>"
            f"<div class='sa-media'>{PLATFORM_LABEL.get(plat,'소셜')}</div></div>"
            f"<div class='sa-brand'>{v.get('brand_name','-')} "
            f"<span style='font-size:10px;color:{S.SUB}'>{rs_label}</span></div>"
            f"<div class='sa-title'>{(v.get('title') or v.get('caption') or '')[:42]}</div>"
            f"<div class='sa-copy'>{(v.get('channel_title') or '')[:40]}</div>"
            f"<div class='sa-meta'><span>👁 {_fmt(v.get('views'))} ❤ {_fmt(v.get('likes'))} "
            f"💬 {_fmt(v.get('comments'))}</span><span class='sa-pbadge'>소셜 원본</span></div>"
            f"<div class='sa-meta'><span>📅 {str(v.get('posted_at') or '-')[:10]}</span>"
            f"<span class='sa-pbadge'>{'📝 스크립트' if (v.get('script_status') in ('extracted','manual','exists')) else '스크립트 없음'}</span></div>",
            unsafe_allow_html=True)
        if st.button("상세 보기", key=f"sv_{v.get('id')}", use_container_width=True):
            render_social_detail(v)


def render_social_grid(vids: list[dict]) -> None:
    """소셜 원본 영상 그리드 — 조회수/좋아요/댓글은 원본 반응 지표(광고 성과 아님)."""
    st.markdown(f"**🎬 소셜 원본 영상** <span style='font-size:12px;color:{S.SUB}'>"
                f"· 조회수·좋아요·댓글은 원본 영상 반응 기준 (광고 성과 아님)</span>",
                unsafe_allow_html=True)
    render_add_youtube()

    fc = st.columns([2, 1.4])
    plats = ["전체", "TikTok", "Instagram", "YouTube"]
    pick = fc[0].segmented_control("플랫폼", plats, default="전체",
                                   label_visibility="collapsed", key="sv_plat") or "전체"
    incl_review = fc[1].toggle("검토 필요 포함", value=False, key="sv_review",
                               help="기본은 승인(approved)만. 켜면 검토필요도 표시. 제외(rejected)는 항상 숨김")
    pmap = {"TikTok": "tiktok", "Instagram": "instagram", "YouTube": "youtube"}

    def keep(v):
        if pick != "전체" and v.get("platform") != pmap.get(pick):
            return False
        rs = v.get("review_status") or "needs_review"
        if rs == "rejected":
            return False
        if rs == "needs_review" and not incl_review:
            return False
        return True

    rows = [v for v in vids if keep(v)]
    n_app = sum(1 for v in vids if (v.get("review_status") == "approved"))
    n_rev = sum(1 for v in vids if (v.get("review_status") == "needs_review"))
    st.caption(f"승인 {n_app} · 검토 필요 {n_rev} · 제외 {sum(1 for v in vids if v.get('review_status')=='rejected')}"
               f"  (브랜드 공식 계정/도메인/키워드 기반 매칭 점수로 분류)")

    if not rows:
        render_empty_state("표시할 소셜 영상이 없습니다")
        if n_rev and not incl_review:
            st.info(f"승인된 영상이 없습니다. '검토 필요 포함'을 켜면 {n_rev}건을 검토할 수 있습니다.")
        elif not YT.is_enabled():
            st.info("YouTube는 **YOUTUBE_API_KEY**, TikTok/IG는 **APIFY_TOKEN**을 등록하면 수집됩니다.")
        return
    for i in range(0, len(rows), 4):
        cols = st.columns(4)
        for col, v in zip(cols, rows[i:i + 4]):
            with col:
                _social_card(v)


YT_CLS = {
    "youtube_ad_matched": ("광고 확정", S.PRIMARY),
    "youtube_ad_candidate": ("광고 후보", "#F59E0B"),
    "not_matched": ("미매칭", S.SUB),
    "youtube_social_or_ppl": ("미매칭", S.SUB),   # 구버전 호환
}
YT_CONF = {"high": "확신 높음", "medium": "검토 필요", "low": "약함", "none": "근거 부족"}


def _run_yt_match(brand: str) -> None:
    """인라인 실행 — subprocess는 Streamlit secrets(YOUTUBE_API_KEY)를 못 읽으므로 직접 호출."""
    if not YT.is_enabled():
        st.error("YOUTUBE_API_KEY가 설정되지 않아 매칭할 수 없습니다. (배포 시 secrets에 등록 필요)")
        return
    import jobs.match_youtube_ads as MJ
    target = brand if brand and brand != "전체" else ""
    if target:
        brands = [target]
    else:
        conn = database.get_conn()
        brands = [r[0] for r in conn.execute(
            "SELECT DISTINCT brand_name FROM ad_library_ads WHERE brand_name<>''").fetchall()]
        conn.close()
    if not brands:
        st.warning("구글 광고를 가진 브랜드가 없습니다. 먼저 구글 투명성센터 광고를 수집하세요.")
        return
    before = sum(database.youtube_candidate_counts().values())
    tot = {"matched": 0, "candidate": 0, "ppl": 0}
    with st.spinner(f"{len(brands)}개 브랜드 YouTube 광고 매칭 중… (검색→유사도→분류)"):
        for b in brands:
            try:
                r = MJ.match_brand(b)
                for k in tot:
                    tot[k] += r.get(k, 0)
            except Exception as e:  # noqa: BLE001
                st.warning(f"{b}: {type(e).__name__} {e}")
    after = sum(database.youtube_candidate_counts().values())
    added = after - before
    st.success(f"매칭 완료 — 대상 {len(brands)}개 브랜드 · 광고확정 {tot['matched']} · "
               f"후보 {tot['candidate']} · 미매칭 {tot['ppl']} (신규 {max(added,0)}건)")
    if tot["matched"] + tot["candidate"] + tot["ppl"] == 0:
        st.info("후보 영상이 0건입니다. YouTube 검색 결과가 없거나 API 키/쿼터 문제일 수 있습니다.")


def _yt_match_card(c: dict) -> None:
    import json as _json
    status = c.get("match_status") or c.get("classification")
    label, color = YT_CLS.get(status, ("?", S.SUB))
    conf = YT_CONF.get(c.get("matching_confidence") or "", "")
    th = c.get("thumbnail_url") or ""
    dur = int(c.get("duration_sec") or 0)
    durtxt = f"{dur//60}:{dur%60:02d}" if dur else "-"
    cap = "· 자막" if c.get("has_caption") else ""
    try:
        why = _json.loads(c.get("matched_by") or "[]")
    except Exception:  # noqa: BLE001
        why = []
    try:
        sg = _json.loads(c.get("signals") or "{}")
    except Exception:  # noqa: BLE001
        sg = {}
    chan = c.get("source_account_name") or c.get("channel_title") or "-"
    legal = c.get("advertiser_legal_name") or "-"
    with st.container(border=True):
        if th:
            st.markdown(f"<div class='sa-thumb'><img src='{th}'/>"
                        f"<div class='sa-badge' style='background:{color}'>{int(c.get('matching_score') or 0)}</div>"
                        f"<div class='sa-media'>{durtxt}</div></div>", unsafe_allow_html=True)
        st.markdown(f"<div style='font-weight:700;font-size:12px;color:{color};margin-top:6px'>{label}"
                    f"<span style='color:{S.SUB};font-weight:500'> · 신뢰도 {conf}</span></div>"
                    f"<div class='sa-title'>{(c.get('title') or '(제목 없음)')[:46]}</div>"
                    f"<div class='sa-copy'>채널 {chan[:22]} · {str(c.get('published_at') or '')[:10]} {cap}</div>",
                    unsafe_allow_html=True)
        if why:
            st.caption("근거: " + " · ".join(why))
        # 왜 이 분류인지 — 5개 신호 상세(특히 후보 검토용)
        def _mk(ok):
            return ("✓" if ok else "·")
        cs = int((sg.get("copy_sim") or 0) * 100)
        ts = sg.get("thumb_sim")
        tstxt = f"{int(ts*100)}%" if ts is not None else "—"
        with st.expander("근거 상세", expanded=(status == "youtube_ad_candidate")):
            st.markdown(
                f"<div style='font-size:11.5px;line-height:1.7;color:{S.TEXT}'>"
                f"법인명: <b>{legal}</b> · 채널: <b>{chan}</b><br>"
                f"랜딩 URL 일치 <b>{_mk(sg.get('landing_hit'))}</b> · "
                f"브랜드/상품명 일치 <b>{_mk(sg.get('product_in_body'))}</b> · "
                f"채널명 일치 <b>{_mk(sg.get('channel_hit'))}</b><br>"
                f"문구 유사도 <b>{cs}%</b> · 썸네일 유사도 <b>{tstxt}</b> · "
                f"해시태그만 {_mk(sg.get('hashtag_only'))}</div>",
                unsafe_allow_html=True)
        if is_valid_external_url(c.get("source_url")):
            st.link_button("YouTube에서 보기", c["source_url"], use_container_width=True)


def render_youtube_ad_matches(brand: str, candidates: list, counts: dict) -> None:
    """YouTube '광고' 매칭 뷰 — 광고 데이터 기반 후보를 3분류해 표시(전체 수집과 분리)."""
    st.markdown(f"**YouTube 광고 매칭** <span style='font-size:12px;color:{S.SUB}'>"
                f"· 광고주·문구·썸네일·랜딩 기준으로 광고 영상만 가려냅니다 (제품명·해시태그만 일치는 광고로 보지 않음)</span>",
                unsafe_allow_html=True)
    run = st.columns([1.4, 3])
    btxt = brand if brand and brand != "전체" else "구글 광고 보유 브랜드 전체"
    if run[0].button(f"'{btxt}' 매칭 실행", use_container_width=True, type="primary"):
        _run_yt_match(brand)
        st.cache_data.clear()
        st.rerun()
    key_ok = YT.is_enabled()   # 키 '값'이 아니라 로딩 여부만 확인
    run[1].caption(f"YouTube API 키: {'로딩됨 ✓' if key_ok else '미설정 ✗ (Cloud Secrets에 YOUTUBE_API_KEY 등록 필요)'}"
                   "  ·  사이드바에서 브랜드를 고르면 그 브랜드만, '전체'면 구글 광고 보유 브랜드 전부 매칭")

    nm = counts.get("youtube_ad_matched", 0)
    nc = counts.get("youtube_ad_candidate", 0)
    npp = counts.get("not_matched", 0) + counts.get("youtube_social_or_ppl", 0)
    pick = st.segmented_control(
        "상태", [f"광고 확정 {nm}", f"광고 후보 {nc}", f"미매칭 {npp}", "전체"],
        default=f"광고 확정 {nm}", label_visibility="collapsed", key="ytm_cls")
    cmap = {f"광고 확정 {nm}": "youtube_ad_matched", f"광고 후보 {nc}": "youtube_ad_candidate",
            f"미매칭 {npp}": "not_matched"}
    want = cmap.get(pick or "")

    def _st(c):
        return c.get("match_status") or c.get("classification")
    if want == "not_matched":
        rows = [c for c in candidates if _st(c) in ("not_matched", "youtube_social_or_ppl")]
    elif want:
        rows = [c for c in candidates if _st(c) == want]
    else:
        rows = candidates

    if not rows:
        if not candidates:
            render_empty_state("아직 매칭 결과가 없습니다 — 위 '매칭 실행'을 눌러주세요")
            if not YT.is_enabled():
                st.info("YouTube 광고 매칭은 **YOUTUBE_API_KEY**가 필요합니다.")
        else:
            render_empty_state("이 분류에 해당하는 영상이 없습니다")
        return
    for i in range(0, len(rows), 4):
        cols = st.columns(4)
        for col, c in zip(cols, rows[i:i + 4]):
            with col:
                _yt_match_card(c)


@st.dialog("소셜 원본 영상", width="large")
def render_social_detail(v: dict) -> None:
    st.info("아래 지표는 **광고 성과가 아니라 원본 소셜 영상(YouTube/TikTok/IG) 반응 기준**입니다.")
    plat = v.get("platform")
    st.markdown(f"### {PLATFORM_LABEL.get(plat,'소셜')} · {v.get('brand_name','-')}")
    if v.get("title"):
        st.markdown(f"**{v['title']}**")
    if v.get("channel_title"):
        st.caption(f"채널: {v['channel_title']} · 게시일 {str(v.get('posted_at') or '-')[:10]}")

    # 브랜드 매칭 검증 + 수동 수정
    rs = v.get("review_status") or "needs_review"
    rs_label = {"approved": "✅ 승인", "needs_review": "🔍 검토필요", "rejected": "🚫 제외"}.get(rs, rs)
    sid = v.get("id")
    st.caption(f"**브랜드 매칭**: {rs_label} · 점수 {int(v.get('brand_match_score') or 0)} · "
               f"사유: {v.get('brand_match_reason') or '-'}")
    rc = st.columns(3)
    if rc[0].button("✅ 이 브랜드 맞음", key=f"appr_{sid}", use_container_width=True):
        database.update_review_status(sid, "approved")
        _reload()
    if rc[1].button("🚫 이 브랜드 아님", key=f"rej_{sid}", use_container_width=True):
        database.update_review_status(sid, "rejected")
        _reload()
    with rc[2].popover("↔ 다른 브랜드"):
        names = [r["name"] for r in database.brand_counts()]
        if names:
            nb = st.selectbox("브랜드 선택", names, key=f"mv_{sid}")
            if st.button("이동", key=f"mvb_{sid}", type="primary"):
                database.move_social_brand(sid, nb)
                _reload()
    st.divider()

    # 영상 재생: YouTube 는 embed iframe, 그 외는 video_url/source
    if plat == "youtube" and v.get("embed_url"):
        stc.html(
            f"<iframe width='100%' height='460' src='{v['embed_url']}' frameborder='0' "
            f"allow='accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; "
            f"picture-in-picture' allowfullscreen style='border-radius:10px'></iframe>", height=470)
    elif v.get("video_url"):
        st.video(v["video_url"])
    elif v.get("thumbnail_url"):
        st.image(v["thumbnail_url"], use_container_width=True)
    if is_valid_external_url(v.get("source_url")):
        st.link_button("▶ YouTube에서 보기 ↗" if plat == "youtube" else "▶ 원본 보기 ↗",
                       v["source_url"], use_container_width=True)

    # 영상 밑 스크립트(자막)
    render_script_section(v.get("id") or "", v.get("video_id") or "")

    m = st.columns(4)
    m[0].metric("조회수", _fmt(v.get("views")))
    m[1].metric("좋아요", _fmt(v.get("likes")))
    m[2].metric("댓글", _fmt(v.get("comments")))
    er = v.get("engagement_rate")
    m[3].metric("참여율", f"{er*100:.1f}%" if er else "-")
    fg, ag = v.get("final_grade"), v.get("absolute_grade")
    st.caption(f"등급: 최종 {fg or '-'}급 · 절대 {ag or '-'}급 · 참여 {v.get('engagement_level') or '-'} "
               f"· YouTube/소셜 원본 기준")

    # 최근 7일 추이
    st.markdown("##### 📈 최근 7일 추이")
    snaps = database.get_snapshots(v.get("id"), days=7)
    if len(snaps) < 2:
        st.caption("추이 데이터가 아직 부족합니다. 며칠 더 수집되면 그래프가 표시됩니다.")
    else:
        import pandas as pd
        df = pd.DataFrame(snaps).set_index("snapshot_date")[["views", "likes", "comments"]]
        st.line_chart(df)


def render_top(ads: list[dict]) -> None:
    """A급 이상 '터진' 소재 모아보기 (소셜 원본 기준)."""
    import services.grading as G
    st.markdown(f"**🔥 TOP — A급 이상 터진 소재** <span style='font-size:12px;color:{S.SUB}'>"
                f"· 소셜 원본 영상 반응 기준 (광고 성과 아님)</span>", unsafe_allow_html=True)
    top = [a for a in ads if G.GRADE_RANK.get(a.get("social_final_grade"), 0) >= G.GRADE_RANK["A"]]
    top.sort(key=lambda a: (G.GRADE_RANK.get(a.get("social_final_grade"), 0),
                            a.get("social_engagement_score") or 0,
                            int(a.get("social_views") or 0)), reverse=True)
    if not top:
        render_empty_state("아직 A급 이상 소재가 없습니다")
        st.info("소셜 원본 영상(TikTok/YouTube 등)이 매칭·등급화되면 여기에 모입니다. "
                "현재는 소셜 데이터가 없어 비어 있습니다.")
        return
    for i in range(0, len(top), 4):
        cols = st.columns(4)
        for col, ad in zip(cols, top[i:i + 4]):
            with col:
                render_ad_card(ad, i)


def render_insights() -> None:
    st.markdown("#### 📊 인사이트")
    s = database.insight_summary()
    if not s["total"]:
        render_empty_state("데이터가 없습니다")
        return
    c = st.columns(4)
    c[0].metric("총 광고", s["total"])
    c[1].metric("영상", s["videos"])
    c[2].metric("라이브", s["live"])
    c[3].metric("북마크", s["bm"])
    st.divider()
    counts = database.brand_counts()[:15]
    st.markdown("**브랜드별 광고 수 (상위 15)**")
    st.bar_chart({b: n for (b, n, _lv) in counts})
    st.markdown("**🔥 터진순 상위 10 (소셜 원본 기준)**")
    for a in database.load_ads_page("TOP", {}, 1, 10):
        g = a.get("social_final_grade") or "-"
        st.write(f"- `{g}급` **{a.get('brand_name')}** · "
                 f"{a.get('ad_title') or (a.get('ad_copy_short') or '')[:40]}")

    st.divider()
    st.markdown("#### 🩺 브랜드 진단 — 0건 브랜드 원인 분류")
    st.caption("사이드바 숫자는 **광고 + 소셜 승인(approved)** 기준. 데이터가 있어도 검토 필요(needs_review)면 0으로 보입니다.")
    diag = database.brand_diagnostics()
    import pandas as pd
    st.dataframe(pd.DataFrame(diag), use_container_width=True, hide_index=True,
                 column_config={"조치": st.column_config.TextColumn(width="large")})
    cause_ko = {"not_collected": "수집 미실행", "no_result": "결과 없음(검색어 부족)",
                "needs_review_only": "검토 필요만 있음(승인 전)", "rejected_only": "전부 무관 판정",
                "ok": "정상", "unknown": "확인 필요"}
    import collections as _c
    dist = _c.Counter(d["원인"] for d in diag)
    st.caption("원인 분포: " + " · ".join(f"{cause_ko.get(k,k)} {v}" for k, v in dist.most_common()))
