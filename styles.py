"""Series Archive custom CSS — 그린/민트 SaaS 룩."""
import streamlit as st

# 팔레트 — 라이트 SaaS 대시보드 톤(텍스트는 그레이, 액센트는 그린 유지)
PRIMARY = "#03C75A"      # 브랜드명/링크/선택 탭 — 그린(Naver)
PRIMARY_HOVER = "#02A94C"  # 링크 hover(진한 그린)
DEEP = "#10B981"         # 진한 그린(선택 텍스트)
MINT = "#2DD4BF"
SOFT_MINT = "#ECFDF5"    # 연한 그린 배경(선택/칩)
BG = "#F8FAFC"
CARD = "#FFFFFF"
TEXT = "#111827"         # 기본 본문
SUB = "#6B7280"          # 보조 텍스트
BORDER = "#E5E7EB"
END_RED = "#EF4444"      # 종료 상태
OFF_GRAY = "#9CA3AF"     # 흐린 설명 텍스트
LIVE = "#10B981"         # 라이브 상태(그린)
WARN = "#F59E0B"         # 경고/주의


def status_color(status: str) -> str:
    return {"live": LIVE, "ended": END_RED, "inactive": OFF_GRAY}.get(status, OFF_GRAY)


def score_color(s: int) -> str:
    return DEEP if s >= 80 else PRIMARY if s >= 60 else OFF_GRAY


def grade_color(g: str) -> str:
    return {"S": "#0F766E", "A": "#10B981", "B": "#0EA5A4", "C": "#94A3B8"}.get(g, "#CBD5E1")


