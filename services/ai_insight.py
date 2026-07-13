"""repurely 내부 소재 AI 분석 — 실무형 크리에이티브 리포트.

성과 데이터(CTR/CPC/CPM/ROAS/광고비/매출/구매) + (있으면) 영상 스크립트를 함께 보고
"왜 됐는지 / 왜 안 됐는지 / 유지·수정·OFF / 다음 소재"까지 판단하는 섹션형 리포트(dict)를 반환.

ROAS는 퍼센트(100%=본전). 평균은 repurely 매체별 전체 소재 평균(누적 합계 기준).
GEMINI_API_KEY 있으면 Gemini, 없거나 실패 시 규칙 기반(키 없이 동작). 읽기 전용.
"""
from __future__ import annotations

import json

import config

# (key, 표시 제목) — UI 섹션 카드 순서
FIELDS = [
    ("summary", "🩺 종합 진단"),
    ("perf_vs_avg", "📈 평균 대비 성과 해석"),
    ("hook", "🎣 후킹 분석 (첫 화면·썸네일·카피)"),
    ("script_analysis", "🎬 영상 스크립트·대본 분석"),
    ("segment", "⏱ 구간별 장면 분석"),
    ("conversion", "🛒 전환 설득 구조 분석"),
    ("fatigue", "😮‍💨 소재 피로도 가능성"),
    ("improvements", "🛠 개선 카피 제안"),
    ("remake", "♻️ 응용 소재 방향"),
    ("next_tests", "🧪 다음 테스트 소재 아이디어"),
    ("producer_memo", "✍️ 제작자 전달 기획 메모"),
]


def enabled() -> bool:
    return bool(config.GEMINI_API_KEY)


def _num(r: dict, k: str, d: float = 0.0) -> float:
    try:
        return float(r.get(k, d) or 0)
    except Exception:  # noqa: BLE001
        return d


def verdict_label(r: dict, av: dict | None = None) -> tuple[str, str]:
    """ROAS(%) 기준 최종 판단 라벨 + 색상(100%=본전). 매체별 기준 차등:
    Meta 등 200/150/100, TikTok은 상단 퍼널(인지) 특성상 낮게 150/100/70 — ROAS 70%+면 효율 괜찮음."""
    roas = _num(r, "roas")
    ctr = _num(r, "ctr")
    a_ctr = (av or {}).get("ctr", 0) or 0
    very_hi_ctr = bool(a_ctr and ctr >= a_ctr * 1.1)
    if r.get("platform") == "TikTok":
        if r.get("is_fatigue") and roas < 100:
            return ("피로도 의심", "#F59E0B")
        if roas >= 150:
            return ("위닝 소재", "#10B981")
        if roas >= 100:
            return ("유지 소재", "#0EA5E9")
        if roas >= 70:                       # TikTok은 ROAS 70%+면 효율 괜찮음 — OFF 아님
            return ("개선 필요", "#F59E0B")
        if very_hi_ctr:
            return ("수정 후 재테스트", "#F59E0B")
        return ("OFF 권장", "#EF4444")
    # Meta 등 기본 기준
    if r.get("is_fatigue") and roas < 150:
        return ("피로도 의심", "#F59E0B")
    if roas >= 200:
        return ("위닝 소재", "#10B981")
    if roas >= 150:
        return ("유지 소재", "#0EA5E9")
    if roas >= 100:
        return ("개선 필요", "#F59E0B")
    if very_hi_ctr:
        return ("수정 후 재테스트", "#F59E0B")
    return ("OFF 권장", "#EF4444")


