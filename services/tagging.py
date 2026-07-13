"""
광고 카피/메타데이터 기반 자동 태깅 (1차: 키워드 룰 베이스).
나중에 LLM 분석으로 교체/보강할 수 있게 auto_tag() 한 함수로 진입점을 고정한다.
"""
from __future__ import annotations

from typing import Iterable

# ── 후킹 유형 태그: 태그명 → 매칭 키워드 ───────────────────
HOOK_RULES: dict[str, list[str]] = {
    "문제제기형": ["고민", "이거 때문에", "혹시", "아직도", "왜", "이런 적", "문제", "모르"],
    "후기형": ["후기", "리뷰", "써봤", "사용 후", "솔직", "내돈내산"],
    "전후비교형": ["전후", "before", "after", "비교", "변화", "2주", "한달"],
    "댓글반응형": ["댓글", "댓글 보고", "반응", "난리"],
    "전문가형": ["약사", "의사", "전문가", "박사", "원장", "성분"],
    "인터뷰형": ["인터뷰", "물어봤", "여쭤", "q&a"],
    "루틴형": ["루틴", "아침", "저녁", "출근 전", "매일"],
    "언박싱형": ["언박싱", "받자마자", "풀어봤", "개봉"],
    "할인/혜택형": ["할인", "혜택", "%", "쿠폰", "이벤트", "특가", "최저가"],
    "한정/긴급형": ["한정", "마감", "임박", "단 ", "오늘만", "지금", "마지막"],
    "논란/호기심형": ["논란", "충격", "실화", "진짜", "될까", "충격적"],
    "비교형": ["vs", "대신", "보다", "차이"],
    "공식몰유도형": ["공식몰", "공식", "official", "바로가기"],
    "품절/인기감형": ["품절", "인기", "베스트", "재입고", "완판"],
}

# ── 영상 구조 태그 ─────────────────────────────────────────
FORMAT_RULES: dict[str, list[str]] = {
    "UGC": ["후기", "내돈내산", "직접", "써봤", "일상"],
    "얼굴 클로즈업": ["얼굴", "표정", "클로즈업"],
    "제품 사용법": ["사용법", "이렇게", "방법", "쓰는 법"],
    "Before/After": ["before", "after", "전후", "변화"],
    "자막 중심": ["자막", "텍스트"],
    "리뷰 캡처": ["리뷰 캡처", "후기 캡처", "별점", "리뷰 모음"],
    "댓글 캡처": ["댓글 캡처", "댓글 보고", "댓글 반응"],
    "숏폼 릴스형": ["릴스", "숏폼", "shorts", "틱톡"],
    "인터뷰형": ["인터뷰", "q&a", "물어봤"],
    "홈쇼핑형": ["홈쇼핑", "방송", "구성", "사은품"],
    "언박싱형": ["언박싱", "받자마자", "개봉"],
}


def _match(text: str, keywords: Iterable[str]) -> bool:
    return any(kw.lower() in text for kw in keywords)


def _apply(rules: dict[str, list[str]], text: str) -> list[str]:
    return [tag for tag, kws in rules.items() if _match(text, kws)]


def auto_tag(ad: dict) -> tuple[list[str], list[str]]:
    """광고 dict → (hook_tags, format_tags). 카피+헤드라인+설명을 합쳐 매칭."""
    blob = " ".join(
        str(ad.get(k) or "") for k in ("ad_text", "headline", "description", "cta")
    ).lower()

    hooks = _apply(HOOK_RULES, blob)

    formats = _apply(FORMAT_RULES, blob)
    # 영상 소재면 숏폼 릴스형을 기본 후보로(틱톡/9:16 가정)
    if (ad.get("media_type") or "").lower() == "video" and ad.get("platform") == "tiktok":
        if "숏폼 릴스형" not in formats:
            formats.append("숏폼 릴스형")

    return hooks, formats
