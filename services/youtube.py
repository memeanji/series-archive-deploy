"""
YouTube 유틸 — video_id 추출 + YouTube Data API(videos.list).
YOUTUBE_API_KEY 는 .env(config) 또는 .streamlit/secrets.toml 에서 읽는다.
키가 없으면 is_enabled()=False → 등록 기능 비활성.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402

_ID = r"([A-Za-z0-9_-]{11})"
_PATTERNS = [rf"[?&]v={_ID}", rf"youtu\.be/{_ID}", rf"shorts/{_ID}", rf"embed/{_ID}", rf"live/{_ID}"]


def extract_video_id(url: str) -> str | None:
    url = (url or "").strip()
    for p in _PATTERNS:
        m = re.search(p, url)
        if m:
            return m.group(1)
    if re.fullmatch(_ID, url):
        return url
    return None


def embed_url(video_id: str) -> str:
    return f"https://www.youtube.com/embed/{video_id}"


def watch_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


def thumb_url(video_id: str) -> str:
    """API 없이도 안정적인 YouTube 썸네일(httpsで직접). fallback UI용."""
    return f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg"


def embeddable(video_id: str, api_key: str = "") -> bool | None:
    """status.embeddable 사전 조회. True=앱내재생가능 / False=외부재생필요 / None=미확인(키없음·실패)."""
    import requests
    key = api_key or get_api_key()
    if not key or not video_id:
        return None
    try:
        r = requests.get("https://www.googleapis.com/youtube/v3/videos",
                         params={"part": "status", "id": video_id, "key": key}, timeout=15)
        items = r.json().get("items", [])
    except Exception:  # noqa: BLE001
        return None
    if not items:
        return None
    return bool(items[0].get("status", {}).get("embeddable", False))


def get_api_key() -> str:
    if config.YOUTUBE_API_KEY:
        return config.YOUTUBE_API_KEY
    try:
        import streamlit as st
        return str(st.secrets.get("YOUTUBE_API_KEY", "")).strip()
    except Exception:  # noqa: BLE001
        return ""


def is_enabled() -> bool:
    return bool(get_api_key())


def gemini_transcript(video_id: str) -> str:
    """자막이 없을 때 Gemini 멀티모달로 영상 대본을 생성(YouTube URL 직접 입력).
    GEMINI_API_KEY 없으면 ''."""
    if not video_id or not config.GEMINI_API_KEY:
        return ""
    import requests
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"{config.GEMINI_MODEL}:generateContent?key={config.GEMINI_API_KEY}")
    body = {"contents": [{"parts": [
        {"file_data": {"file_uri": watch_url(video_id)}},
        {"text": "이 영상의 음성을 한국어 대본으로 전사해줘. 해설·요약 없이 말한 내용만 자연스럽게."},
    ]}]}
    from services.gemini_log import log_call
    try:
        r = requests.post(url, json=body, timeout=120)
        j = r.json()
        if "error" in j:
            log_call("youtube.gemini_transcript", 0, ok=False,
                     code=str(j["error"].get("code")), ad_id=video_id)
            return ""
        parts = j["candidates"][0]["content"]["parts"]
        log_call("youtube.gemini_transcript", 0, ok=True, ad_id=video_id)
        return " ".join(p.get("text", "") for p in parts).strip()
    except Exception as e:  # noqa: BLE001
        log_call("youtube.gemini_transcript", 0, ok=False, code=type(e).__name__, ad_id=video_id)
        return ""


def fetch_transcript(video_id: str, languages=("ko", "en")) -> str:
    """YouTube 자막(스크립트) 추출. 자막 없으면 ''. API 키 불필요."""
    if not video_id:
        return ""
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        api = YouTubeTranscriptApi()
        fetched = api.fetch(video_id, languages=list(languages) + ["a.ko", "a.en"])
        return " ".join(s.text for s in fetched).strip()
    except Exception:  # noqa: BLE001  (TranscriptsDisabled/NoTranscript 등)
        return ""


def fetch_transcript_segments(video_id: str, languages=("ko", "en")) -> list[dict]:
    """타임스탬프가 있는 자막 구간 목록 [{start, dur, text}]. 자막 없으면 []."""
    if not video_id:
        return []
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        api = YouTubeTranscriptApi()
        fetched = api.fetch(video_id, languages=list(languages) + ["a.ko", "a.en"])
        return [{"start": float(s.start), "dur": float(s.duration), "text": (s.text or "").strip()}
                for s in fetched if (s.text or "").strip()]
    except Exception:  # noqa: BLE001
        return []


def _fmt_ts(sec: float) -> str:
    m, s = divmod(int(sec), 60)
    return f"{m:02d}:{s:02d}"


def format_segments(segs: list[dict], min_chunk: float = 4.0) -> str:
    """짧은 자막 조각들을 ~min_chunk초 단위로 묶어 'MM:SS–MM:SS  대사' 줄로 포맷(스니핏 스타일)."""
    if not segs:
        return ""
    lines, buf, b_start = [], [], None
    for sg in segs:
        if b_start is None:
            b_start = sg["start"]
        buf.append(sg["text"])
        b_end = sg["start"] + sg["dur"]
        txt = " ".join(buf).strip()
        # 4초 이상 모였거나 문장이 끝나면 한 구간으로 확정
        if (b_end - b_start) >= min_chunk or txt.endswith((".", "?", "!", "다", "요", "죠", "까")):
            lines.append(f"{_fmt_ts(b_start)}–{_fmt_ts(b_end)}  {txt}")
            buf, b_start = [], None
    if buf and b_start is not None:
        lines.append(f"{_fmt_ts(b_start)}–{_fmt_ts(segs[-1]['start'] + segs[-1]['dur'])}  "
                     f"{' '.join(buf).strip()}")
    return "\n".join(lines)


def search_video_ids(query: str, max_results: int = 5, api_key: str = "") -> list[str]:
    """search.list 로 키워드(브랜드명) 관련 영상 id 목록. quota 100유닛/호출."""
    import requests
    key = api_key or get_api_key()
    if not key or not query:
        return []
    try:
        r = requests.get("https://www.googleapis.com/youtube/v3/search",
                         params={"part": "snippet", "q": query, "type": "video",
                                 "maxResults": max_results, "regionCode": "KR",
                                 "relevanceLanguage": "ko", "key": key}, timeout=30)
        items = r.json().get("items", [])
    except Exception:  # noqa: BLE001
        return []
    return [it["id"]["videoId"] for it in items if it.get("id", {}).get("videoId")]


def fetch_videos(video_ids: list[str], api_key: str = "") -> list[dict]:
    """videos.list 배치 조회(최대 50개). 1유닛/호출."""
    key = api_key or get_api_key()
    ids = [v for v in video_ids if v][:50]
    if not key or not ids:
        return []
    import requests
    try:
        r = requests.get("https://www.googleapis.com/youtube/v3/videos",
                         params={"part": "snippet,statistics", "id": ",".join(ids), "key": key},
                         timeout=30)
        items = r.json().get("items", [])
    except Exception:  # noqa: BLE001
        return []
    out = []
    for it in items:
        d = _parse_item(it)
        if d:
            out.append(d)
    return out


def _parse_item(it: dict) -> dict | None:
    vid = it.get("id")
    if isinstance(vid, dict):
        vid = vid.get("videoId")
    if not vid:
        return None
    sn = it.get("snippet", {})
    stt = it.get("statistics", {})
    thumbs = sn.get("thumbnails", {})
    thumb = (thumbs.get("high") or thumbs.get("medium") or thumbs.get("default") or {}).get("url", "")
    return {
        "id": f"yt_{vid}", "video_id": vid, "platform": "youtube",
        "video_url": watch_url(vid), "embed_url": embed_url(vid),
        "thumbnail_url": thumb, "title": sn.get("title", ""),
        "channel_title": sn.get("channelTitle", ""),
        "caption": sn.get("description", "")[:1000],
        "posted_at": (sn.get("publishedAt", "") or "")[:10], "source_url": watch_url(vid),
        "views": int(stt.get("viewCount") or 0), "likes": int(stt.get("likeCount") or 0),
        "comments": int(stt.get("commentCount") or 0), "shares": 0,
    }


def _iso_duration_sec(iso: str) -> int:
    """PT#H#M#S → 초. 파싱 실패 0."""
    m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso or "")
    if not m:
        return 0
    h, mi, s = (int(x) if x else 0 for x in m.groups())
    return h * 3600 + mi * 60 + s


