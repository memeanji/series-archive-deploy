"""TikTok Marketing API — repurely 자사 광고 계정의 소재 + 영상/썸네일 + 지표 통합 조회.

ad/get(소재: ad_id·ad_name·video_id) + file/video/ad/info(영상 preview_url·썸네일 cover_url)
+ report/integrated/get(조회/완료율/시청시간/인게이지먼트 지표)를 합쳐
Insight TikTok 카드와 매칭할 소재 목록을 만든다.

토큰: config.TIKTOK_ACCESS_TOKEN · advertiser: config.TIKTOK_ADVERTISER_ID (읽기 전용 GET).
preview_url(영상)은 만료되므로 썸네일은 persist_thumbs로 static에 영구화.
"""
from __future__ import annotations

import json
import os

import config

BASE = config.TIKTOK_API_BASE
_ROOT = os.path.dirname(os.path.dirname(__file__))
_CACHE_PATH = os.path.join(_ROOT, "data", "repurely_tiktok_ads.json")

# report/integrated/get 에서 가져올 지표(probe로 응답 확인된 것만)
METRICS = [
    "spend", "impressions", "clicks", "ctr", "cpc", "cpm", "conversion",
    "video_play_actions", "video_watched_2s", "video_watched_6s",
    "video_views_p25", "video_views_p50", "video_views_p75", "video_views_p100",
    "average_video_play", "average_video_play_per_user",
    "likes", "comments", "shares", "follows", "profile_visits",
]


def _creds() -> tuple[str, str]:
    return config.TIKTOK_ACCESS_TOKEN, config.TIKTOK_ADVERTISER_ID


def enabled() -> bool:
    t, a = _creds()
    return bool(t and a)


def _get(path: str, params: dict) -> dict:
    import requests
    tok, _ = _creds()
    try:
        r = requests.get(f"{BASE}{path}", headers={"Access-Token": tok},
                         params=params, timeout=45)
        return r.json()
    except Exception:  # noqa: BLE001
        return {}