def _ctx(r: dict) -> str:
    base = (
        f"매체: {r.get('platform','')}\n"
        f"소재명: {r.get('creative_name','')}\n"
        f"캠페인: {r.get('campaign_name','')}\n"
        f"광고비: {int(_num(r,'spend')):,}원 · 매출: {int(_num(r,'revenue')):,}원 · "
        f"구매: {int(_num(r,'conversions'))}건\n"
        f"ROAS: {_num(r,'roas'):.0f}% · CTR: {_num(r,'ctr')}% · "
        f"CPC: {int(_num(r,'cpc')):,}원 · CPM: {int(_num(r,'cpm')):,}원"
    )
    tt = r.get("tt_metrics") or {}
    if tt:
        pv = tt.get("video_play_actions", 0) or 0
        comp = f"{(tt.get('video_views_p100',0) or 0)/pv*100:.0f}%" if pv else "-"
        base += (f"\nTikTok 영상지표: 재생 {int(pv):,} · 2초조회 {int(tt.get('video_watched_2s',0)):,} · "
                 f"6초조회 {int(tt.get('video_watched_6s',0)):,} · 완료율(100%) {comp} · "
                 f"평균시청 {tt.get('average_video_play',0):.0f}초 · "
                 f"좋아요 {int(tt.get('likes',0))} · 댓글 {int(tt.get('comments',0))} · 공유 {int(tt.get('shares',0))}"
                 f"\n(완료율·평균시청시간으로 영상 이탈 구간을 추정하라 — 2초조회 대비 6초·완료율이 급락하면 그 구간이 이탈점)")
    return base


def _avg_block(av_meta: dict | None) -> str:
    if not av_meta:
        return "(평균 기준 데이터 없음)"
    a = av_meta
    return (f"기준: {a.get('source','repurely 전체 소재 평균')}\n"
            f"평균 ROAS {a.get('roas',0):.0f}% · 평균 CTR {a.get('ctr',0):.2f}% · "
            f"평균 CPC {int(a.get('cpc',0)):,}원 · 평균 CPM {int(a.get('cpm',0)):,}원\n"
            f"비교 대상: 선택 소재의 {a.get('basis','누적 합계')} 성과")


def _peers_ctx(peers: list) -> str:
    if not peers:
        return "(같은 매체 위너 소재 없음)"
    out = []
    for p in peers[:5]:
        out.append(f"- {p.get('creative_name','?')}: ROAS {_num(p,'roas'):.0f}% · "
                   f"CTR {_num(p,'ctr')}% · CPC {int(_num(p,'cpc')):,}원 · "
                   f"구매 {int(_num(p,'conversions'))}건")
    return "\n".join(out)