def fetch_videos_detailed(video_ids: list[str], api_key: str = "") -> list[dict]:
    """videos.list (snippet,contentDetails,statistics) — 제목·설명·채널·길이·업로드일·자막여부·썸네일.
    YouTube 광고 매칭 후보 메타데이터 수집용. 1유닛/호출."""
    key = api_key or get_api_key()
    ids = [v for v in video_ids if v][:50]
    if not key or not ids:
        return []
    import requests
    try:
        r = requests.get("https://www.googleapis.com/youtube/v3/videos",
                         params={"part": "snippet,contentDetails,statistics",
                                 "id": ",".join(ids), "key": key}, timeout=30)
        items = r.json().get("items", [])
    except Exception:  # noqa: BLE001
        return []
    out = []
    for it in items:
        vid = it.get("id")
        sn = it.get("snippet", {})
        cd = it.get("contentDetails", {})
        stt = it.get("statistics", {})
        thumbs = sn.get("thumbnails", {})
        thumb = (thumbs.get("high") or thumbs.get("medium")
                 or thumbs.get("default") or {}).get("url", "")
        out.append({
            "video_id": vid, "id": f"yt_{vid}",
            "title": sn.get("title", ""), "description": (sn.get("description", "") or "")[:2000],
            "channel_title": sn.get("channelTitle", ""), "channel_id": sn.get("channelId", ""),
            "duration_sec": _iso_duration_sec(cd.get("duration", "")),
            "has_caption": 1 if cd.get("caption") == "true" else 0,
            "published_at": (sn.get("publishedAt", "") or "")[:10],
            "thumbnail_url": thumb, "source_url": watch_url(vid), "video_url": watch_url(vid),
            "views": int(stt.get("viewCount") or 0), "likes": int(stt.get("likeCount") or 0),
            "comments": int(stt.get("commentCount") or 0),
        })
    return out


