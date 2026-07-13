"""
광고 영상 스크립트 생성 — 버튼 클릭 시에만 실행.
우선순위: ① YouTube 자막 → ② Gemini 영상 분석(YouTube) → ③ Gemini 추정(카피 기반).
결과는 ad_library_ads.script_* 에 저장되어 재호출 안 함('재생성' 시에만 다시).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
import services.youtube as YT  # noqa: E402

# 영상을 3초 구간으로 나눠 대사(STT)+화면문구(OCR)+장면요약을 한 번에 JSON으로 받는다.
PROMPT = """이 광고 영상을 처음부터 끝까지 3초 단위 구간으로 나눠 분석하고, JSON 배열로만 출력해줘.
설명 문장이나 ```코드블록``` 없이 순수 JSON 배열만 출력해.

각 원소는 다음 형식:
{"start":"M:SS","end":"M:SS","script":"그 구간에 들리는 음성 대사(없으면 빈 문자열)","visual_summary":"그 구간 화면에서 일어나는 일 한 줄","on_screen_text":"화면에 보이는 자막/텍스트(없으면 빈 문자열)"}

규칙:
- 영상 길이에 맞춰 0:00부터 끝까지 3초 간격으로 연속 구간을 만들 것
- script(음성 대사)와 on_screen_text(화면 텍스트)는 다를 수 있으니 각각 적을 것
- 한국어로, 영상에 실제로 있는 내용만. 없는 내용을 지어내지 말 것"""


def _ctx(ad: dict) -> str:
    return (f"\n\n[참고 광고 정보]\n브랜드: {ad.get('brand_name','')}\n"
            f"광고 카피:\n{ad.get('ad_copy','') or '(없음)'}\nCTA: {ad.get('cta','') or '-'}")


def _parse_segments(raw: str) -> str:
    """Gemini 응답에서 JSON 배열만 추출해 정규화된 JSON 문자열로. 실패 시 원문 반환."""
    import json
    import re
    if not raw:
        return ""
    txt = raw.strip()
    txt = re.sub(r"^```(?:json)?|```$", "", txt, flags=re.MULTILINE).strip()
    m = re.search(r"\[.*\]", txt, flags=re.DOTALL)
    if not m:
        return raw
    try:
        arr = json.loads(m.group(0))
        segs = [{"start": s.get("start", ""), "end": s.get("end", ""),
                 "script": s.get("script", ""), "visual_summary": s.get("visual_summary", ""),
                 "on_screen_text": s.get("on_screen_text", "")}
                for s in arr if isinstance(s, dict)]
        return json.dumps(segs, ensure_ascii=False) if segs else raw
    except Exception:  # noqa: BLE001
        return raw


def gemini_key() -> str:
    """호출 시점에 st.secrets→env에서 live 로딩(import 시점 캐시 X) → reboot 없이 secret 반영."""
    return config.secret("GEMINI_API_KEY")


def gemini_model() -> str:
    return config.secret("GEMINI_MODEL", "") or "gemini-2.5-flash"


def _gemini(parts: list, retries: int = 3, _fn: str = "script_gen._gemini", _ad: str = "") -> str:
    """Gemini 호출. 과부하(503)/한도(429) 대응:
    ① 토큰을 줄여(저해상도 미디어) TPM 압박·비용 완화 ② 점증 백오프 재시도
    ③ 그래도 과부하면 덜 붐비는 대체 모델로 폴백. 실패 시 '' (앱 멈춤 방지)."""
    import time

    import requests
    from services.gemini_log import log_call
    _plen = sum(len(str(p.get("text", ""))) for p in parts if isinstance(p, dict))
    _last_code = ""
    key = gemini_key()
    # 설정 모델 → 대체 모델 순서(2.5-flash 과부하 시 2.0-flash/flash-lite로)
    models = []
    for m in (gemini_model(), "gemini-2.0-flash", "gemini-2.5-flash-lite"):
        if m and m not in models:
            models.append(m)
    body = {
        "contents": [{"parts": parts}],
        "generationConfig": {
            "maxOutputTokens": 8192, "temperature": 0.2,
            "thinkingConfig": {"thinkingBudget": 0},   # thinking 끄기(빈 응답·비용 방지)
            "mediaResolution": "MEDIA_RESOLUTION_LOW",  # 영상 토큰 대폭 절감 → 한도·비용↓
        },
    }
    for model in models:
        url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
               f"{model}:generateContent?key={key}")
        for attempt in range(retries + 1):
            try:
                r = requests.post(url, json=body, timeout=180)
                j = r.json()
                if "error" in j:
                    code = j["error"].get("code")
                    _last_code = str(code)
                    if code in (503, 429, 500) and attempt < retries:
                        time.sleep(min(3 * (attempt + 1), 15))
                        continue
                    break   # 이 모델 포기 → 다음 대체 모델
                cand = (j.get("candidates") or [{}])[0]
                txt = " ".join(p.get("text", "") for p in cand.get("content", {}).get("parts", [])).strip()
                if txt:
                    log_call(_fn, _plen, ok=True, code="", ad_id=_ad, note=model)
                    return txt
                break   # 빈 응답 → 다음 모델
            except Exception as e:  # noqa: BLE001
                _last_code = type(e).__name__
                if attempt < retries:
                    time.sleep(2 * (attempt + 1))
                    continue
                break
    log_call(_fn, _plen, ok=False, code=_last_code, ad_id=_ad, note="all models failed")
    return ""


def _mp4_duration(content: bytes) -> float:
    """ffmpeg 없이 mp4 'mvhd' 박스에서 영상 길이(초) 추출. 실패 시 0."""
    try:
        i = content.find(b"mvhd")
        if i < 0:
            return 0.0
        import struct
        ver = content[i + 4]
        if ver == 1:
            timescale = struct.unpack(">I", content[i + 24:i + 28])[0]
            duration = struct.unpack(">Q", content[i + 28:i + 36])[0]
        else:
            timescale = struct.unpack(">I", content[i + 16:i + 20])[0]
            duration = struct.unpack(">I", content[i + 20:i + 24])[0]
        return duration / timescale if timescale else 0.0
    except Exception:  # noqa: BLE001
        return 0.0


MAX_DURATION_SEC = 120   # 2분 이하 영상까지 분석 허용(과거 60초 컷 → 사용자 요청으로 120초)


def _gemini_video_file(url: str, ad: dict) -> tuple:
    """Meta(fbcdn) 등 mp4를 다운로드 → 해시 캐시 확인 → 길이 게이트 → Gemini 분석.
    반환 (status, text, source, error). status ∈ completed/video_unavailable/video_too_long."""
    import base64
    import hashlib

    import database
    import requests
    try:
        r = requests.get(url, timeout=90, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code != 200 or not r.content:
            return "video_unavailable", "", "", f"영상 다운로드 실패(HTTP {r.status_code}, 만료 가능)"
        content = r.content
    except Exception as e:  # noqa: BLE001
        return "video_unavailable", "", "", f"영상 다운로드 오류: {str(e)[:80]}"

    # 중복 제거: 같은 영상(내용 해시)이면 캐시 재사용 → Gemini 재호출 안 함
    vhash = "vid:" + hashlib.sha1(content).hexdigest()
    cached = database.get_script_cache(vhash)
    if cached:
        return "completed", cached["text"], cached["source"], ""

    if len(content) > 20 * 1024 * 1024:
        return ("video_unavailable", "", "",
                f"영상 용량 {len(content)//(1024*1024)}MB — 20MB 초과로 분석 불가"
                "(긴/고화질 영상). 필요 시 업로드 API 방식으로 확장 가능")
    dur = _mp4_duration(content)
    if dur and dur > MAX_DURATION_SEC:
        return "video_too_long", "", "", f"{int(dur)}초 영상 — {MAX_DURATION_SEC}초(2분) 초과로 분석 제한"

    b64 = base64.b64encode(content).decode()
    g = _gemini([{"inline_data": {"mime_type": "video/mp4", "data": b64}},
                 {"text": PROMPT + _ctx(ad)}], _fn="script_gen.video_file", _ad=str(ad.get("id") or ""))
    seg = _parse_segments(g)
    if seg:
        database.put_script_cache(vhash, seg, "gemini_video")
        return "completed", seg, "gemini_video", ""
    return "video_unavailable", "", "", "Gemini 영상 분석 실패(과부하/응답 없음) — 잠시 후 재시도"


def transcript_only(ad: dict) -> dict | None:
    """무료(YouTube 자막)만 시도 — Gemini 호출 없이. 자막 없으면 None.
    상세 진입 시 자동 호출해 Gemini API 사용을 최소화한다.
    ※ 매칭된 소셜 영상(social_source_url)이 아니라 '이 광고 자체 영상'만 본다."""
    vid = YT.extract_video_id(ad.get("video_url") or "")
    if not vid:
        return None
    segs = YT.fetch_transcript_segments(vid)
    if segs:
        return {"text": "[영상 스크립트]\n" + YT.format_segments(segs),
                "source": "youtube_transcript", "status": "completed",
                "error": "", "input_type": "transcript"}
    tr = YT.fetch_transcript(vid)
    if tr:
        return {"text": tr, "source": "youtube_transcript", "status": "completed",
                "error": "", "input_type": "transcript"}
    return None


def generate(ad: dict) -> dict:
    """반환 {text, source, status, error, input_type}. Gemini 사용(버튼 클릭 시).
    ※ 메타 등은 '이 광고가 재생하는 자체 영상'을 분석한다(매칭 소셜 영상 사용 안 함)."""
    vid = YT.extract_video_id(ad.get("video_url") or "")

    # ① YouTube 자막 (타임스탬프 구간 — 스니핏 스타일, 무료)
    free = transcript_only(ad)
    if free:
        return free

    # 영상(mp4/YouTube)이 실제로 있어야 분석 — 썸네일만 있는 소재는 thumbnail_only
    vurl = ad.get("video_url") or ad.get("media_url") or ""
    has_video = bool(vid) or vurl.startswith("http")
    if not has_video:
        return {"text": "", "source": "", "status": "thumbnail_only",
                "error": "영상 없음(이미지 소재) — '썸네일 분석'을 사용하세요", "input_type": "none"}

    if not gemini_key():
        return {"text": "", "source": "manual", "status": "failed",
                "error": "GEMINI_API_KEY 미설정", "input_type": "none"}

    # ② Gemini 영상 분석(YouTube URL 직접) → 구간 JSON (video_id 캐시)
    if vid:
        import database
        cached = database.get_script_cache("yt:" + vid)
        if cached:
            return {"text": cached["text"], "source": cached["source"], "status": "completed",
                    "error": "", "input_type": "video_url"}
        g = _gemini([{"file_data": {"file_uri": YT.watch_url(vid)}}, {"text": PROMPT + _ctx(ad)}],
                    _fn="script_gen.youtube_uri", _ad=str(ad.get("id") or ""))
        seg = _parse_segments(g)
        if seg:
            database.put_script_cache("yt:" + vid, seg, "gemini_video")
            return {"text": seg, "source": "gemini_video", "status": "completed",
                    "error": "", "input_type": "video_url"}
        return {"text": "", "source": "", "status": "video_unavailable",
                "error": "Gemini 응답 없음(과부하) — 잠시 후 재시도", "input_type": "none"}

    # ③ Meta/IG 등 mp4 다운로드 → 캐시확인 → 길이게이트 → Gemini Vision(STT+화면문구+장면, 1콜)
    status, text, source, err = _gemini_video_file(vurl, ad)
    return {"text": text, "source": source, "status": status, "error": err,
            "input_type": "video_file"}


THUMB_PROMPT = """이미지 광고 소재(썸네일)를 보고 아래 JSON 객체 하나만 출력해줘. 설명/코드블록 없이 JSON만.
{"thumbnail_text":"화면에 보이는 문구(없으면 \\"\\")","visual_summary":"썸네일 장면 요약 한 줄",
"main_subject":"인물/제품/피부/패키지 등 핵심 피사체","hook_type":"문제제기/전후비교/후기/혜택/정보전달 중 하나",
"ad_angle":"이 소재의 소구 포인트","script_available":false,"reason":"thumbnail_only"}
한국어로, 이미지에 실제로 보이는 것만. 추측 최소화."""


def _thumb_bytes(ad: dict) -> tuple:
    """광고 썸네일 이미지 바이트 확보(로컬 파일 우선, 없으면 http). 반환 (bytes, mime)."""
    import requests
    from pathlib import Path
    src = ad.get("local_thumbnail_path") or ad.get("thumbnail_url") or ad.get("preview_url") or ""
    if not src:
        return b"", ""
    try:
        if src.startswith("http"):
            r = requests.get(src, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
            if r.status_code == 200 and r.content:
                return r.content, (r.headers.get("content-type") or "image/jpeg").split(";")[0]
        else:
            p = src[4:] if src.startswith("app/") else src   # 'app/static/..' → 'static/..'
            fp = config.ROOT / p
            if fp.exists():
                mime = "image/png" if fp.suffix.lower() == ".png" else "image/jpeg"
                return fp.read_bytes(), mime
    except Exception:  # noqa: BLE001
        pass
    return b"", ""


def analyze_thumbnail(ad: dict) -> dict:
    """이미지 소재 썸네일을 Gemini Vision으로 분석 → 후킹/소구 등 JSON 객체. (버튼 클릭 시에만)"""
    import base64
    import hashlib

    import database
    if not gemini_key():
        return {"text": "", "source": "", "status": "thumbnail_only",
                "error": "GEMINI_API_KEY 미설정", "input_type": "none"}
    content, mime = _thumb_bytes(ad)
    if not content:
        return {"text": "", "source": "", "status": "thumbnail_only",
                "error": "썸네일 이미지를 불러올 수 없습니다", "input_type": "none"}
    ihash = "img:" + hashlib.sha1(content).hexdigest()
    cached = database.get_script_cache(ihash)
    if cached:
        return {"text": cached["text"], "source": "gemini_thumbnail", "status": "thumbnail_only",
                "error": "", "input_type": "thumbnail"}
    b64 = base64.b64encode(content).decode()
    g = _gemini([{"inline_data": {"mime_type": mime or "image/jpeg", "data": b64}},
                 {"text": THUMB_PROMPT + _ctx(ad)}], _fn="script_gen.thumbnail", _ad=str(ad.get("id") or ""))
    obj = _parse_thumb(g)
    if obj:
        database.put_script_cache(ihash, obj, "gemini_thumbnail")
        return {"text": obj, "source": "gemini_thumbnail", "status": "thumbnail_only",
                "error": "", "input_type": "thumbnail"}
    return {"text": "", "source": "", "status": "thumbnail_only",
            "error": "Gemini 썸네일 분석 실패(과부하) — 잠시 후 재시도", "input_type": "none"}


def _parse_thumb(raw: str) -> str:
    """Gemini 응답에서 JSON 객체만 추출해 정규화. 실패 시 ''."""
    import json
    import re
    if not raw:
        return ""
    txt = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.MULTILINE).strip()
    m = re.search(r"\{.*\}", txt, flags=re.DOTALL)
    if not m:
        return ""
    try:
        o = json.loads(m.group(0))
        return json.dumps({
            "thumbnail_text": o.get("thumbnail_text", ""),
            "visual_summary": o.get("visual_summary", ""),
            "main_subject": o.get("main_subject", ""),
            "hook_type": o.get("hook_type", ""),
            "ad_angle": o.get("ad_angle", ""),
            "script_available": False, "reason": "thumbnail_only",
        }, ensure_ascii=False)
    except Exception:  # noqa: BLE001
        return ""