_PROMPT = (
    "너는 시니어 퍼포먼스 크리에이티브 전략가다. repurely(자사)가 운영 중인 광고 소재 1건을 "
    "성과 데이터 + 영상 내용까지 함께 보고 실무형 리포트를 작성하라. "
    "광고 운영자가 바로 유지/수정/OFF/다음 소재를 판단할 수 있게 구체적으로. "
    "'좋은 소재입니다', '성과가 평균 이상입니다' 같은 모호한 표현 금지.\n\n"
    "[ROAS 내부 기준 — 반드시 따를 것] (ROAS는 %, 100%=본전, 매체별 기준이 다름)\n"
    "- Meta 등: 200%+ 위닝 / 150~200 유지 / 100~150 개선 / 100% 미만 OFF·리메이크.\n"
    "- TikTok: 상단 퍼널(인지) 특성상 기준이 낮음 — 150%+ 위닝 / 100~150 유지 / 70~100 효율 괜찮음(개선 여지) / "
    "70% 미만 OFF·리메이크. TikTok은 ROAS 70%+면 섣불리 OFF하지 말 것.\n"
    "- 매체에 맞는 기준으로 판단하고, CTR이 평균보다 매우 높은데 ROAS가 기준 미달이면 "
    "'후킹은 유지하고 전환 구조를 수정한 리메이크'로 제안.\n\n"
    "[성과 조합별 해석]\n"
    "- CTR↑·ROAS↑: 후킹과 전환 설득이 모두 작동(위닝).\n"
    "- CTR↑·ROAS↓: 클릭은 만들지만 클릭 후 구매 설득(후기·수치·혜택·CTA)이 약함.\n"
    "- CTR↓·ROAS↑: 클릭 규모는 작지만 구매 의도 높은 타깃엔 설득력 있음.\n"
    "- CTR↓·ROAS↓: 후킹·전환 모두 약함 → OFF 우선.\n"
    "- CPM↑·CTR↓: 노출 경쟁 대비 반응 약해 비효율 가능성.\n"
    "- 광고비↑·ROAS↓: 예산 낭비 가능 → 즉시 점검.\n"
    "- CPC가 평균보다 낮으면 클릭 유도 효율이 좋은 이유를 짚고, CPM이 높으면 경쟁/피로도/타깃 중 무엇인지 나눠 분석.\n\n"
    "[영상이면] 아래 구간으로 나눠 분석하라(스크립트/자막 제공 시 그 내용 기반, 없으면 카피·썸네일로 추론):\n"
    "- 0~3초(첫 후킹) / 3~7초(문제 제기·공감) / 7~15초(제품·해결책) / 15초+ (구매 설득·후기·CTA).\n"
    "각 구간: 전달 메시지 / 계속 보게 만드는 요소 / 이탈 가능 구간 / 후킹 강·약 / 제품 설득 충분 여부 / "
    "CTA 자연스러움 / 어떤 문장·장면을 바꾸면 좋을지.\n\n"
    "반드시 아래 키의 JSON 객체로만 출력(다른 텍스트 금지). summary는 3~4문장 평문, 나머지는 마크다운 불릿('- '):\n"
    "{{\n"
    '  "summary": "종합 진단 — CTR/ROAS 조합 기반으로 이 소재가 위닝인지/리메이크인지/OFF인지와 핵심 이유 3~4문장",\n'
    '  "perf_vs_avg": "각 지표를 제시된 평균과 비교한 해석 + 조합별 해석 불릿",\n'
    '  "hook": "첫 화면/썸네일/첫 문장/문제 제기가 클릭을 유도했는지(CTR 근거) 불릿",\n'
    '  "script_analysis": "자막/대사/장면 흐름과 문제제기→공감→해결→제품→CTA 구조 진단 불릿",\n'
    '  "segment": "0~3초/3~7초/7~15초/15초+ 구간별 메시지·이탈 가능 구간·개선 불릿",\n'
    '  "conversion": "클릭 후 구매 설득 구조(후기·수치·혜택·가격·신뢰)가 충분한지 ROAS 근거로 불릿",\n'
    '  "fatigue": "피로도 가능성 — CPM 상승만으로 단정 금지. CTR 하락+CPC 상승+ROAS 하락이 함께 나타나는지로 판단하고 동반 여부를 명시",\n'
    '  "improvements": "실제 개선 카피 문구 제안 불릿",\n'
    '  "remake": "재활용 시 어떤 구간을 유지/교체할지 리메이크 방향 불릿",\n'
    '  "next_tests": "다음 테스트 소재 아이디어 불릿",\n'
    '  "producer_memo": "제작 담당자에게 전달할 기획 메모 — 이 소재가 왜 효율이 좋은지 핵심 성공 요소, '
    '타깃이 반응한 포인트, 다음 제작에서 살릴 방향을 바로 실행 가능하게 3~4줄(불릿)"\n'
    "}}\n\n"
    "[이 소재]\n{ctx}\n\n[비교 평균]\n{avg}\n\n[같은 매체 위너 소재]\n{peers}\n\n"
    "[영상 스크립트/대본(자동 추출 — 없으면 카피·썸네일로 추론)]\n{script}"
)


def _gemini_json(prompt: str) -> dict:
    import time

    import requests
    from services.gemini_log import log_call
    key = config.GEMINI_API_KEY
    model = config.GEMINI_MODEL or "gemini-2.5-flash"
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    body = {"contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.5, "maxOutputTokens": 3200,
                                 "responseMimeType": "application/json"}}
    last = None
    for attempt in range(3):
        r = requests.post(url, params={"key": key}, json=body, timeout=90)
        if r.status_code in (429, 500, 503) and attempt < 2:
            last = r
            time.sleep(1.5 * (attempt + 1))
            continue
        try:
            r.raise_for_status()
            txt = r.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
        except Exception as e:  # noqa: BLE001
            log_call("ai_insight._gemini_json", len(prompt), ok=False, code=str(getattr(r, "status_code", e)))
            raise
        if txt.startswith("```"):
            txt = txt.strip("`")
            txt = txt[4:] if txt[:4].lower() == "json" else txt
        log_call("ai_insight._gemini_json", len(prompt), ok=True)
        return json.loads(txt)
    if last is not None:
        log_call("ai_insight._gemini_json", len(prompt), ok=False, code=str(last.status_code))
        last.raise_for_status()
    return {}