def _save_cache(ads: list[dict]) -> None:
    try:
        os.makedirs(os.path.dirname(_CACHE_PATH), exist_ok=True)
        with open(_CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(ads, f, ensure_ascii=False)
    except Exception:  # noqa: BLE001
        pass


def _load_cache() -> list[dict]:
    try:
        with open(_CACHE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return []


def _num(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _fetch_ads() -> list[dict]:
    """ad/get — 광고 소재 기본정보(ad_id, ad_name, video_id, image_ids)."""
    _, adv = _creds()
    out: list[dict] = []
    page = 1
    while page <= 20:
        j = _get("/ad/get/", {
            "advertiser_id": adv, "page": page, "page_size": 100,
            "fields": json.dumps(["ad_id", "ad_name", "campaign_id", "campaign_name",
                                  "adgroup_id", "adgroup_name", "video_id", "image_ids",
                                  "operation_status"])})
        if j.get("code") != 0:
            break
        data = j.get("data", {}) or {}
        out += data.get("list", []) or []
        if page >= (data.get("page_info", {}) or {}).get("total_page", 1):
            break
        page += 1
    return out


def _fetch_report() -> dict:
    """report/integrated/get — {ad_id: {metric: value}} (최근 30일 누적)."""
    import datetime
    _, adv = _creds()
    end = datetime.date.today()
    start = end - datetime.timedelta(days=30)
    out: dict = {}
    page = 1
    while page <= 20:
        j = _get("/report/integrated/get/", {
            "advertiser_id": adv, "report_type": "BASIC", "data_level": "AUCTION_AD",
            "dimensions": json.dumps(["ad_id"]), "metrics": json.dumps(METRICS),
            "start_date": str(start), "end_date": str(end), "page": page, "page_size": 100})
        if j.get("code") != 0:
            break
        data = j.get("data", {}) or {}
        for row in data.get("list", []) or []:
            aid = (row.get("dimensions", {}) or {}).get("ad_id")
            if aid:
                out[str(aid)] = row.get("metrics", {}) or {}
        if page >= (data.get("page_info", {}) or {}).get("total_page", 1):
            break
        page += 1
    return out


def _fetch_videos(video_ids: list[str]) -> dict:
    """file/video/ad/info — {video_id: {video_cover_url, preview_url, duration}}."""
    _, adv = _creds()
    out: dict = {}
    uniq = [v for v in dict.fromkeys(video_ids) if v]
    for i in range(0, len(uniq), 20):
        chunk = uniq[i:i + 20]
        j = _get("/file/video/ad/info/", {
            "advertiser_id": adv, "video_ids": json.dumps(chunk),
            "fields": json.dumps(["video_id", "video_cover_url", "poster_url",
                                  "preview_url", "duration", "width", "height"])})
        if j.get("code") != 0:
            continue
        for v in j.get("data", {}).get("list", []) or []:
            out[v.get("video_id")] = v
    return out


def load_ads() -> list[dict]:
    """소재+영상+지표 통합 → [{ad_id, ad_name, video_id, thumbnail_url, video_url, metrics:{...}}].
    성공 시 캐시 저장. 토큰 없음/실패 시 마지막 캐시로 폴백(클라우드 등)."""
    if not enabled():
        return _load_cache()
    ads = _fetch_ads()
    if not ads:
        return _load_cache()
    report = _fetch_report()
    vinfo = _fetch_videos([a.get("video_id") for a in ads if a.get("video_id")])
    out: list[dict] = []
    prev = {a.get("ad_name"): a.get("thumb_local") for a in _load_cache() if a.get("thumb_local")}
    for a in ads:
        aid = str(a.get("ad_id") or "")
        vid = a.get("video_id") or ""
        m = report.get(aid, {})
        vi = vinfo.get(vid, {})
        name = (a.get("ad_name") or "").strip()
        row = {
            "ad_id": aid, "ad_name": name, "video_id": vid,
            "campaign_name": a.get("campaign_name", ""), "adgroup_name": a.get("adgroup_name", ""),
            "thumbnail_url": vi.get("video_cover_url") or vi.get("poster_url") or "",
            "video_url": vi.get("preview_url") or "",
            "duration": _num(vi.get("duration")),
            "status": a.get("operation_status", ""),
            "metrics": {k: _num(m.get(k)) for k in METRICS},
        }
        if prev.get(name):                 # 영구화된 로컬 썸네일 보존
            row["thumb_local"] = prev[name]
        out.append(row)
    _save_cache(out)
    return out


def index_by_name(ads: list[dict]) -> dict:
    """매칭 인덱스 — ad_name / video_id / ad_id(소문자) 모두 키로. 우선순위 매칭에 사용."""
    idx: dict = {}
    for a in ads:
        for key in (a.get("ad_name"), a.get("video_id"), a.get("ad_id")):
            k = str(key or "").strip().lower()
            if k:
                idx.setdefault(k, a)
    return idx


def _safe_name(name: str) -> str:
    s = "".join(c for c in (name or "") if c.isalnum() or c in "_-")
    if s:
        return s
    import hashlib
    return hashlib.md5((name or "").encode()).hexdigest()[:14]


def persist_thumbs(ads: list[dict]) -> int:
    """TikTok 썸네일(video_cover_url, 서명·만료)을 static/thumbnails/rep_tt_*.png로 영구화.
    잡에서만 호출(렌더 금지). 각 ad에 thumb_local 채우고 캐시 갱신. 저장 건수 반환."""
    import requests
    saved = 0
    for a in ads:
        url = (a.get("thumbnail_url") or "").strip()
        name = (a.get("ad_name") or "").strip()
        if not url.startswith("http") or not name:
            continue
        rel = f"static/thumbnails/rep_tt_{_safe_name(name)}.png"
        fp = os.path.join(_ROOT, rel)
        a["thumb_local"] = "app/" + rel
        if os.path.exists(fp) and os.path.getsize(fp) > 0:
            continue
        try:
            r = requests.get(url, timeout=25)
            if r.status_code == 200 and r.content:
                os.makedirs(os.path.dirname(fp), exist_ok=True)
                with open(fp, "wb") as f:
                    f.write(r.content)
                saved += 1
        except Exception:  # noqa: BLE001
            pass
    _save_cache(ads)
    return saved