def inject_css() -> None:
    st.markdown(f"""
    <style>
      @import url('https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/static/pretendard.css');
      html, body, [class*="css"], .stApp, button, input, select, textarea {{
          font-family: Pretendard, -apple-system, BlinkMacSystemFont, system-ui, "Segoe UI",
                       "Malgun Gothic", sans-serif !important; }}
      /* 메인 폭 제한 + 여백(밀도 ↑) */
      .block-container {{padding: 0.2rem 1.8rem 2rem !important; max-width: 1480px;}}
      [data-testid="stHorizontalBlock"] {{gap: 0.7rem;}}
      [data-testid="stVerticalBlock"] {{gap: 0.45rem;}}
      ::-webkit-scrollbar {{width: 7px; height: 7px;}}
      ::-webkit-scrollbar-thumb {{background: #CBD5E1; border-radius: 8px;}}
      ::-webkit-scrollbar-track {{background: transparent;}}

      /* 헤더 통합검색 input(낮게) */
      .stTextInput input {{height: 38px; border-radius: 10px !important; border:1px solid {BORDER};
          background:#fff; font-size:13.5px;}}
      .stTextInput input:focus {{border-color:{PRIMARY}; box-shadow:0 0 0 3px {SOFT_MINT};}}

      /* selectbox / multiselect — 낮고 컴팩트하게 */
      div[data-baseweb="select"] > div {{border-radius:10px !important; border-color:{BORDER} !important;
          min-height:38px; background:#fff; font-size:13px;}}
      .stSelectbox label, .stMultiSelect label {{font-size:12px !important; color:{SUB};
          font-weight:600; margin-bottom:1px !important;}}
      div[data-testid="stCheckbox"] label {{font-size:11.5px; color:{OFF_GRAY};}}

      /* 메인 내비 탭 — 앱 주요 내비게이션 pill(실제 DOM: stButtonGroup + stBaseButton-segmented_control) */
      div[data-testid="stButtonGroup"] {{gap:8px !important;}}
      button[data-testid^="stBaseButton-segmented_control"] {{border-radius:11px !important;
          font-size:14px !important; font-weight:600; border:1px solid {BORDER} !important;
          padding:10px 18px !important; min-height:40px; box-sizing:border-box; line-height:1;
          background:#fff !important; color:{SUB} !important; transition:all .12s;}}
      button[data-testid^="stBaseButton-segmented_control"]:hover {{
          border-color:{PRIMARY} !important; color:{TEXT} !important;}}
      button[data-testid="stBaseButton-segmented_controlActive"] {{
          background:{SOFT_MINT} !important; color:{PRIMARY} !important;
          border-color:{PRIMARY} !important; font-weight:800 !important;}}
      #MainMenu, footer {{visibility:hidden;}}
      /* header(상단 툴바)는 숨기지 않음 — 사이드바 펼치는 '>' 버튼이 여기 들어있다.
         배경만 투명 처리해 깔끔하게, 펼침/접기 컨트롤은 항상 보이도록 강제. */
      /* 헤더 전체를 클릭 통과시켜 상단 탭(메인 영역)이 헤더 빈 영역에 가려도 눌리게 한다.
         toolbar까지 전부 통과(none) → 탭 클릭 보장. 단 펼치기 버튼만 예외로 다시 활성화. */
      header[data-testid="stHeader"] {{background:transparent; pointer-events:none;}}
      header[data-testid="stHeader"] [data-testid="stToolbar"],
      header[data-testid="stHeader"] [data-testid="stToolbar"] * {{pointer-events:none !important;}}
      /* 펼치기(>>) 버튼만 재활성화 — toolbar none보다 높은 specificity로 이기게(toolbar 내/외 모두 매칭) */
      header[data-testid="stHeader"] [data-testid="stToolbar"] [data-testid="stExpandSidebarButton"],
      header[data-testid="stHeader"] [data-testid="stToolbar"] [data-testid="stExpandSidebarButton"] *,
      header[data-testid="stHeader"] [data-testid="stExpandSidebarButton"],
      header[data-testid="stHeader"] [data-testid="stExpandSidebarButton"] * {{
          pointer-events:auto !important;}}
      /* 펼치기(>>)/접기(<<) 컨트롤 — 항상 보이고, 상단 탭 위로 올려 클릭 보장 */
      [data-testid="stExpandSidebarButton"],
      [data-testid="stSidebarCollapseButton"],
      [data-testid="stSidebarCollapsedControl"],
      [data-testid="collapsedControl"] {{
          visibility:visible !important; display:flex !important; opacity:1 !important;
          position:relative !important; pointer-events:auto !important; z-index:2147483000 !important;
          min-width:2.4rem !important; min-height:2.4rem !important;}}
      [data-testid="stExpandSidebarButton"] *,
      [data-testid="stSidebarCollapseButton"] *,
      [data-testid="stSidebarCollapsedControl"] *,
      [data-testid="collapsedControl"] * {{pointer-events:auto !important;}}
      .stApp {{background:{BG};}}
      .block-container {{padding-top:0.2rem; padding-bottom:2.5rem; max-width:1560px;}}

      /* ── 헤더 ── */
      .sa-header {{display:flex; align-items:center; gap:12px;}}
      .sa-logo {{font-size:21px; font-weight:800; color:{PRIMARY}; line-height:1.25;
                 white-space:nowrap; margin:0;}}
      .sa-sub {{font-size:12px; color:{SUB}; line-height:1.3; margin:0;}}
      .sa-info {{color:{SUB}; font-size:12.5px; margin:.2rem 0 .6rem;}}
      .sa-info b {{color:{TEXT};}}

      /* ── 칩(적용된 필터 요약) ── */
      .sa-chip {{display:inline-block; background:#F1F5F9; color:{SUB}; border:1px solid {BORDER};
                 font-size:11.5px; font-weight:600; padding:2px 10px; border-radius:999px; margin:0 5px 6px 0;}}
      .sa-chip b {{color:{TEXT}; font-weight:700;}}

      /* ── 카드 ── */
      div[data-testid="stVerticalBlockBorderWrapper"] {{
          background:{CARD}; border:1px solid #EEF1F6 !important; border-radius:16px;
          box-shadow:0 1px 2px rgba(16,24,40,.03); transition:transform .16s, box-shadow .16s, border-color .16s;}}
      div[data-testid="stVerticalBlockBorderWrapper"]:hover {{
          transform:translateY(-2px); box-shadow:0 10px 26px rgba(16,24,40,.09);
          border-color:{BORDER} !important;}}
      /* 필터 toolbar 카드(소재유형 multiselect 포함)는 hover lift 제거 */
      div[data-testid="stVerticalBlockBorderWrapper"]:has(div[data-testid="stMultiSelect"]):hover {{
          transform:none; box-shadow:0 1px 2px rgba(16,24,40,.03);}}
      /* 썸네일: 16:9 통일 + 영상 전체가 보이도록 contain + 뒤에 블러 배경(레터박스/세로영상 자연스럽게) */
      .sa-thumb {{position:relative; aspect-ratio:16/9; border-radius:12px; overflow:hidden;
                  background:#0F172A; background-size:cover; background-position:center;}}
      .sa-thumb::before {{content:''; position:absolute; inset:0; background:inherit;
                  background-size:cover; background-position:center;
                  filter:blur(22px) brightness(.5); transform:scale(1.3);}}
      .sa-thumb img {{position:relative; z-index:1; width:100%; height:100%; object-fit:contain;}}
      /* data URI 썸네일(구글 스크린샷 등)은 블러배경 없이 꽉 채움(용량 절감) */
      .sa-thumb-fill::before {{display:none;}}
      .sa-thumb-fill img {{object-fit:cover;}}
      /* 구글 탭: 원본 비율 유지(자르지 않음) — 흰 배경, 블러 없음 */
      .sa-thumb-contain {{background:#F8FAFC !important;}}
      .sa-thumb-contain::before {{display:none;}}
      .sa-thumb-contain img {{object-fit:contain;}}
      /* 메타 탭: 세로형 숏폼·클로즈업·자막이 잘 보이게 3:4(세로 긴) cover */
      .sa-thumb-meta {{aspect-ratio:3/4 !important;}}
      .sa-thumb-meta::before {{display:none;}}
      .sa-thumb-meta img {{object-fit:cover; object-position:center;}}
      /* repurely(Insight): Meta 탭과 같은 3:4 박스 크기, 단 영상 썸네일이라 contain
         (cover로 꽉 채우면 좌우가 잘려 확대·흐림) + 어두운 여백 */
      .sa-thumb-rep {{aspect-ratio:3/4 !important; background:#0F172A !important;}}
      .sa-thumb-rep::before {{display:none;}}
      .sa-thumb-rep img {{object-fit:contain;}}
      .sa-badge {{position:absolute; top:8px; left:8px; color:#fff; font-weight:700; font-size:11px;
                  padding:2px 7px; border-radius:999px; box-shadow:0 1px 4px rgba(0,0,0,.2); z-index:2;}}
      .sa-dot {{position:absolute; top:9px; right:9px; width:9px; height:9px; border-radius:50%;
                box-shadow:0 0 0 2px #fff; z-index:2;}}
      .sa-media {{position:absolute; bottom:7px; left:7px; background:rgba(15,23,42,.6); color:#fff;
                  font-size:10px; font-weight:600; padding:1px 7px; border-radius:6px; z-index:2;
                  backdrop-filter:blur(2px);}}
      .sa-play {{position:absolute; top:50%; left:50%; transform:translate(-50%,-50%); font-size:34px;
                 color:#fff; opacity:.92; text-shadow:0 2px 10px rgba(0,0,0,.55); z-index:2;}}
      .sa-thumb-empty {{background:{BG} !important; border:1px dashed {BORDER};}}
      .sa-thumb-empty::before {{display:none;}}
      .sa-ph {{position:absolute; top:50%; left:50%; transform:translate(-50%,-50%);
               text-align:center; color:{OFF_GRAY}; font-size:13px; font-weight:600; z-index:1;}}
      .sa-ph .i {{display:block; font-size:30px; margin-bottom:2px; opacity:.7;}}
      .sa-brand {{font-weight:800; font-size:15px; color:{PRIMARY}; margin-top:9px;
                  white-space:nowrap; overflow:hidden; text-overflow:ellipsis; letter-spacing:-.2px;}}
      .sa-title {{font-size:14px; color:{TEXT}; font-weight:600; margin-top:1px; line-height:1.25;
                  white-space:nowrap; overflow:hidden; text-overflow:ellipsis;}}
      .sa-copy {{font-size:12px; color:{SUB}; line-height:1.3; margin-top:2px;
                 display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden;}}
      .sa-meta {{font-size:12.5px; color:{SUB}; margin-top:6px; display:flex;
                 justify-content:space-between; align-items:center;}}
      .sa-meta .v {{font-weight:700; color:{SUB};}}
      .sa-pbadge {{font-size:11px; font-weight:600; color:{SUB}; background:{BG};
                   border:1px solid {BORDER}; padding:1px 7px; border-radius:6px;}}
      .sa-mchip {{font-size:11px; font-weight:600; color:{SUB}; background:#F1F5F9;
                  padding:1px 7px; border-radius:6px;}}
      .sa-live {{font-size:11.5px; font-weight:700;}}
      .sa-date {{font-size:11.5px; color:{OFF_GRAY};}}

      /* ── 버튼 ── */
      div[data-testid="stButton"] button {{border-radius:9px; font-size:13px; border:1px solid {BORDER};
          color:{TEXT}; background:{CARD};}}
      div[data-testid="stButton"] button:hover {{border-color:{PRIMARY}; color:{PRIMARY};}}
      div[data-testid="stButton"] button[kind="primary"] {{background:{PRIMARY}; border-color:{PRIMARY}; color:#fff;}}
      /* 링크 — 블루 + hover 진한 블루 */
      a, .stMarkdown a, [data-testid="stMarkdownContainer"] a {{color:{PRIMARY} !important;
          text-decoration:none;}}
      a:hover, .stMarkdown a:hover {{color:{PRIMARY_HOVER} !important; text-decoration:underline;}}
      /* 카드 내부 액션 버튼: 작게 + 기본 은은 → 카드 hover 시 또렷 */
      div[data-testid="stVerticalBlockBorderWrapper"] div[data-testid="stButton"] button {{
          font-size:12px; padding:5px 8px; min-height:0; opacity:.8;
          transition:opacity .16s, border-color .16s, color .16s;}}
      div[data-testid="stVerticalBlockBorderWrapper"]:hover div[data-testid="stButton"] button {{opacity:1;}}

      /* ── 사이드바(흰 카드) ── */
      section[data-testid="stSidebar"] {{background:{CARD}; border-right:1px solid {BORDER};
          width:320px !important; min-width:320px !important;}}
      section[data-testid="stSidebar"] > div {{width:320px !important;}}
      section[data-testid="stSidebar"] .block-container {{padding:0 18px 1rem !important;}}
      /* 사이드바 상단 << 접기 버튼 행 — 컴팩트하게(클릭 유지), 단 겹치지 않게 공간만 최소 확보 */
      section[data-testid="stSidebar"] [data-testid="stSidebarHeader"] {{
          min-height:0 !important; height:auto !important; padding:.25rem .4rem .1rem !important;}}
      section[data-testid="stSidebar"] [data-testid="stSidebarUserContent"] {{padding-top:0 !important;}}
      section[data-testid="stSidebar"] [data-testid="stSidebarContent"] > div:first-child {{
          padding-top:0 !important;}}
      /* Series Archive — << 버튼 옆/위로 끌어올린 sticky 헤더(<<는 최상위 z-index라 클릭 유지).
         좌측 패딩으로 << 버튼을 피해 텍스트가 그 오른쪽에서 시작. 구분선 없음·리스트만 스크롤 */
      section[data-testid="stSidebar"] [data-testid="stElementContainer"]:has(.sa-sb-head) {{
          position:sticky; top:0; z-index:4; background:{CARD};
          margin:-.35rem 0 0 !important; padding:.1rem 0 .45rem 0 !important;}}
      .sa-sb-head {{margin:0; padding-top:0;}}
      section[data-testid="stSidebar"] .sa-logo {{font-size:19px;}}
      section[data-testid="stSidebar"] [data-testid="stVerticalBlock"] {{gap:.4rem;}}
      /* 브랜드 row: 이름 왼쪽 · 개수 오른쪽(양끝 정렬) */
      section[data-testid="stSidebar"] div[data-testid="stButton"] button {{
          width:100%; background:transparent; border:none; border-left:3px solid transparent; color:{TEXT};
          font-size:13px; padding:8px 10px; min-height:0; line-height:1.3; border-radius:8px;
          font-weight:500;}}
      section[data-testid="stSidebar"] div[data-testid="stButton"] button p {{
          display:flex; justify-content:space-between; align-items:center; width:100%; margin:0;
          gap:8px; color:#475569; font-weight:600;}}
      section[data-testid="stSidebar"] div[data-testid="stButton"] button:hover {{
          background:#F1F5F9; color:{TEXT};}}
      /* 선택된 브랜드(primary) — 은은한 민트 배경 + 초록 좌측 액센트 */
      section[data-testid="stSidebar"] div[data-testid="stButton"] button[kind="primary"] {{
          background:{SOFT_MINT} !important; color:{DEEP} !important; font-weight:700;
          border-left:3px solid {PRIMARY} !important;}}
      section[data-testid="stSidebar"] div[data-testid="stButton"] button[kind="primary"] p {{color:{DEEP};}}
      /* 브랜드 검색창 강조 */
      section[data-testid="stSidebar"] .stTextInput input {{height:42px; background:{BG};
          border:1px solid {BORDER}; font-size:13.5px; border-radius:10px;}}
      section[data-testid="stSidebar"] .stTextInput input:focus {{background:#fff;
          border-color:{PRIMARY}; box-shadow:0 0 0 3px {SOFT_MINT};}}
      section[data-testid="stSidebar"] hr {{margin:.45rem 0 1.5rem !important;}}
      section[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p {{margin:0 !important;}}
      section[data-testid="stSidebar"] [data-testid="stExpander"] {{margin:.15rem 0;}}
      section[data-testid="stSidebar"] [data-testid="stExpander"] summary {{padding:.3rem .6rem;}}
      section[data-testid="stSidebar"] [data-testid="stElementContainer"] {{margin-bottom:0 !important;}}

      /* 탭(segmented) 라운드 */
    </style>
    """, unsafe_allow_html=True)


def block_hotkeys() -> None:
    """Streamlit 기본 단축키(C=clear cache, R=rerun) 차단 — 소재명 등 텍스트를 복사할 때
    'Clear cache?' 팝업이 뜨던 오작동 방지. 복사(Ctrl+C)는 copy 이벤트라 영향 없음."""
    import streamlit.components.v1 as _stc
    _stc.html("""
    <script>
    try {
      const doc = window.parent.document;
      if (!doc._saHotkeyBlock) {
        doc._saHotkeyBlock = true;
        const block = function(e){
          const t = e.target;
          const typing = t && (t.tagName==='INPUT' || t.tagName==='TEXTAREA' || t.isContentEditable);
          if (!typing && (e.key==='c' || e.key==='C' || e.key==='r' || e.key==='R')) {
            e.stopImmediatePropagation();   // Streamlit 핫키만 차단(복사·기본동작 유지)
          }
        };
        ['keydown','keypress','keyup'].forEach(function(ev){ doc.addEventListener(ev, block, true); });
      }
    } catch (err) { /* iframe sandbox 등으로 부모 접근 불가 시 무시 */ }
    </script>
    """, height=0)