def _rule_based(r: dict, av: dict | None, peers: list | None, script: str) -> dict:
    """키 없을 때 — ROAS 기준 + 성과 조합으로 11개 섹션을 직접 구성."""
    av = av or {}
    ctr = _num(r, "ctr"); cpc = _num(r, "cpc"); cpm = _num(r, "cpm")
    roas = _num(r, "roas"); spend = _num(r, "spend"); rev = _num(r, "revenue")
    conv = int(_num(r, "conversions"))
    a_ctr = av.get("ctr", 0) or 0; a_cpc = av.get("cpc", 0) or 0
    a_cpm = av.get("cpm", 0) or 0; a_roas = av.get("roas", 0) or 0
    hi_ctr = bool(a_ctr and ctr >= a_ctr)
    lo_ctr = bool(a_ctr and ctr < a_ctr)
    label, _ = verdict_label(r, av)

    vs = []
    if a_ctr:
        vs.append(f"- CTR {ctr}% vs 평균 {a_ctr:.2f}% → {'평균 이상(첫 화면·썸네일·문제 제기 카피가 클릭 유도에 기여)' if hi_ctr else '평균 미만(첫 화면 후킹이 약함)'}")
    if a_roas:
        vs.append(f"- ROAS {roas:.0f}% vs 평균 {a_roas:.0f}% · 내부 기준 100% 미만은 비효율")
    if a_cpc:
        vs.append(f"- CPC {int(cpc):,}원 vs 평균 {int(a_cpc):,}원 → {'클릭 유도 효율 양호' if a_cpc and cpc<=a_cpc else '클릭 단가 비효율'}")
    if a_cpm:
        tag = "노출 경쟁 심화 또는 소재 피로 가능" if cpm > a_cpm else "노출 효율 양호"
        vs.append(f"- CPM {int(cpm):,}원 vs 평균 {int(a_cpm):,}원 → {tag}")
    # 조합 해석
    if hi_ctr and roas >= 150:
        combo = "- 조합: CTR 높음 + ROAS 양호 → 후킹과 전환 설득이 모두 작동"
    elif hi_ctr and roas < 100:
        combo = "- 조합: CTR 높음 + ROAS 100% 미만 → 클릭은 만들지만 클릭 후 구매 설득(후기·수치·혜택·CTA)이 약함"
    elif lo_ctr and roas >= 150:
        combo = "- 조합: CTR 낮음 + ROAS 양호 → 클릭 규모는 작지만 구매 의도 높은 타깃엔 설득력 있음"
    elif lo_ctr and roas < 100:
        combo = "- 조합: CTR 낮음 + ROAS 낮음 → 후킹·전환 모두 약함, OFF 우선 검토"
    else:
        combo = "- 조합: 지표가 평균 부근 — 약한 축(후킹 또는 전환)을 우선 보강"
    vs.append(combo)
    if a_cpm and cpm > a_cpm and lo_ctr:
        vs.append("- CPM 높음 + CTR 낮음 → 노출 경쟁 대비 반응이 약해 비효율 가능성 높음")
    if spend >= 200000 and roas < 100:
        vs.append(f"- 광고비 {int(spend):,}원 대비 ROAS {roas:.0f}% → 예산 낭비 가능, 즉시 점검 필요")

    if hi_ctr:
        hook = (f"- CTR {ctr}%가 평균({a_ctr:.2f}%) 이상 → 첫 화면/썸네일/문제 제기 카피가 클릭 유도에 기여한 것으로 보입니다.\n"
                "- 클릭을 만든 후킹 요소(첫 자막·비주얼)는 리메이크 시에도 유지 권장")
    else:
        hook = (f"- CTR {ctr}%가 평균({a_ctr:.2f}%) 미만 → 첫 1~3초 후킹(썸네일·오프닝 자막)이 약함\n"
                "- 통념을 깨는 질문형 훅 또는 강한 비주얼로 첫 장면 교체 필요")

    has_script = bool(script.strip())
    if has_script:
        script_analysis = ("- 제공된 스크립트 기준 [문제 제기 → 공감 → 해결책 → 제품 제안 → CTA] 구조가 순서대로 잡혔는지 점검\n"
                           "- 제품 등장이 늦거나 CTA가 약하면 해당 지점에서 전환이 끊깁니다")
        segment = ("- 0~3초(첫 후킹): 첫 자막·장면이 시선을 잡는지\n"
                   "- 3~7초(문제 제기·공감): 타깃의 고민을 건드리는지\n"
                   "- 7~15초(제품·해결): 제품이 해결책으로 자연스럽게 등장하는지\n"
                   "- 15초+ (구매 설득·CTA): 후기·수치·혜택·가격으로 신뢰를 주고 CTA로 이어지는지\n"
                   "- 위 구간 중 메시지가 비거나 늘어지는 곳이 이탈 구간입니다")
    else:
        script_analysis = ("- 영상 스크립트 자동 추출 실패 또는 이미지 소재 → 카피·썸네일 기반 추론입니다.\n"
                           "- 영상 소재면 'AI 분석 실행' 시 자동으로 자막·장면을 인식해 구간별로 분석합니다")
        segment = ("- (스크립트 미확보) 구간별 분석은 영상 자동 인식 또는 대본 입력 시 제공됩니다.\n"
                   "- 0~3초 후킹 / 3~7초 문제 제기 / 7~15초 제품 / 15초+ CTA 기준으로 점검 권장")

    if roas >= 100:
        conversion = f"- ROAS {roas:.0f}%로 본전 이상 — 후기·수치·혜택 제시가 일부 작동. 더 끌어올리려면 가격/혜택 명확화"
    else:
        conversion = (f"- ROAS {roas:.0f}%로 100% 미만 → 클릭 후 구매 설득이 부족\n"
                      "- 제품 신뢰 요소(후기·수치·전후 비교·가격/혜택 제시)가 약해 전환까지 못 이어진 가능성")

    fat = []
    if a_cpm and cpm > a_cpm * 1.15:
        fat.append(f"- CPM {int(cpm):,}원이 평균 {int(a_cpm):,}원보다 높아 노출 확보 비용이 비싼 상태입니다.")
        fat.append("- 다만 CPM 상승만으로 소재 피로도를 단정할 수는 없습니다 — CTR 하락·CPC 상승·ROAS 하락이 함께 나타나는지 확인이 필요합니다.")
        _sig = []
        if a_ctr and ctr < a_ctr:
            _sig.append("CTR 평균 미만")
        if a_cpc and cpc > a_cpc:
            _sig.append("CPC 평균 초과")
        if a_roas and roas < a_roas:
            _sig.append("ROAS 평균 미만")
        if _sig:
            fat.append(f"- 현재 동반 신호: {', '.join(_sig)} → 피로도 가능성이 있어 일별 추세(빈도·CTR 하락) 확인 권장")
        else:
            fat.append("- 현재 CTR·CPC·ROAS는 함께 악화되지 않아, 피로도보다는 일시적 노출 경쟁일 가능성이 큽니다")
    elif r.get("is_fatigue"):
        fat.append("- 전환은 있으나 ROAS가 평균 대비 낮음 — 일별 추세에서 빈도 상승·CTR 하락이 동반되는지 확인 필요")
    else:
        fat.append("- 현재 뚜렷한 피로 신호 없음(일별 스냅샷 누적 시 빈도·CTR 추세로 정밀 판단)")

    if roas < 100:
        improvements = ("- 중후반에 실제 후기/사용 수치/전후 비교 추가\n"
                        "- 가격·혜택·기간 한정 오퍼를 CTA 직전에 명확히 노출\n"
                        "- 개선 카피 예: '7일 써본 후기 → OO% 개선, 지금 첫 구매 OO% 할인'")
        remake = ("- 첫 후킹(잘 먹힌 첫 3초)은 유지\n"
                  "- 중후반부를 후기·수치·혜택·CTA 중심으로 재구성한 리메이크 제작")
    else:
        improvements = ("- 먹히는 후킹·오퍼 구조를 유지하며 카피 미세 변주\n"
                        "- 개선 카피 예: 핵심 혜택을 첫 5초 자막으로 한 번 더 강조")
        remake = "- 현 구조 유지, 후킹/오퍼만 바꾼 변주 소재로 라인업 확대"

    nxt = ["- 이 소재의 후킹을 유지하고 전환 구간만 바꾼 A/B"]
    if peers:
        p = peers[0]
        nxt.append(f"- 위너 '{p.get('creative_name','')}'(ROAS {_num(p,'roas'):.0f}%)의 전환 구조를 차용한 변주")
    nxt.append("- 동일 제품, 다른 후킹(질문형 / 실사용 후기 / 전후 비교) 3종 테스트")

    if label == "OFF 권장":
        summary = (f"ROAS {roas:.0f}%로 광고비 대비 매출이 부족해 위닝 소재로 보기 어렵습니다. "
                   f"CTR {ctr}%는 {'평균 이상이라 후킹은 일부 작동' if hi_ctr else '평균 미만이라 후킹도 약함'}하지만, "
                   "클릭 이후 구매 설득이 부족해 전환까지 이어지지 못했습니다. "
                   "현재 소재는 OFF 검토 대상입니다.")
    elif label == "수정 후 재테스트":
        summary = (f"CTR {ctr}%가 평균보다 높아 첫 화면·문제 제기 카피의 후킹은 작동했으나, "
                   f"ROAS {roas:.0f}%로 100% 미만이라 클릭 이후 구매 설득은 부족합니다. "
                   "후킹은 유지하고 전환 구조(후기·혜택·CTA)를 보강한 리메이크가 필요합니다.")
    elif label in ("위닝 소재", "유지 소재"):
        summary = (f"ROAS {roas:.0f}%로 내부 기준상 {label}입니다. "
                   f"CTR {ctr}%({'평균 이상' if hi_ctr else '평균 부근'})로 후킹도 받쳐줘 "
                   "예산 확장·변주 제작 단계입니다.")
    else:  # 개선 필요 / 피로도 의심
        _tt_ok = (r.get("platform") == "TikTok" and roas >= 70)
        summary = (f"ROAS {roas:.0f}%로 "
                   f"{'TikTok 기준(70%+)으로는 효율이 괜찮은 편이나 더 끌어올릴 여지가 있어' if _tt_ok else '효율을 더 끌어올릴 여지가 있어'}"
                   f" 개선이 필요합니다. CTR {ctr}% 기준 후킹은 {'작동' if hi_ctr else '약함'}, "
                   "전환 구간 보강이 핵심입니다.")

    return {
        "summary": summary,
        "perf_vs_avg": "\n".join(vs),
        "hook": hook,
        "script_analysis": script_analysis,
        "segment": segment,
        "conversion": conversion,
        "fatigue": "\n".join(fat),
        "improvements": improvements,
        "remake": remake,
        "next_tests": "\n".join(nxt),
        "producer_memo": (
            f"- 분류: {verdict_label(r, av)[0]} (오늘 ROAS {roas:.0f}%)\n"
            + ("- 성공 요소: 후킹(CTR)이 평균 이상 — 첫 화면·썸네일이 클릭을 유도\n" if hi_ctr
               else "- 약점: 후킹이 약함 — 첫 화면부터 개선 필요\n")
            + "- 다음 제작: 효율 요소는 유지하고 약한 구간을 보강한 응용 소재\n"
            + "- 제작자 전달: 위 '응용 소재 방향'·'개선 카피' 참고"),
        "_source": "rule",
    }