def fetch_video(video_id: str, api_key: str = "") -> dict | None:
    """videos.list 로 단일 영상 메타+통계 조회. 실패시 None."""
    import requests
    key = api_key or get_api_key()
    if not key or not video_id:
        return None
    try:
        r = requests.get("https://www.googleapis.com/youtube/v3/videos",
                         params={"part": "snippet,statistics", "id": video_id, "key": key},
                         timeout=30)
        items = r.json().get("items", [])
    except Exception:  # noqa: BLE001
        return None
    if not items:
        return None
    it = items[0]
    sn = it.get("snippet", {})
    stt = it.get("statistics", {})
    thumbs = sn.get("thumbnails", {})
    thumb = (thumbs.get("high") or thumbs.get("medium") or thumbs.get("default") or {}).get("url", "")
    return {
        "id": f"yt_{video_id}", "video_id": video_id, "platform": "youtube",
        "video_url": watch_url(video_id), "embed_url": embed_url(video_id),
        "thumbnail_url": thumb, "title": sn.get("title", ""),
        "channel_title": sn.get("channelTitle", ""),
        "caption": sn.get("description", "")[:1000],
        "posted_at": (sn.get("publishedAt", "") or "")[:10],
        "source_url": watch_url(video_id),
        "views": int(stt.get("viewCount") or 0), "likes": int(stt.get("likeCount") or 0),
        "comments": int(stt.get("commentCount") or 0), "shares": 0,
    }
