"""
내부 공유용 간단 로그인.
계정은 .streamlit/secrets.toml [auth.users] 에서 읽고, users 테이블(해시)로 검증한다.
로그인 상태는 st.session_state.authenticated 로 유지.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import time
from functools import lru_cache
from pathlib import Path

import streamlit as st

import database

_LOGIN_TTL = 6 * 3600   # 로그인 유지 6시간


def _auth_log(step: str, username: str = "", ok: bool = True, note: str = "") -> None:
    """로그인/접근 단계 로그 — 누가 어디서 막히는지 추적(logs/auth.log)."""
    try:
        from datetime import datetime
        d = Path(__file__).resolve().parent / "logs"
        d.mkdir(exist_ok=True)
        with open(d / "auth.log", "a", encoding="utf-8") as f:
            f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S}\t{step}\tuser={username or '-'}\t"
                    f"{'OK' if ok else 'FAIL'}\t{note}\n")
    except Exception:  # noqa: BLE001
        pass


def _sign_key() -> bytes:
    return (_secret("AUTH_SECRET") or "series-archive-2026-internal-signing").encode()


def _make_token(username: str) -> str:
    """서명된 만료 토큰(6시간) — URL 쿼리파라미터에 저장해 새로고침에도 유지."""
    exp = int(time.time()) + _LOGIN_TTL
    msg = f"{username}|{exp}"
    sig = hmac.new(_sign_key(), msg.encode(), hashlib.sha256).hexdigest()[:20]
    return base64.urlsafe_b64encode(f"{msg}|{sig}".encode()).decode()


def _check_token(token: str) -> str:
    """토큰 검증 → 유효하면 username, 아니면 ''."""
    try:
        raw = base64.urlsafe_b64decode(token.encode()).decode()
        username, exp, sig = raw.rsplit("|", 2)
        if int(exp) < time.time():
            return ""
        good = hmac.new(_sign_key(), f"{username}|{exp}".encode(), hashlib.sha256).hexdigest()[:20]
        return username if hmac.compare_digest(sig, good) else ""
    except Exception:  # noqa: BLE001
        return ""

_ASSETS = Path(__file__).resolve().parent / "assets"


@lru_cache(maxsize=1)
def _login_bg_uri() -> str:
    """전체 장면 이미지를 base64 data URI 로 — 가벼운 jpg 우선(메모리·로딩 절감)."""
    for name, mime in (("login_bg.jpg", "image/jpeg"), ("login_bg.png", "image/png"),
                       ("login_left.png", "image/png")):
        try:
            fp = _ASSETS / name
            if fp.exists():
                return f"data:{mime};base64," + base64.b64encode(fp.read_bytes()).decode()
        except Exception:  # noqa: BLE001
            pass
    return ""


def _secret(key: str) -> str:
    try:
        if key in st.secrets:
            return str(st.secrets[key])
    except Exception:  # noqa: BLE001
        pass
    return os.getenv(key, "")


def _flat_login() -> tuple[str, str, str]:
    """LOGIN_USERNAME / LOGIN_PASSWORD / LOGIN_PASSWORD_HASH (secrets→env)."""
    return _secret("LOGIN_USERNAME"), _secret("LOGIN_PASSWORD"), _secret("LOGIN_PASSWORD_HASH")


def get_secret_users() -> dict:
    """{username: plaintext_password}. [auth.users] + 평면 LOGIN_USERNAME/PASSWORD 병합."""
    users: dict = {}
    try:
        users.update({k: str(v) for k, v in dict(st.secrets["auth"]["users"]).items()})
    except Exception:  # noqa: BLE001
        pass
    u, pw, _h = _flat_login()
    if u and pw:
        users[u] = pw
    return users


def check_password(username: str, password: str) -> bool:
    if database.verify_user(username, password):   # DB(users, 해시) — init_db가 시드
        return True
    if get_secret_users().get(username) == password:   # 평문 폴백
        return True
    lu, _pw, lh = _flat_login()                    # LOGIN_PASSWORD_HASH 폴백
    return bool(lh) and username == lu and database.hash_pw(password) == lh


def login() -> None:
    """로그인 화면 — 이미지 없이 깔끔한 배경 + 가운데 정렬 카드 1개. (로직은 그대로)"""
    st.markdown("""
    <style>
      @import url('https://cdn.jsdelivr.net/gh/orioncactus/pretendard/dist/web/static/pretendard.css');
      html, body, [class*="css"], .stApp, input, button { font-family:'Pretendard',-apple-system,sans-serif; }
      header[data-testid="stHeader"] { background:transparent; }
      /* 깔끔한 연그린 그라데이션 배경(이미지 없음) */
      .stApp { background:linear-gradient(180deg,#F0FBF4 0%,#E3F6EC 55%,#D5F0E0 100%); }
      .block-container { padding-top:0 !important; max-width:1100px; }
      /* 가운데 로그인 카드 1개 (연회색 테두리) */
      div[data-testid="stForm"] {
        background:#FFFFFF; border:1px solid #E5E7EB !important;
        border-radius:34px; padding:30px 40px 36px;
        box-shadow:0 20px 50px rgba(16,24,40,.12);
        width:100%; max-width:440px; margin:0 auto; box-sizing:border-box;
      }
      div[data-testid="stForm"] label { font-weight:700; color:#334155; font-size:13px;
        margin-bottom:6px; }
      div[data-testid="stForm"] div[data-testid="stTextInput"] { margin-bottom:15px; }
      /* baseweb 기본 레이아웃(세로중앙·눈아이콘 내부배치)을 유지하고 '외형만' 입힘
         → 아이디=비밀번호 동일 크기, 텍스트 안 잘림, 눈 아이콘이 박스 크기 안 바꿈 */
      div[data-testid="stForm"] [data-baseweb="input"] {
        border-radius:18px !important; border:1px solid #E5E7EB !important;
        background:#FFFFFF !important; box-sizing:border-box; }
      div[data-testid="stForm"] [data-baseweb="input"]:focus-within {
        border-color:#03C75A !important; box-shadow:0 0 0 3px rgba(3,199,90,.16) !important; }
      /* ★ 컨테이너 내부 모든 요소(눈 아이콘 enhancer/button 포함)의 배경·그림자·테두리 제거
         → 아이콘 뒤 연한 초록 박스 완전 제거. 컨테이너 자체 배경/테두리/포커스글로우는 위에서 유지 */
      div[data-testid="stForm"] [data-baseweb="input"] *,
      div[data-testid="stForm"] [data-baseweb="input"] *:hover,
      div[data-testid="stForm"] [data-baseweb="input"] *:focus,
      div[data-testid="stForm"] [data-baseweb="input"] *:active,
      div[data-testid="stForm"] [data-baseweb="input"] *:focus-visible {
        background:transparent !important; background-color:transparent !important;
        box-shadow:none !important; outline:none !important; border:none !important; }
      div[data-testid="stForm"] [data-baseweb="input"] input { font-size:14.5px !important; }
      /* 눈 아이콘: 세로 중앙으로(너무 아래 X) */
      div[data-testid="stForm"] [data-baseweb="input"] button {
        border-radius:0 !important; color:#64748B !important;
        display:flex !important; align-items:center !important; justify-content:center !important;
        align-self:center !important; height:auto !important; padding:0 6px !important; }
      div[data-testid="stForm"] button {
        background:linear-gradient(135deg,#34D399 0%,#03C75A 100%) !important;
        color:#fff !important; border:none !important; border-radius:26px !important;
        height:54px; font-weight:800 !important; font-size:15.5px; margin-top:6px;
        box-shadow:0 12px 26px rgba(3,199,90,.40) !important; }
      div[data-testid="stForm"] button:hover { filter:brightness(1.05); }
      a, .stMarkdown a { color:#03C75A !important; }
    </style>
    """, unsafe_allow_html=True)

    # 화면 가운데 정렬
    _, mid, _ = st.columns([1, 1.3, 1])
    with mid:
        st.markdown("<div style='height:23vh'></div>", unsafe_allow_html=True)
        with st.form("login", border=True):
            # 맥북 느낌 신호등 3개
            st.markdown(
                "<div style='display:flex;gap:8px;margin-bottom:18px'>"
                "<span style='display:inline-block;width:12px;height:12px;border-radius:50%;background:#FF5F57'></span>"
                "<span style='display:inline-block;width:12px;height:12px;border-radius:50%;background:#FEBC2E'></span>"
                "<span style='display:inline-block;width:12px;height:12px;border-radius:50%;background:#28C840'></span>"
                "</div>", unsafe_allow_html=True)
            st.markdown(
                "<div style='text-align:center;font-size:38px;font-weight:900;color:#03C75A;"
                "letter-spacing:-1px;line-height:1.1;margin-bottom:24px'>Repurely</div>",
                unsafe_allow_html=True)
            u = st.text_input("아이디", placeholder="아이디")
            p = st.text_input("비밀번호", type="password", placeholder="비밀번호")
            if st.form_submit_button("로그인", use_container_width=True, type="primary"):
                if check_password(u.strip(), p):
                    st.session_state.authenticated = True
                    st.session_state.username = u.strip()
                    _auth_log("login", u.strip(), True)
                    try:   # 6시간 유지 토큰을 URL에 저장(새로고침에도 로그인 유지)
                        st.query_params["auth"] = _make_token(u.strip())
                    except Exception:  # noqa: BLE001
                        pass
                    st.rerun()
                else:
                    _known = ", ".join(sorted(get_secret_users().keys())) or "(없음)"
                    _auth_log("login", u.strip(), False, f"등록계정={_known}")
                    st.error("아이디 또는 비밀번호가 올바르지 않습니다.")
            st.markdown("<div style='text-align:center;color:#94A3B8;font-size:12px;margin-top:14px'>"
                        "내부 공유용 · 계정 문의는 관리자에게</div>", unsafe_allow_html=True)


def logout() -> None:
    for k in ("authenticated", "username"):
        st.session_state.pop(k, None)
    try:
        del st.query_params["auth"]   # 유지 토큰 제거
    except Exception:  # noqa: BLE001
        pass
    st.rerun()


def require_login() -> bool:
    if st.session_state.get("authenticated"):
        return True
    # 새로고침 시 세션이 풀려도, URL의 서명 토큰(6시간)으로 자동 재로그인
    try:
        tok = st.query_params.get("auth")
    except Exception:  # noqa: BLE001
        tok = None
    if tok:
        u = _check_token(tok)
        if u:
            st.session_state.authenticated = True
            st.session_state.username = u
            return True
    return False