def analyze(r: dict, peers: list | None = None, av: dict | None = None,
            script: str = "", av_meta: dict | None = None) -> dict:
    """소재 1건 → 실무형 리포트(dict, FIELDS 섹션 + verdict). 키 있으면 Gemini, 없거나 실패 시 규칙 기반.
    peers=같은 매체 위너, av=평균 지표(dict), script=자동추출/입력 스크립트, av_meta=평균 출처 표시용."""
    rep = None
    if enabled():
        try:
            d = _gemini_json(_PROMPT.format(
                ctx=_ctx(r), avg=_avg_block(av_meta or _avg_meta_from(av)),
                peers=_peers_ctx(peers or []),
                script=(script.strip() or "(스크립트 미제공 — 카피·썸네일로 영상 구조를 추론하라)")))
            if isinstance(d, dict) and d.get("summary"):
                d["_source"] = "gemini"
                rep = d
        except Exception:  # noqa: BLE001
            rep = None
    if rep is None:
        rep = _rule_based(r, av, peers, script)
    lab, col = verdict_label(r, av)
    rep["verdict"] = lab
    rep["verdict_color"] = col
    rep["has_script"] = bool(script.strip())
    rep["av_meta"] = av_meta or _avg_meta_from(av)
    return rep


def _avg_meta_from(av: dict | None) -> dict:
    """av(dict)만 있을 때 기본 출처 메타 구성."""
    av = av or {}
    return {"source": "repurely 같은 매체 전체 소재 평균", "basis": "오늘 누적",
            "roas": av.get("roas", 0), "ctr": av.get("ctr", 0),
            "cpc": av.get("cpc", 0), "cpm": av.get("cpm", 0)}
